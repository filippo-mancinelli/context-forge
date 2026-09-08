"""Previews shown to the human who approves an agent-proposed write.

SQL is EXPLAINed through the read-only executor, whose connection is closed
without committing: the plan is real, the statement is not applied. File writes
are summarised as sizes and sha256 hashes of the new and existing content.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

MAX_PROPOSAL_CONTENT_BYTES = 512 * 1024
PLAN_TEXT_CHARS = 2000
_EXPLAIN_MAX_ROWS = 5


class UnsupportedExplain(Exception):
    pass


def explain_statement(dialect: str, sql: str) -> str:
    """The engine-specific EXPLAIN wrapper for a DML statement."""
    if dialect == "postgresql":
        return f"EXPLAIN (FORMAT JSON) {sql}"
    if dialect in ("mysql", "mariadb"):
        return f"EXPLAIN FORMAT=JSON {sql}"
    raise UnsupportedExplain(f"EXPLAIN (FORMAT JSON) is not supported on '{dialect}'")


def extract_plan(rows: list[dict[str, Any]]) -> tuple[Optional[int], str]:
    """(estimated rows, truncated plan text) from a single-cell EXPLAIN result."""
    if not rows:
        return None, ""
    cell = next(iter(rows[0].values()), None)
    if isinstance(cell, str):
        try:
            parsed = json.loads(cell)
        except ValueError:
            return None, cell[:PLAN_TEXT_CHARS]
    else:
        parsed = cell
    text = json.dumps(parsed, default=str)[:PLAN_TEXT_CHARS]
    plan_rows = None
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
        node = parsed[0].get("Plan")
        if isinstance(node, dict) and isinstance(node.get("Plan Rows"), int):
            plan_rows = node["Plan Rows"]
    return plan_rows, text


async def sql_preview(org_id: int, project_id: int, connection: str, sql: str) -> dict[str, Any]:
    """EXPLAIN the (already validated) statement without applying it."""
    from .datasources import service

    preview: dict[str, Any] = {"statement": sql, "plan_rows": None, "plan_text": ""}
    try:
        record = await service.get_connection(org_id, project_id, connection, include_secret=True)
        engine = await service._resolve_engine(record)
        statement = explain_statement(engine.dialect.name, sql)
        _columns, rows, _truncated = await asyncio.wait_for(
            asyncio.to_thread(service._execute_readonly, engine, statement, _EXPLAIN_MAX_ROWS),
            timeout=service.QUERY_TIMEOUT_SECONDS * 2,
        )
        preview["plan_rows"], preview["plan_text"] = extract_plan(rows)
    except Exception as e:  # noqa: BLE001 - the proposal is stored even without a plan
        logger.info("EXPLAIN preview unavailable: %s", e)
        preview["explain_error"] = str(e)
    return preview


def file_preview(conn_params: dict[str, Any], path: str, content: str) -> dict[str, Any]:
    """Sizes and hashes for a proposed file write (blocking: SFTP)."""
    from .ssh_sources import client

    data = content.encode("utf-8")
    if len(data) > MAX_PROPOSAL_CONTENT_BYTES:
        raise ValueError(
            f"content exceeds {MAX_PROPOSAL_CONTENT_BYTES} bytes and cannot be proposed"
        )
    preview: dict[str, Any] = {
        "path": path,
        "content_bytes": len(data),
        "content_sha256": hashlib.sha256(data).hexdigest(),
        "existing_sha256": None,
        "existing_size": None,
        "is_new": True,
    }
    try:
        existing = client.read_file(conn_params, path)
    except FileNotFoundError:
        return preview
    except Exception as e:  # noqa: BLE001 - surfaced to the approver, not swallowed
        preview["is_new"] = False
        preview["read_error"] = str(e)
        return preview
    preview["is_new"] = False
    preview["existing_size"] = existing.get("size")
    if not existing.get("truncated"):
        preview["existing_sha256"] = hashlib.sha256(
            (existing.get("content") or "").encode("utf-8")
        ).hexdigest()
    return preview
