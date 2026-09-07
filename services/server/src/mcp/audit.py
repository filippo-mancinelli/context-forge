"""Audit trail of MCP tool calls: summarise, enqueue, batch-write."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from contextlib import asynccontextmanager
from typing import Any, Optional
from urllib.parse import urlsplit

from ..config import get_settings
from ..db import get_pool
from .context import get_current_org_id, get_current_principal, get_selected_project_id

logger = logging.getLogger(__name__)

MAX_QUEUE = 1000
BATCH_SIZE = 50
MAX_STRING = 200
MAX_ERROR = 500

_REDACT = re.compile(r"secret|password|token|key|content|sql", re.IGNORECASE)
# Chiavi il cui valore e' una URL: credenziali e query string non vanno loggate.
_URL_KEY = re.compile(r"url|uri|endpoint|href", re.IGNORECASE)
_URL_IN_TEXT = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)

_FIELDS = (
    "org_id",
    "project_id",
    "principal_kind",
    "principal_id",
    "principal",
    "tool",
    "permission",
    "outcome",
    "duration_ms",
    "error",
    "args_summary",
)

_INSERT_SQL = """
INSERT INTO mcp_tool_calls
    (org_id, project_id, principal_kind, principal_id, principal,
     tool, permission, outcome, duration_ms, error, args_summary)
SELECT * FROM unnest(
    $1::bigint[], $2::bigint[], $3::text[], $4::bigint[], $5::text[],
    $6::text[], $7::text[], $8::text[], $9::int[], $10::text[], $11::jsonb[]
)
"""

_queue: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUE)
_writer_task: Optional[asyncio.Task] = None
_last_drop_warning = 0.0


def scrub_url(text: str) -> str:
    """scheme://host[:port]/path: via userinfo, query e fragment. Non-URL: invariata."""
    try:
        parts = urlsplit(text)
    except ValueError:
        return text
    if not parts.scheme or not parts.netloc:
        return text
    try:
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return text
    if port:
        host = f"{host}:{port}"
    return f"{parts.scheme}://{host}{parts.path}"


def scrub_text(text: str) -> str:
    """Riscrive ogni URL contenuta in un testo libero passandola per scrub_url."""
    if not text:
        return text
    return _URL_IN_TEXT.sub(lambda m: scrub_url(m.group(0)), text)


def summarize_args(kwargs: dict) -> dict:
    """Argomenti loggabili: chiavi conservate, valori troncati o redatti."""
    out: dict[str, Any] = {}
    for key, value in (kwargs or {}).items():
        is_sql = key.lower() == "sql"
        if isinstance(value, dict):
            out[key] = f"<dict:{len(value)}>"
        elif isinstance(value, (list, tuple, set)):
            out[key] = f"<list:{len(value)}>"
        elif _REDACT.search(key) and not is_sql:
            out[key] = "<redacted>"
        elif isinstance(value, str) and _URL_KEY.search(key):
            out[key] = scrub_url(value)[:MAX_STRING]
        elif isinstance(value, str):
            out[key] = scrub_text(value)[:MAX_STRING]
        elif value is None or isinstance(value, (bool, int, float)):
            out[key] = value
        else:
            # Non-JSON-primitive (e.g. FastMCP's injected Context): name it
            # instead of keeping the raw object, which json.dumps can't serialise.
            out[key] = f"<{type(value).__name__}>"
    return out


def classify_outcome(exc: BaseException) -> str:
    """Esito dall'eccezione: prima il tipo, poi il messaggio per i chiamanti legacy."""
    from .permissions import PermissionDenied
    from .ratelimit import RateLimited

    if isinstance(exc, RateLimited):
        return "rate_limited"
    if isinstance(exc, PermissionDenied):
        return "denied"
    message = str(exc)
    if message.startswith("Rate limit exceeded"):
        return "rate_limited"
    # Solo il messaggio esatto del decoratore: un "Access denied" propagato
    # dal corpo di un tool resta un errore, non una decisione di permessi.
    if message.startswith("Access denied: tool requires permission"):
        return "denied"
    return "error"


def _increment_metric(tool: str, outcome: str) -> None:
    try:
        from ..metrics import mcp_tool_calls_total

        mcp_tool_calls_total.labels(tool=tool, outcome=outcome).inc()
    except Exception:
        logger.debug("MCP tool-call metric not incremented", exc_info=True)


def _warn_dropped() -> None:
    global _last_drop_warning
    now = time.monotonic()
    if now - _last_drop_warning >= 60:
        _last_drop_warning = now
        logger.warning("MCP audit queue full: dropping the oldest tool-call rows")


async def record_call(
    *,
    tool: str,
    permission: Optional[str],
    outcome: str,
    duration_ms: int,
    error: Optional[str] = None,
    args_summary: Optional[dict] = None,
) -> None:
    """Accoda una riga di audit; non tocca mai il database nel path del tool."""
    try:
        principal = get_current_principal()
        row = {
            "org_id": get_current_org_id(),
            "project_id": get_selected_project_id(),
            "principal_kind": principal.kind,
            "principal_id": principal.id,
            "principal": principal.label,
            "tool": tool,
            "permission": permission,
            "outcome": outcome,
            "duration_ms": int(duration_ms),
            "error": scrub_text(error)[:MAX_ERROR] if error else None,
            # default=str is a safety net: summarize_args should already have
            # reduced every value to a JSON primitive, but a bare dict/list
            # (e.g. from record_call called directly, bypassing summarize_args)
            # must not silently drop the whole row.
            "args_summary": (
                json.dumps(args_summary, default=str) if args_summary is not None else None
            ),
        }
        try:
            _queue.put_nowait(row)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                _queue.get_nowait()
            _warn_dropped()
            with contextlib.suppress(asyncio.QueueFull):
                _queue.put_nowait(row)
    except Exception:
        logger.debug("MCP audit row not recorded", exc_info=True)


class AuditedCall:
    """Esito in corso: il chiamante gli consegna il valore di ritorno del tool."""

    __slots__ = ("outcome", "error")

    def __init__(self) -> None:
        self.outcome = "ok"
        self.error: Optional[str] = None

    def set_result(self, result: Any) -> None:
        """Un tool che ritorna {"status": "error", ...} ha fallito, senza sollevare."""
        if isinstance(result, dict) and result.get("status") == "error":
            self.outcome = "error"
            self.error = scrub_text(str(result.get("error") or ""))[:MAX_ERROR]


@asynccontextmanager
async def audited(
    tool_name: str, permission: Optional[str] = None, args_summary: Optional[dict] = None
):
    """Misura una chiamata, ne classifica l'esito e la registra."""
    start = time.perf_counter()
    call = AuditedCall()
    try:
        yield call
    except BaseException as exc:
        call.outcome = classify_outcome(exc)
        call.error = str(exc)
        raise
    finally:
        duration_ms = int((time.perf_counter() - start) * 1000)
        _increment_metric(tool_name, call.outcome)
        await record_call(
            tool=tool_name,
            permission=permission,
            outcome=call.outcome,
            duration_ms=duration_ms,
            error=call.error,
            args_summary=args_summary,
        )


def _take_batch(limit: int = BATCH_SIZE) -> list[dict]:
    batch: list[dict] = []
    while len(batch) < limit:
        try:
            batch.append(_queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return batch


async def _write_batch(batch: list[dict]) -> int:
    columns = [[row[field] for row in batch] for field in _FIELDS]
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(_INSERT_SQL, *columns)
    except Exception:
        logger.warning("MCP audit batch not written (%d rows lost)", len(batch), exc_info=True)
        return 0
    return len(batch)


async def flush_once() -> int:
    """Scrive un batch. Ritorna le righe scritte (0 se la insert fallisce)."""
    batch = _take_batch()
    if not batch:
        return 0
    return await _write_batch(batch)


async def _writer_loop() -> None:
    while True:
        row = await _queue.get()
        # The blocking get() above already claimed one slot: cap the rest so
        # the batch never exceeds BATCH_SIZE rows.
        await _write_batch([row] + _take_batch(BATCH_SIZE - 1))


def start_audit_writer() -> None:
    global _writer_task
    if _writer_task is None or _writer_task.done():
        _writer_task = asyncio.create_task(_writer_loop())
        logger.info("MCP audit writer started")


async def stop_audit_writer() -> None:
    global _writer_task
    task, _writer_task = _writer_task, None
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await flush_once()


async def purge_old_calls() -> int:
    """Cancella l'audit più vecchio della retention configurata."""
    days = get_settings().mcp_audit_retention_days
    if not days or days <= 0:
        return 0
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM mcp_tool_calls "
                "WHERE created_at < NOW() - make_interval(days => $1)",
                int(days),
            )
    except Exception:
        logger.warning("MCP audit retention purge failed", exc_info=True)
        return 0
    try:
        return int(str(result).split()[-1])
    except (ValueError, IndexError):
        return 0
