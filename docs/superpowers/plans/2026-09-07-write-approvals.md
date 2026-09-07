# Human Approvals for Write Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A caller without `db-write` / `ssh-write` but with `context-write` no longer gets a flat denial from `db_execute` / `ssh_write_file`: the tool stores a *write request* with a preview and returns `pending_approval`, and an organization admin approves or rejects it from the UI, which then executes it under the approver's identity.

**Architecture:** A new table `write_requests` (migration `0004_write_requests`) plus a service `src/write_requests.py` holding the whole state machine (`pending → approved → executed | failed`, `rejected`, `expired`). Preview builders live in `src/write_previews.py`: SQL gets an `EXPLAIN` run through the existing read-only executor (its connection is closed without commit, so the transaction rolls back), files get sha256/size comparison through the SSH client. The two write tools keep their current direct-execution branch byte-for-byte and only take the proposal branch when the caller lacks the write permission; a new tool `write_request_status` polls the outcome. REST routes and a UI **Approvals** page give admins the human side, and a daily scheduler job expires overdue requests.

**Tech Stack:** Python 3.11 (local dev 3.14), asyncpg raw SQL, FastMCP, FastAPI, APScheduler, SQLAlchemy (external datasources), paramiko/SFTP (SSH sources), pytest; React 18 + TypeScript + Vite + Tailwind, zustand, vitest.

**Spec:** `docs/superpowers/specs/2026-09-07-write-approvals-design.md`

## Global Constraints

Copied verbatim from `docs/superpowers/specs/2026-09-07-platform-improvements-roadmap.md` ("Global constraints (bind every plan)"):

- Server: Python 3.11 in Docker, local dev on 3.14; code root `services/server/src`
  (import root `src.*`), tests in `services/server/tests`, run with
  `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider` from `services/server`.
  Tests never need a live database: follow the fake-pool pattern already used
  by the existing tests (search `tests/` for `get_pool` monkeypatching).
- All DB access is raw SQL through `src.db.get_pool()` (asyncpg). No ORM.
- Schema changes: from feature 1 on, only as modules in `src/migrations/versions/`.
- MCP tools live in `src/mcp/*.py`, are imported in `src/main.py`, and are gated
  with `@requires_permission("<permission>")` from `src/mcp/permissions.py`.
  New permissions are added to `PERMISSIONS` there and documented in README.
- Per-organization settings go through `src/org_settings.py`; new keys need a
  default and a UI field in Settings when user-facing.
- Scheduler jobs are registered in `src/scheduler.py` (`start_scheduler`).
- REST routes: `src/api/routes/<area>.py`, included in `src/api/app.py`,
  role checks via the helpers in `src/api/deps.py` / `src/tenancy.py`.
- UI: React 18 + TypeScript in `services/ui/src`; API client in `lib/api.ts`,
  store in `store/index.ts`, kit in `components/ui`, pages in `pages/`, routes
  and nav in `App.tsx`. Role gating follows `pages/Organization.tsx`. Verify
  with `npx tsc --noEmit`, `npm run build`, `npx vitest run` from `services/ui`.
  UI copy is English.
- New Python dependencies go in `services/server/pyproject.toml` and are
  installed in the local venv with `uv pip install --python .venv/Scripts/python.exe -e .`
  (run from `services/server`). No new system packages unless the Dockerfile
  is updated in the same task.
- Naming: the product is `context-forge` / `ContextForge`. Never `askme`.
- Code comments: max one line, only where needed. Commit titles 3–10 words,
  English, imperative; body optional, max 20 words.
- README: each feature updates the sections it touches (features, tools,
  env vars, permissions) in the same plan.
- Do not touch `services/server/src/api/routes/product_*`: it does not exist
  here and must not be recreated.

Plan-specific constraints:

- **No new MCP permission is introduced.** The approval flow reuses `db-write`,
  `ssh-write` and `context-write`.
- **The direct-execution path must stay byte-for-byte identical** for a caller
  holding the write permission (including a caller whose permission set is
  `None`, i.e. MCP auth disabled). Task 3 pins this with an acceptance test.
- This feature depends on two earlier features on the same branch:
  - **Feature 1 (versioned migrations)** — `src/migrations/versions/NNNN_<name>.py`
    modules exposing `VERSION: int`, `NAME: str`, `TRANSACTIONAL: bool = True`,
    `async def upgrade(conn) -> None`. Never edit `SCHEMA_SQL` in `src/db.py`.
  - **Feature 4 (MCP audit & rate limits)** — `src/mcp/context.py` exposes
    `Principal` (`kind: "user" | "api_key" | "anonymous"`, `id: int | None`,
    `label: str`) with `get_current_principal()`; `requires_permission` in
    `src/mcp/permissions.py` already audits every tool call. Task 3 edits only
    the permission *condition* inside that decorator, never its audit wrapper.
- All server test commands run from `services/server`:
  `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/<file>`.
- All UI commands run from `services/ui`: `npx tsc --noEmit`, `npm run build`,
  `npx vitest run`.

---

### Task 1: Migration `0004_write_requests` and the write-request service

**Files:**
- Create: `services/server/src/migrations/versions/0004_write_requests.py`
- Create: `services/server/src/write_requests.py`
- Test: `services/server/tests/test_write_requests_migration.py`
- Test: `services/server/tests/test_write_requests_service.py`

**Interfaces:**
- Consumes: `src.db.get_pool()` (asyncpg pool, `pool.acquire()` async context
  manager yielding a connection with `fetch`, `fetchrow`, `fetchval`, `execute`).
  The migration module contract from feature 1: `VERSION: int`, `NAME: str`,
  `TRANSACTIONAL: bool = True`, `async def upgrade(conn) -> None`.
- Produces, all used by Tasks 3–5:
  - `src.write_requests.STATUSES: tuple[str, ...]` =
    `("pending", "approved", "rejected", "executed", "failed", "expired")`
  - `src.write_requests.KIND_PERMISSION: dict[str, str]` =
    `{"db_execute": "db-write", "ssh_write_file": "ssh-write"}`
  - `class WriteRequestError(Exception)`, `class WriteRequestNotFound(WriteRequestError)`,
    `class WriteRequestForbidden(WriteRequestError)`, `class WriteRequestState(WriteRequestError)`
  - `async def create(*, org_id: int, project_id: int, kind: str, target: str,
    payload: dict, preview: dict, reason: str, requested_by_kind: str,
    requested_by_id: int | None, requested_by: str) -> dict`
  - `async def get(request_id: int, org_id: int | None = None,
    project_id: int | None = None) -> dict | None`
  - `async def list_for_org(org_id: int, status: str | None = None,
    limit: int = 50, offset: int = 0) -> dict` → `{"requests": [...], "total": int, "pending": int}`
  - `async def expire_pending() -> int`
  - Every returned record is a JSON-safe dict with the keys `id, org_id,
    project_id, kind, status, target, payload, preview, reason,
    requested_by_kind, requested_by_id, requested_by, decided_by, decided_at,
    decision_note, result, error, created_at, expires_at` (timestamps as ISO
    strings, `payload`/`preview`/`result` as dicts).

- [ ] **Step 1: Write the failing migration test**

Create `services/server/tests/test_write_requests_migration.py`:

```python
"""La migrazione 0004 crea la tabella delle write request e il suo indice."""
import asyncio

from src.migrations.versions import _0004_write_requests as mod  # noqa: F401 - placeholder


class FakeConn:
    def __init__(self):
        self.executed = []

    async def execute(self, sql, *args):
        self.executed.append(sql)


def test_module_declares_the_contract():
    assert mod.VERSION == 4
    assert mod.NAME == "write_requests"
    assert mod.TRANSACTIONAL is True


def test_upgrade_creates_table_and_index():
    conn = FakeConn()
    asyncio.run(mod.upgrade(conn))
    sql = "\n".join(conn.executed)
    assert "CREATE TABLE IF NOT EXISTS write_requests" in sql
    assert "status         TEXT NOT NULL DEFAULT 'pending'" in sql
    assert "write_requests_org_status_idx" in sql
    assert "INTERVAL '7 days'" in sql
```

The import line above is a placeholder: a module named `0004_write_requests`
cannot be imported with a normal `import` statement (it starts with a digit).
Replace that first line with the loader below, which is what the final test
file must contain:

```python
"""La migrazione 0004 crea la tabella delle write request e il suo indice."""
import asyncio
import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "src" / "migrations" / "versions" / "0004_write_requests.py"
_spec = importlib.util.spec_from_file_location("m0004", _PATH)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)
```

- [ ] **Step 2: Run the test to verify it fails**

Run from `services/server`:
`.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_write_requests_migration.py`
Expected: FAIL — `FileNotFoundError` / `spec_from_file_location` returns None,
the version module does not exist yet.

- [ ] **Step 3: Write the migration module**

Create `services/server/src/migrations/versions/0004_write_requests.py`:

```python
"""Write requests: agent-proposed SQL and file writes awaiting human approval."""
from __future__ import annotations

VERSION = 4
NAME = "write_requests"
TRANSACTIONAL = True

SQL = """
CREATE TABLE IF NOT EXISTS write_requests (
    id             BIGSERIAL PRIMARY KEY,
    org_id         BIGINT NOT NULL,
    project_id     BIGINT NOT NULL,
    kind           TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending',
    target         TEXT NOT NULL,
    payload        JSONB NOT NULL,
    preview        JSONB NOT NULL,
    reason         TEXT,
    requested_by_kind TEXT NOT NULL,
    requested_by_id   BIGINT,
    requested_by      TEXT NOT NULL,
    decided_by     BIGINT,
    decided_at     TIMESTAMPTZ,
    decision_note  TEXT,
    result         JSONB,
    error          TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at     TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '7 days'
);

CREATE INDEX IF NOT EXISTS write_requests_org_status_idx
    ON write_requests (org_id, status, created_at DESC);
"""


async def upgrade(conn) -> None:
    await conn.execute(SQL)
```

- [ ] **Step 4: Run the migration test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_write_requests_migration.py`
Expected: PASS (3 tests).

- [ ] **Step 5: Write the failing service test**

Create `services/server/tests/test_write_requests_service.py`. It uses the
fake-pool pattern from `tests/test_role_permissions.py` — no live database:

```python
"""Stato e persistenza delle write request, con pool finto (nessun DB reale)."""
import asyncio
import json

from src import write_requests


class FakeConn:
    def __init__(self, rows=None, row=None, value=None, execute_result="UPDATE 0"):
        self.rows = rows if rows is not None else []
        self.row = row
        self.value = value
        self.execute_result = execute_result
        self.calls = []

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return self.rows

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        return self.row

    async def fetchval(self, sql, *args):
        self.calls.append((sql, args))
        return self.value

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        return self.execute_result


class FakeAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return FakeAcquire(self.conn)


def _patch_pool(monkeypatch, conn):
    async def fake_pool():
        return FakePool(conn)

    monkeypatch.setattr(write_requests, "get_pool", fake_pool)


class _Stamp:
    def __init__(self, text):
        self.text = text

    def isoformat(self):
        return self.text


def _row(**over):
    row = {
        "id": 12, "org_id": 1, "project_id": 2, "kind": "db_execute",
        "status": "pending", "target": "erp",
        "payload": json.dumps({"sql": "UPDATE t SET a=1 WHERE id=1"}),
        "preview": json.dumps({"plan_rows": 3}),
        "reason": "fix a bad row",
        "requested_by_kind": "api_key", "requested_by_id": 5, "requested_by": "ci-bot",
        "decided_by": None, "decided_at": None, "decision_note": None,
        "result": None, "error": None,
        "created_at": _Stamp("2026-09-07T10:00:00+00:00"),
        "expires_at": _Stamp("2026-09-14T10:00:00+00:00"),
    }
    row.update(over)
    return row


def test_statuses_and_kind_permissions():
    assert write_requests.STATUSES == (
        "pending", "approved", "rejected", "executed", "failed", "expired"
    )
    assert write_requests.KIND_PERMISSION == {
        "db_execute": "db-write", "ssh_write_file": "ssh-write"
    }


def test_create_inserts_and_returns_a_json_safe_record(monkeypatch):
    conn = FakeConn(row=_row())
    _patch_pool(monkeypatch, conn)

    record = asyncio.run(write_requests.create(
        org_id=1, project_id=2, kind="db_execute", target="erp",
        payload={"sql": "UPDATE t SET a=1 WHERE id=1"}, preview={"plan_rows": 3},
        reason="fix a bad row", requested_by_kind="api_key",
        requested_by_id=5, requested_by="ci-bot",
    ))

    sql, args = conn.calls[0]
    assert "INSERT INTO write_requests" in sql
    assert args[0] == 1 and args[1] == 2 and args[2] == "db_execute"
    # payload/preview arrivano serializzati, come per le altre colonne jsonb
    assert json.loads(args[4]) == {"sql": "UPDATE t SET a=1 WHERE id=1"}
    assert record["status"] == "pending"
    assert record["payload"] == {"sql": "UPDATE t SET a=1 WHERE id=1"}
    assert record["preview"] == {"plan_rows": 3}
    assert record["created_at"] == "2026-09-07T10:00:00+00:00"


def test_get_returns_none_for_another_org(monkeypatch):
    _patch_pool(monkeypatch, FakeConn(row=_row(org_id=1, project_id=2)))
    assert asyncio.run(write_requests.get(12, org_id=99)) is None


def test_get_returns_none_for_another_project(monkeypatch):
    _patch_pool(monkeypatch, FakeConn(row=_row(org_id=1, project_id=2)))
    assert asyncio.run(write_requests.get(12, org_id=1, project_id=77)) is None


def test_get_returns_the_record_in_scope(monkeypatch):
    _patch_pool(monkeypatch, FakeConn(row=_row()))
    record = asyncio.run(write_requests.get(12, org_id=1, project_id=2))
    assert record["id"] == 12
    assert record["payload"]["sql"] == "UPDATE t SET a=1 WHERE id=1"


def test_get_missing_row_is_none(monkeypatch):
    _patch_pool(monkeypatch, FakeConn(row=None))
    assert asyncio.run(write_requests.get(12)) is None


def test_list_for_org_filters_by_status_and_counts(monkeypatch):
    conn = FakeConn(rows=[_row(), _row(id=13)], value=4)
    _patch_pool(monkeypatch, conn)

    out = asyncio.run(write_requests.list_for_org(1, status="pending", limit=50, offset=0))

    assert out["total"] == 4 and out["pending"] == 4
    assert [r["id"] for r in out["requests"]] == [12, 13]
    sql, args = conn.calls[0]
    assert "WHERE org_id=$1 AND status=$2" in sql
    assert args == (1, "pending", 50, 0)


def test_list_for_org_without_status_lists_everything(monkeypatch):
    conn = FakeConn(rows=[_row()], value=1)
    _patch_pool(monkeypatch, conn)
    out = asyncio.run(write_requests.list_for_org(1))
    sql, args = conn.calls[0]
    assert "AND status=" not in sql
    assert args == (1, 50, 0)
    assert out["requests"][0]["id"] == 12


def test_list_for_org_caps_the_limit(monkeypatch):
    conn = FakeConn(rows=[], value=0)
    _patch_pool(monkeypatch, conn)
    asyncio.run(write_requests.list_for_org(1, limit=5000, offset=-3))
    _sql, args = conn.calls[0]
    assert args == (1, 200, 0)


def test_expire_pending_parses_the_updated_count(monkeypatch):
    conn = FakeConn(execute_result="UPDATE 7")
    _patch_pool(monkeypatch, conn)
    assert asyncio.run(write_requests.expire_pending()) == 7
    sql, _args = conn.calls[0]
    assert "SET status='expired'" in sql
    assert "status='pending'" in sql
    assert "expires_at < NOW()" in sql


def test_expire_pending_unparsable_result_is_zero(monkeypatch):
    _patch_pool(monkeypatch, FakeConn(execute_result=""))
    assert asyncio.run(write_requests.expire_pending()) == 0
```

- [ ] **Step 6: Run the service test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_write_requests_service.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.write_requests'`.

- [ ] **Step 7: Write the service module**

Create `services/server/src/write_requests.py`:

```python
"""Write requests: agent proposals for SQL and file writes, approved by a human.

A caller without the write permission does not execute: the tool stores the
statement (or file content) here with a preview, and an organization admin
approves it, which is when the existing executors actually run.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from .db import get_pool

logger = logging.getLogger(__name__)

STATUSES = ("pending", "approved", "rejected", "executed", "failed", "expired")
KIND_PERMISSION = {"db_execute": "db-write", "ssh_write_file": "ssh-write"}

_FIELDS = (
    "id, org_id, project_id, kind, status, target, payload, preview, reason, "
    "requested_by_kind, requested_by_id, requested_by, decided_by, decided_at, "
    "decision_note, result, error, created_at, expires_at"
)

MAX_LIST_LIMIT = 200


class WriteRequestError(Exception):
    pass


class WriteRequestNotFound(WriteRequestError):
    pass


class WriteRequestForbidden(WriteRequestError):
    pass


class WriteRequestState(WriteRequestError):
    pass


def _to_dict(row: Any) -> dict[str, Any]:
    d = dict(row)
    for key in ("payload", "preview", "result"):
        if isinstance(d.get(key), str):
            d[key] = json.loads(d[key] or "null")
    for key in ("created_at", "decided_at", "expires_at"):
        if d.get(key) is not None and hasattr(d[key], "isoformat"):
            d[key] = d[key].isoformat()
    return d


async def create(
    *,
    org_id: int,
    project_id: int,
    kind: str,
    target: str,
    payload: dict[str, Any],
    preview: dict[str, Any],
    reason: str = "",
    requested_by_kind: str,
    requested_by_id: Optional[int],
    requested_by: str,
) -> dict[str, Any]:
    """Store a pending proposal and return the created record."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            INSERT INTO write_requests
                (org_id, project_id, kind, target, payload, preview, reason,
                 requested_by_kind, requested_by_id, requested_by)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, $8, $9, $10)
            RETURNING {_FIELDS}
            """,
            org_id,
            project_id,
            kind,
            target,
            json.dumps(payload),
            json.dumps(preview),
            reason or None,
            requested_by_kind,
            requested_by_id,
            requested_by,
        )
    return _to_dict(row)


async def get(
    request_id: int, org_id: Optional[int] = None, project_id: Optional[int] = None
) -> Optional[dict[str, Any]]:
    """Fetch one request; returns None when it is outside the given org/project."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_FIELDS} FROM write_requests WHERE id=$1", request_id
        )
    if row is None:
        return None
    record = _to_dict(row)
    if org_id is not None and record["org_id"] != org_id:
        return None
    if project_id is not None and record["project_id"] != project_id:
        return None
    return record


async def list_for_org(
    org_id: int, status: Optional[str] = None, limit: int = 50, offset: int = 0
) -> dict[str, Any]:
    limit = max(1, min(int(limit), MAX_LIST_LIMIT))
    offset = max(0, int(offset))
    pool = await get_pool()
    async with pool.acquire() as conn:
        if status:
            rows = await conn.fetch(
                f"SELECT {_FIELDS} FROM write_requests WHERE org_id=$1 AND status=$2 "
                f"ORDER BY created_at DESC LIMIT $3 OFFSET $4",
                org_id, status, limit, offset,
            )
            total = await conn.fetchval(
                "SELECT count(*) FROM write_requests WHERE org_id=$1 AND status=$2",
                org_id, status,
            )
        else:
            rows = await conn.fetch(
                f"SELECT {_FIELDS} FROM write_requests WHERE org_id=$1 "
                f"ORDER BY created_at DESC LIMIT $2 OFFSET $3",
                org_id, limit, offset,
            )
            total = await conn.fetchval(
                "SELECT count(*) FROM write_requests WHERE org_id=$1", org_id
            )
        pending = await conn.fetchval(
            "SELECT count(*) FROM write_requests WHERE org_id=$1 AND status='pending'",
            org_id,
        )
    return {
        "requests": [_to_dict(r) for r in rows],
        "total": int(total or 0),
        "pending": int(pending or 0),
    }


async def expire_pending() -> int:
    """Mark overdue pending requests as expired; returns how many."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE write_requests SET status='expired' "
            "WHERE status='pending' AND expires_at < NOW()"
        )
    try:
        return int(str(result).split()[-1])
    except (ValueError, IndexError):
        return 0
```

- [ ] **Step 8: Run both test files to verify they pass**

Run:
`.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_write_requests_migration.py tests/test_write_requests_service.py`
Expected: PASS (13 tests).

- [ ] **Step 9: Commit**

```bash
git add services/server/src/migrations/versions/0004_write_requests.py services/server/src/write_requests.py services/server/tests/test_write_requests_migration.py services/server/tests/test_write_requests_service.py
git commit -m "feat: store agent write requests for approval"
```

---

### Task 2: Preview builders for SQL and file proposals

**Files:**
- Create: `services/server/src/write_previews.py`
- Test: `services/server/tests/test_write_previews.py`

**Interfaces:**
- Consumes (existing code, exact signatures — do not change them):
  - `src.datasources.service.get_connection(org_id: int, project_id: int, ref: int | str, include_secret: bool = False) -> dict`
  - `src.datasources.service._resolve_engine(record: dict) -> sqlalchemy.engine.Engine`
  - `src.datasources.service._execute_readonly(engine, sql: str, max_rows: int) -> tuple[list[str], list[dict], bool]`
    — it opens `engine.connect()` and closes it without committing, so whatever
    the statement did is rolled back. `QUERY_TIMEOUT_SECONDS = 15` is set as a
    server-side `statement_timeout` inside it.
  - `src.ssh_sources.client.read_file(conn: dict, path: str, offset=None, tail=None) -> dict`
    returning `{"path", "size", "offset", "bytes_read", "truncated", "content"}`
    and raising on a missing file. `client.MAX_WRITE_BYTES = 1_000_000`.
  - `src.ssh_sources.service.decrypted_conn(record: dict) -> dict` — SFTP
    connection parameters with the secrets in clear.
- Produces, used by Task 3:
  - `MAX_PROPOSAL_CONTENT_BYTES = 512 * 1024`
  - `PLAN_TEXT_CHARS = 2000`
  - `def explain_statement(dialect: str, sql: str) -> str` (raises `UnsupportedExplain`)
  - `class UnsupportedExplain(Exception)`
  - `def extract_plan(rows: list[dict]) -> tuple[int | None, str]` → `(plan_rows, plan_text)`
  - `async def sql_preview(org_id: int, project_id: int, connection: str, sql: str) -> dict`
    → `{"statement": str, "plan_rows": int | None, "plan_text": str}` plus
    `"explain_error": str` when the EXPLAIN could not run
  - `def file_preview(conn_params: dict, path: str, content: str) -> dict`
    → `{"path", "content_bytes", "content_sha256", "existing_sha256",
    "existing_size", "is_new"}`; raises `ValueError` over the size cap.
    It is blocking (SFTP) — call it with `asyncio.to_thread`.

- [ ] **Step 1: Write the failing test**

Create `services/server/tests/test_write_previews.py`:

```python
"""Preview delle proposte di scrittura: EXPLAIN per SQL, hash/dimensioni per i file."""
import asyncio
import hashlib
import json

import pytest

from src import write_previews


# ===== EXPLAIN =====

def test_explain_statement_postgres():
    assert write_previews.explain_statement(
        "postgresql", "UPDATE t SET a=1 WHERE id=1"
    ) == "EXPLAIN (FORMAT JSON) UPDATE t SET a=1 WHERE id=1"


def test_explain_statement_mysql():
    assert write_previews.explain_statement(
        "mysql", "DELETE FROM t WHERE id=1"
    ) == "EXPLAIN FORMAT=JSON DELETE FROM t WHERE id=1"


def test_explain_statement_unsupported_dialect():
    with pytest.raises(write_previews.UnsupportedExplain):
        write_previews.explain_statement("sqlite", "DELETE FROM t WHERE id=1")


def test_extract_plan_reads_top_node_plan_rows():
    plan = [{"Plan": {"Node Type": "Update", "Plan Rows": 42}}]
    rows = [{"QUERY PLAN": plan}]
    plan_rows, plan_text = write_previews.extract_plan(rows)
    assert plan_rows == 42
    assert "Plan Rows" in plan_text


def test_extract_plan_accepts_a_json_string_cell():
    plan = json.dumps([{"Plan": {"Plan Rows": 7}}])
    plan_rows, plan_text = write_previews.extract_plan([{"QUERY PLAN": plan}])
    assert plan_rows == 7


def test_extract_plan_truncates_plan_text():
    plan = [{"Plan": {"Plan Rows": 1, "Filter": "x" * 5000}}]
    _rows, plan_text = write_previews.extract_plan([{"QUERY PLAN": plan}])
    assert len(plan_text) == write_previews.PLAN_TEXT_CHARS


def test_extract_plan_without_plan_rows_is_none():
    plan_rows, plan_text = write_previews.extract_plan([{"EXPLAIN": '{"query_block": {}}'}])
    assert plan_rows is None
    assert "query_block" in plan_text


def test_extract_plan_on_empty_result():
    assert write_previews.extract_plan([]) == (None, "")


# ===== sql_preview =====

class _FakeDialect:
    name = "postgresql"


class _FakeEngine:
    dialect = _FakeDialect()


def _patch_datasources(monkeypatch, executed, rows):
    from src.datasources import service

    async def fake_get_connection(org_id, project_id, ref, include_secret=False):
        return {"id": 4, "name": "erp"}

    async def fake_resolve_engine(record):
        return _FakeEngine()

    def fake_execute_readonly(engine, sql, max_rows):
        executed.append(sql)
        return (["QUERY PLAN"], rows, False)

    monkeypatch.setattr(service, "get_connection", fake_get_connection)
    monkeypatch.setattr(service, "_resolve_engine", fake_resolve_engine)
    monkeypatch.setattr(service, "_execute_readonly", fake_execute_readonly)


def test_sql_preview_runs_explain_through_the_readonly_executor(monkeypatch):
    executed = []
    _patch_datasources(monkeypatch, executed, [{"QUERY PLAN": [{"Plan": {"Plan Rows": 9}}]}])

    preview = asyncio.run(
        write_previews.sql_preview(1, 2, "erp", "UPDATE t SET a=1 WHERE id=1")
    )

    assert executed == ["EXPLAIN (FORMAT JSON) UPDATE t SET a=1 WHERE id=1"]
    assert preview["statement"] == "UPDATE t SET a=1 WHERE id=1"
    assert preview["plan_rows"] == 9
    assert "Plan Rows" in preview["plan_text"]
    assert "explain_error" not in preview


def test_sql_preview_survives_an_explain_failure(monkeypatch):
    from src.datasources import service

    async def fake_get_connection(org_id, project_id, ref, include_secret=False):
        return {"id": 4, "name": "erp"}

    async def fake_resolve_engine(record):
        return _FakeEngine()

    def boom(engine, sql, max_rows):
        raise RuntimeError("permission denied for table t")

    monkeypatch.setattr(service, "get_connection", fake_get_connection)
    monkeypatch.setattr(service, "_resolve_engine", fake_resolve_engine)
    monkeypatch.setattr(service, "_execute_readonly", boom)

    preview = asyncio.run(
        write_previews.sql_preview(1, 2, "erp", "UPDATE t SET a=1 WHERE id=1")
    )
    assert preview["plan_rows"] is None
    assert "permission denied" in preview["explain_error"]
    assert preview["statement"] == "UPDATE t SET a=1 WHERE id=1"


# ===== file_preview =====

def _conn():
    return {"root_path": "/etc/app", "host": "h", "port": 22, "username": "u",
            "auth_method": "password", "password": "p", "include_globs": None,
            "exclude_globs": None}


def test_file_preview_for_a_new_file(monkeypatch):
    from src.ssh_sources import client

    def missing(conn, path, offset=None, tail=None):
        raise FileNotFoundError(path)

    monkeypatch.setattr(client, "read_file", missing)

    preview = write_previews.file_preview(_conn(), "conf.d/app.yml", "key: value\n")
    assert preview["is_new"] is True
    assert preview["path"] == "conf.d/app.yml"
    assert preview["content_bytes"] == len(b"key: value\n")
    assert preview["content_sha256"] == hashlib.sha256(b"key: value\n").hexdigest()
    assert preview["existing_sha256"] is None
    assert preview["existing_size"] is None


def test_file_preview_for_an_existing_file(monkeypatch):
    from src.ssh_sources import client

    def existing(conn, path, offset=None, tail=None):
        return {"path": path, "size": 5, "offset": 0, "bytes_read": 5,
                "truncated": False, "content": "old\n\n"}

    monkeypatch.setattr(client, "read_file", existing)

    preview = write_previews.file_preview(_conn(), "app.yml", "new\n")
    assert preview["is_new"] is False
    assert preview["existing_size"] == 5
    assert preview["existing_sha256"] == hashlib.sha256(b"old\n\n").hexdigest()


def test_file_preview_skips_the_hash_of_a_truncated_read(monkeypatch):
    from src.ssh_sources import client

    def huge(conn, path, offset=None, tail=None):
        return {"path": path, "size": 9_000_000, "offset": 0, "bytes_read": 1_000_000,
                "truncated": True, "content": "x" * 1_000_000}

    monkeypatch.setattr(client, "read_file", huge)

    preview = write_previews.file_preview(_conn(), "big.log", "new\n")
    assert preview["is_new"] is False
    assert preview["existing_size"] == 9_000_000
    assert preview["existing_sha256"] is None


def test_file_preview_refuses_oversize_content(monkeypatch):
    from src.ssh_sources import client

    def boom(conn, path, offset=None, tail=None):
        raise AssertionError("must not touch SSH before the size check")

    monkeypatch.setattr(client, "read_file", boom)
    over = "x" * (write_previews.MAX_PROPOSAL_CONTENT_BYTES + 1)
    with pytest.raises(ValueError, match="exceeds"):
        write_previews.file_preview(_conn(), "big.txt", over)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_write_previews.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.write_previews'`.

- [ ] **Step 3: Write the preview module**

Create `services/server/src/write_previews.py`:

```python
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
    except Exception:  # noqa: BLE001 - a missing or unreadable file is simply "new"
        return preview
    preview["is_new"] = False
    preview["existing_size"] = existing.get("size")
    if not existing.get("truncated"):
        preview["existing_sha256"] = hashlib.sha256(
            (existing.get("content") or "").encode("utf-8")
        ).hexdigest()
    return preview
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_write_previews.py`
Expected: PASS (14 tests).

- [ ] **Step 5: Commit**

```bash
git add services/server/src/write_previews.py services/server/tests/test_write_previews.py
git commit -m "feat: build previews for proposed writes"
```

---

### Task 3: Tool dispatch, `write_request_status`, and the alternatives gate

**Files:**
- Modify: `services/server/src/mcp/permissions.py` (`requires_permission` condition, new `has_permission`)
- Create: `services/server/src/mcp/approvals.py` (new tool + shared helpers)
- Modify: `services/server/src/mcp/datasources.py` (`db_execute`)
- Modify: `services/server/src/mcp/ssh_files.py` (`ssh_write_file`)
- Modify: `services/server/src/main.py` (import the new tool module)
- Test: `services/server/tests/test_write_request_tools.py`

**Interfaces:**
- Consumes from Task 1: `src.write_requests.create(...) -> dict` and
  `src.write_requests.get(request_id, org_id=None, project_id=None) -> dict | None`
  (see Task 1 for the full record shape).
- Consumes from Task 2: `src.write_previews.sql_preview(org_id, project_id, connection, sql) -> dict`
  and `src.write_previews.file_preview(conn_params, path, content) -> dict` (blocking).
- Consumes from feature 4: `src.mcp.context.get_current_principal() -> Principal | None`
  where `Principal` has `.kind` (`"user" | "api_key" | "anonymous"`), `.id`
  (`int | None`) and `.label` (`str`).
- Consumes existing code:
  - `src.mcp.permissions.get_current_permissions() -> frozenset | None`
    (`None` means MCP auth is off and everything is allowed)
  - `src.datasources.service.run_write(org_id, project_id, ref, sql, source="mcp") -> dict`
  - `src.datasources.service.get_connection(org_id, project_id, ref, include_secret=False) -> dict`
  - `src.datasources.validator.validate_write_query(sql) -> str` raising `QueryValidationError`
  - `src.ssh_sources.client.write_file(conn, path, content) -> dict`
  - `src.ssh_sources.service.decrypted_conn(record) -> dict`
- Produces, used by Tasks 4–6:
  - `src.mcp.permissions.has_permission(permission: str) -> bool`
  - `requires_permission(permission: str, *, alternatives: tuple[str, ...] = ())`
  - `src.mcp.approvals.current_requester() -> tuple[str, int | None, str]`
  - `src.mcp.approvals.pending_response(request_id: int, preview: dict) -> dict`
  - MCP tool `write_request_status(request_id: int) -> dict`

- [ ] **Step 1: Write the failing test**

Create `services/server/tests/test_write_request_tools.py`:

```python
"""Dispatch dei tool di scrittura: esecuzione diretta o proposta da approvare."""
import asyncio

import pytest

from src import write_previews, write_requests as wr_service
from src.mcp import approvals as mcp_approvals
from src.mcp import datasources as mcp_datasources
from src.mcp import ssh_files as mcp_ssh
from src.mcp.context import set_current_org_id, set_current_project_id
from src.mcp.permissions import set_current_permissions


@pytest.fixture(autouse=True)
def _reset_mcp_context():
    yield
    set_current_org_id(None)
    set_current_project_id(None)
    set_current_permissions(None)


def _underlying(tool):
    return getattr(tool, "fn", tool)


class _Principal:
    kind = "api_key"
    id = 5
    label = "ci-bot"


def _patch_principal(monkeypatch):
    monkeypatch.setattr(mcp_approvals, "get_current_principal", lambda: _Principal(), raising=False)


# ===== db_execute =====

def test_db_execute_direct_path_is_unchanged(monkeypatch):
    """ACCETTAZIONE: con db-write la risposta è identica a prima, byte per byte."""
    expected = {"connection": "erp", "sql": "UPDATE t SET a=1 WHERE id=1",
                "row_count": 1, "duration_ms": 5}
    called = {}

    async def fake_run_write(org_id, project_id, ref, sql, source="mcp"):
        called.update(org=org_id, project=project_id, ref=ref, sql=sql, source=source)
        return expected

    async def no_create(**kwargs):
        raise AssertionError("a caller holding db-write must not create a request")

    monkeypatch.setattr(mcp_datasources.service, "run_write", fake_run_write)
    monkeypatch.setattr(wr_service, "create", no_create)
    set_current_org_id(1)
    set_current_project_id(2)
    set_current_permissions(frozenset({"db-write"}))

    out = asyncio.run(_underlying(mcp_datasources.db_execute)(
        connection="erp", sql="UPDATE t SET a=1 WHERE id=1"))

    assert out == expected
    assert called == {"org": 1, "project": 2, "ref": "erp",
                      "sql": "UPDATE t SET a=1 WHERE id=1", "source": "mcp"}


def test_db_execute_direct_path_with_auth_disabled(monkeypatch):
    """Permessi None (auth MCP disattivata): resta l'esecuzione diretta."""
    async def fake_run_write(org_id, project_id, ref, sql, source="mcp"):
        return {"connection": "erp", "sql": sql, "row_count": 2, "duration_ms": 1}

    monkeypatch.setattr(mcp_datasources.service, "run_write", fake_run_write)
    set_current_org_id(1)
    set_current_project_id(2)
    set_current_permissions(None)
    out = asyncio.run(_underlying(mcp_datasources.db_execute)(
        connection="erp", sql="UPDATE t SET a=1 WHERE id=1"))
    assert out["row_count"] == 2


def test_db_execute_direct_path_error_shape_is_unchanged(monkeypatch):
    async def boom(org_id, project_id, ref, sql, source="mcp"):
        raise RuntimeError("db statement timeout")

    monkeypatch.setattr(mcp_datasources.service, "run_write", boom)
    set_current_org_id(1)
    set_current_project_id(2)
    set_current_permissions(frozenset({"db-write"}))
    out = asyncio.run(_underlying(mcp_datasources.db_execute)(
        connection="erp", sql="UPDATE t SET a=1 WHERE id=1"))
    assert out == {"status": "error", "error": "db statement timeout"}


def test_db_execute_without_db_write_creates_a_request(monkeypatch):
    _patch_principal(monkeypatch)
    created = {}

    async def no_run_write(*a, **k):
        raise AssertionError("must not execute without db-write")

    async def fake_get_connection(org_id, project_id, ref, include_secret=False):
        return {"id": 4, "name": "erp"}

    async def fake_sql_preview(org_id, project_id, connection, sql):
        return {"statement": sql, "plan_rows": 3, "plan_text": "{}"}

    async def fake_create(**kwargs):
        created.update(kwargs)
        return {"id": 12}

    monkeypatch.setattr(mcp_datasources.service, "run_write", no_run_write)
    monkeypatch.setattr(mcp_datasources.service, "get_connection", fake_get_connection)
    monkeypatch.setattr(write_previews, "sql_preview", fake_sql_preview)
    monkeypatch.setattr(wr_service, "create", fake_create)
    set_current_org_id(1)
    set_current_project_id(2)
    set_current_permissions(frozenset({"context-read", "context-write"}))

    out = asyncio.run(_underlying(mcp_datasources.db_execute)(
        connection="erp", sql="UPDATE t SET a=1 WHERE id=1", reason="fix a bad row"))

    assert out == {
        "status": "pending_approval",
        "request_id": 12,
        "preview": {"statement": "UPDATE t SET a=1 WHERE id=1", "plan_rows": 3, "plan_text": "{}"},
        "message": ("An organization admin must approve this change. "
                    "Poll with write_request_status(12)."),
    }
    assert created["kind"] == "db_execute"
    assert created["target"] == "erp"
    assert created["payload"] == {"sql": "UPDATE t SET a=1 WHERE id=1"}
    assert created["reason"] == "fix a bad row"
    assert created["requested_by_kind"] == "api_key"
    assert created["requested_by_id"] == 5
    assert created["requested_by"] == "ci-bot"


def test_db_execute_proposal_validates_before_previewing(monkeypatch):
    async def no_preview(*a, **k):
        raise AssertionError("must not preview an invalid statement")

    monkeypatch.setattr(write_previews, "sql_preview", no_preview)
    set_current_org_id(1)
    set_current_project_id(2)
    set_current_permissions(frozenset({"context-write"}))

    out = asyncio.run(_underlying(mcp_datasources.db_execute)(
        connection="erp", sql="DROP TABLE t"))
    assert out["status"] == "error"
    assert "Query rejected" in out["error"]


def test_db_execute_with_neither_permission_is_denied():
    set_current_org_id(1)
    set_current_project_id(2)
    set_current_permissions(frozenset({"context-read", "db-query"}))
    with pytest.raises(Exception, match="db-write"):
        asyncio.run(_underlying(mcp_datasources.db_execute)(
            connection="erp", sql="UPDATE t SET a=1 WHERE id=1"))


# ===== ssh_write_file =====

def _source_record():
    return {"id": 3, "org_id": 1, "project_id": 2, "name": "web1",
            "root_path": "/etc/app", "host": "h", "port": 22, "username": "u",
            "auth_method": "password", "password_enc": "", "private_key_enc": "",
            "include_globs": None, "exclude_globs": None}


def test_ssh_write_file_direct_path_is_unchanged(monkeypatch):
    """ACCETTAZIONE: con ssh-write la risposta è identica a prima."""
    async def fake_resolve(source, include_secret=False):
        return _source_record()

    def fake_write(conn, path, content):
        return {"path": path, "bytes_written": len(content.encode("utf-8"))}

    async def no_create(**kwargs):
        raise AssertionError("a caller holding ssh-write must not create a request")

    monkeypatch.setattr(mcp_ssh, "_resolve", fake_resolve)
    monkeypatch.setattr(mcp_ssh.ssh_service, "decrypted_conn", lambda r: {"root_path": "/etc/app"})
    from src.ssh_sources import client
    monkeypatch.setattr(client, "write_file", fake_write)
    monkeypatch.setattr(wr_service, "create", no_create)
    set_current_permissions(frozenset({"ssh-write"}))

    out = asyncio.run(_underlying(mcp_ssh.ssh_write_file)(
        source="web1", path="app.yml", content="key: value\n"))
    assert out == {"source": "web1", "path": "app.yml", "bytes_written": 11}


def test_ssh_write_file_without_ssh_write_creates_a_request(monkeypatch):
    _patch_principal(monkeypatch)
    created = {}

    async def fake_resolve(source, include_secret=False):
        return _source_record()

    def no_write(conn, path, content):
        raise AssertionError("must not write without ssh-write")

    def fake_file_preview(conn_params, path, content):
        return {"path": path, "content_bytes": 11, "content_sha256": "abc",
                "existing_sha256": None, "existing_size": None, "is_new": True}

    async def fake_create(**kwargs):
        created.update(kwargs)
        return {"id": 31}

    monkeypatch.setattr(mcp_ssh, "_resolve", fake_resolve)
    monkeypatch.setattr(mcp_ssh.ssh_service, "decrypted_conn", lambda r: {"root_path": "/etc/app"})
    from src.ssh_sources import client
    monkeypatch.setattr(client, "write_file", no_write)
    monkeypatch.setattr(write_previews, "file_preview", fake_file_preview)
    monkeypatch.setattr(wr_service, "create", fake_create)
    set_current_permissions(frozenset({"context-write"}))

    out = asyncio.run(_underlying(mcp_ssh.ssh_write_file)(
        source="web1", path="app.yml", content="key: value\n", reason="rotate the port"))

    assert out["status"] == "pending_approval"
    assert out["request_id"] == 31
    assert out["message"].endswith("Poll with write_request_status(31).")
    assert created["kind"] == "ssh_write_file"
    assert created["target"] == "web1:app.yml"
    assert created["payload"] == {"source": "web1", "path": "app.yml", "content": "key: value\n"}
    assert created["org_id"] == 1 and created["project_id"] == 2


def test_ssh_write_file_with_neither_permission_is_denied():
    set_current_permissions(frozenset({"ssh-read"}))
    with pytest.raises(Exception, match="ssh-write"):
        asyncio.run(_underlying(mcp_ssh.ssh_write_file)(
            source="web1", path="app.yml", content="x"))


# ===== write_request_status =====

def test_write_request_status_returns_the_record(monkeypatch):
    async def fake_get(request_id, org_id=None, project_id=None):
        assert (request_id, org_id, project_id) == (12, 1, 2)
        return {"id": 12, "kind": "db_execute", "target": "erp", "status": "executed",
                "decision_note": "looks fine", "result": {"row_count": 1}, "error": None,
                "created_at": "2026-09-07T10:00:00+00:00",
                "expires_at": "2026-09-14T10:00:00+00:00"}

    monkeypatch.setattr(wr_service, "get", fake_get)
    set_current_org_id(1)
    set_current_project_id(2)
    set_current_permissions(frozenset({"context-read"}))

    out = asyncio.run(_underlying(mcp_approvals.write_request_status)(request_id=12))
    assert out["status"] == "ok"
    assert out["state"] == "executed"
    assert out["result"] == {"row_count": 1}
    assert out["decision_note"] == "looks fine"


def test_write_request_status_is_scoped_to_org_and_project(monkeypatch):
    async def fake_get(request_id, org_id=None, project_id=None):
        return None

    monkeypatch.setattr(wr_service, "get", fake_get)
    set_current_org_id(1)
    set_current_project_id(2)
    set_current_permissions(frozenset({"context-read"}))
    out = asyncio.run(_underlying(mcp_approvals.write_request_status)(request_id=12))
    assert out["status"] == "error"
    assert "not found" in out["error"]


def test_write_request_status_requires_context_read():
    set_current_org_id(1)
    set_current_project_id(2)
    set_current_permissions(frozenset({"jobs"}))
    with pytest.raises(Exception, match="context-read"):
        asyncio.run(_underlying(mcp_approvals.write_request_status)(request_id=12))


# ===== permission helpers =====

def test_has_permission_true_when_auth_is_disabled():
    from src.mcp.permissions import has_permission
    set_current_permissions(None)
    assert has_permission("db-write") is True


def test_has_permission_honours_the_wildcard():
    from src.mcp.permissions import has_permission
    set_current_permissions(frozenset({"*"}))
    assert has_permission("ssh-write") is True


def test_has_permission_false_when_missing():
    from src.mcp.permissions import has_permission
    set_current_permissions(frozenset({"context-write"}))
    assert has_permission("db-write") is False


def test_requires_permission_accepts_an_alternative():
    from src.mcp.permissions import requires_permission, set_current_permissions as setp

    @requires_permission("db-write", alternatives=("context-write",))
    async def tool():
        return "ran"

    setp(frozenset({"context-write"}))
    assert asyncio.run(tool()) == "ran"


def test_requires_permission_denial_message_names_the_primary():
    from src.mcp.permissions import requires_permission, set_current_permissions as setp

    @requires_permission("db-write", alternatives=("context-write",))
    async def tool():
        return "ran"

    setp(frozenset({"db-query"}))
    with pytest.raises(Exception, match="db-write"):
        asyncio.run(tool())
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_write_request_tools.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.mcp.approvals'`.

- [ ] **Step 3: Add `has_permission` and the `alternatives` gate**

In `services/server/src/mcp/permissions.py`, add after `get_current_permissions`:

```python
def has_permission(permission: str) -> bool:
    """True when the current caller holds it. None (auth off) allows everything."""
    perms = _current_permissions.get()
    return perms is None or "*" in perms or permission in perms
```

Then change `requires_permission`. Its signature gains a keyword-only
`alternatives`, and **only the `if` condition inside `wrapper` changes** —
leave any surrounding audit wrapper from feature 4 exactly as it is.

Before:

```python
def requires_permission(permission: str):
    """None (auth off / legacy caller) allows everything; otherwise the set must contain '*' or the permission."""
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            perms = _current_permissions.get()
            if perms is not None and "*" not in perms and permission not in perms:
                raise ToolError(
```

After:

```python
def requires_permission(permission: str, *, alternatives: tuple[str, ...] = ()):
    """None (auth off / legacy caller) allows everything; otherwise the set must contain '*', the permission, or one of `alternatives` (a weaker path, e.g. proposing instead of executing)."""
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            perms = _current_permissions.get()
            allowed = perms is None or "*" in perms or permission in perms or any(
                alt in perms for alt in alternatives
            )
            if not allowed:
                raise ToolError(
```

The `ToolError(...)` message body stays byte-for-byte as it is today — it names
`permission`, so an unauthorized caller sees the same denial as before.

- [ ] **Step 4: Write the approvals tool module**

Create `services/server/src/mcp/approvals.py`:

```python
"""MCP surface of the write-approval flow: polling and the shared proposal helpers."""
from __future__ import annotations

import logging
from typing import Any, Optional

from .server import mcp
from .permissions import requires_permission
from .context import get_current_principal

logger = logging.getLogger(__name__)


def current_requester() -> tuple[str, Optional[int], str]:
    """(kind, id, label) of the MCP caller, stored on the write request."""
    principal = get_current_principal()
    if principal is None:
        return ("anonymous", None, "anonymous")
    return (principal.kind, principal.id, principal.label)


def pending_response(request_id: int, preview: dict[str, Any]) -> dict[str, Any]:
    """The reply a write tool returns instead of executing."""
    return {
        "status": "pending_approval",
        "request_id": request_id,
        "preview": preview,
        "message": (
            "An organization admin must approve this change. "
            f"Poll with write_request_status({request_id})."
        ),
    }


@mcp.tool()
@requires_permission("context-read")
async def write_request_status(request_id: int) -> dict:
    """Check a write request you proposed with db_execute or ssh_write_file.

    A write proposed without the write permission is not executed until an
    organization admin approves it. This returns where it stands and, once
    executed, what it did.

    Args:
        request_id: the id returned by db_execute / ssh_write_file.

    Returns:
        dict with `state` (pending, approved, rejected, executed, failed,
        expired), `decision_note`, `result` when executed, `error` when failed.
    """
    from .. import write_requests as service
    from .context import require_project_id, resolve_org_id

    org_id = await resolve_org_id()
    project_id = await require_project_id()
    record = await service.get(request_id, org_id=org_id, project_id=project_id)
    if record is None:
        return {"status": "error", "error": f"Write request {request_id} not found"}
    return {
        "status": "ok",
        "request_id": record["id"],
        "kind": record["kind"],
        "target": record["target"],
        "state": record["status"],
        "decision_note": record["decision_note"],
        "result": record["result"],
        "error": record["error"],
        "created_at": record["created_at"],
        "expires_at": record["expires_at"],
    }
```

- [ ] **Step 5: Rewrite `db_execute`**

In `services/server/src/mcp/datasources.py`, replace the whole `db_execute`
function with:

```python
@mcp.tool()
@requires_permission("db-write", alternatives=("context-write",))
async def db_execute(connection: str, sql: str, reason: str = "") -> dict:
    """Execute a single INSERT, UPDATE or DELETE on a project datasource.

    Guarded: one DML statement only, UPDATE/DELETE must have a WHERE clause,
    DDL is rejected. Runs in its own transaction and returns the affected
    row count (no result set).

    Without the db-write permission the statement is not executed: it is stored
    as a write request with an EXPLAIN preview for an organization admin to
    approve. Poll the outcome with write_request_status.

    Args:
        connection: datasource name or id (from db_list).
        sql: the DML statement to execute.
        reason: why the change is needed; shown to the approver.
    """
    from .context import resolve_org_id, require_project_id
    from .permissions import has_permission

    org_id = await resolve_org_id()
    project_id = await require_project_id()
    if not has_permission("db-write"):
        return await _propose_db_execute(org_id, project_id, connection, sql, reason)
    try:
        return await service.run_write(org_id, project_id, connection, sql, source="mcp")
    except Exception as e:  # noqa: BLE001
        logger.error("db_execute failed: %s", e)
        return {"status": "error", "error": str(e)}


async def _propose_db_execute(
    org_id: int, project_id: int, connection: str, sql: str, reason: str
) -> dict:
    """Store the statement as a pending write request instead of running it."""
    from .. import write_previews, write_requests
    from ..datasources.validator import QueryValidationError, validate_write_query
    from .approvals import current_requester, pending_response

    try:
        validated = validate_write_query(sql)
    except QueryValidationError as e:
        return {"status": "error", "error": f"Query rejected: {e}"}
    try:
        record = await service.get_connection(org_id, project_id, connection)
        preview = await write_previews.sql_preview(org_id, project_id, connection, validated)
    except Exception as e:  # noqa: BLE001
        logger.error("db_execute proposal failed: %s", e)
        return {"status": "error", "error": str(e)}

    kind, requester_id, label = current_requester()
    created = await write_requests.create(
        org_id=org_id,
        project_id=project_id,
        kind="db_execute",
        target=record["name"],
        payload={"sql": validated},
        preview=preview,
        reason=reason,
        requested_by_kind=kind,
        requested_by_id=requester_id,
        requested_by=label,
    )
    return pending_response(created["id"], preview)
```

- [ ] **Step 6: Rewrite `ssh_write_file`**

In `services/server/src/mcp/ssh_files.py`, replace the whole `ssh_write_file`
function with:

```python
@mcp.tool()
@requires_permission("ssh-write", alternatives=("context-write",))
async def ssh_write_file(source: str, path: str, content: str, reason: str = "") -> dict:
    """Create or overwrite a text file on an SSH source (confined to its root).

    The write is atomic (temp file + rename) and creates any missing
    intermediate directories. Deleting files is not supported.

    Without the ssh-write permission nothing is written: the content is stored
    as a write request with a size/hash preview for an organization admin to
    approve. Poll the outcome with write_request_status.

    Args:
        source: source name or id (from ssh_sources).
        path: file path relative to the source root.
        content: full text content to write.
        reason: why the change is needed; shown to the approver.

    Returns:
        dict with the written `path` and `bytes_written`.
    """
    from ..ssh_sources import client
    from ..ssh_sources.service import decrypted_conn
    from .permissions import has_permission

    try:
        record = await _resolve(source, include_secret=True)
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}

    if not has_permission("ssh-write"):
        return await _propose_ssh_write(record, path, content, reason)

    try:
        result = await asyncio.to_thread(
            client.write_file, decrypted_conn(record), path, content
        )
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}
    return {"source": record["name"], **result}


async def _propose_ssh_write(record: dict, path: str, content: str, reason: str) -> dict:
    """Store the content as a pending write request instead of writing it."""
    from .. import write_previews, write_requests
    from .approvals import current_requester, pending_response

    try:
        preview = await asyncio.to_thread(
            write_previews.file_preview, ssh_service.decrypted_conn(record), path, content
        )
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}

    kind, requester_id, label = current_requester()
    created = await write_requests.create(
        org_id=record["org_id"],
        project_id=record["project_id"],
        kind="ssh_write_file",
        target=f"{record['name']}:{path}",
        payload={"source": record["name"], "path": path, "content": content},
        preview=preview,
        reason=reason,
        requested_by_kind=kind,
        requested_by_id=requester_id,
        requested_by=label,
    )
    return pending_response(created["id"], preview)
```

`ssh_service` is already imported at the top of that module
(`from ..ssh_sources import service as ssh_service`) — do not re-import it.

- [ ] **Step 7: Register the new tool module**

In `services/server/src/main.py`, add `approvals` to the MCP module import
(currently line 31), keeping it on one line:

```python
    from .mcp import memory, repos, jobs, knowledge, web, datasources, contracts, ci, code_tools, code_graph, project_tools, project_admin, ssh_files, git_write_tools, approvals  # noqa: F401
```

- [ ] **Step 8: Run the tests to verify they pass**

Run:
`.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_write_request_tools.py tests/test_db_execute_surfaces.py tests/test_ssh_write_file.py tests/test_permissions.py tests/test_tool_gating.py`
Expected: PASS. The two pre-existing files must pass unchanged — that is the
byte-for-byte guarantee for the direct path.

- [ ] **Step 9: Commit**

```bash
git add services/server/src/mcp/permissions.py services/server/src/mcp/approvals.py services/server/src/mcp/datasources.py services/server/src/mcp/ssh_files.py services/server/src/main.py services/server/tests/test_write_request_tools.py
git commit -m "feat: propose writes when the permission is missing"
```

---

### Task 4: Approval, execution and the expiry job

**Files:**
- Modify: `services/server/src/write_requests.py` (approve, reject, executors, `_finish`)
- Modify: `services/server/src/scheduler.py` (daily expiry job)
- Test: `services/server/tests/test_write_request_approval.py`

**Interfaces:**
- Consumes from Task 1 (same module): `_FIELDS`, `_to_dict`, `get`,
  `KIND_PERMISSION`, `WriteRequestNotFound`, `WriteRequestForbidden`,
  `WriteRequestState`, `expire_pending`.
- Consumes existing code:
  - `src.tenancy.get_membership_role(org_id: int, user_id: int) -> str | None`
  - `src.tenancy.resolve_role_permissions(org_id: int | None, role: str | None) -> frozenset`
    (an org with no customized rows falls back to `DEFAULT_ROLE_PERMISSIONS`;
    `owner` resolves to `frozenset({"*"})`)
  - `src.datasources.service.run_write(org_id, project_id, ref, sql, source="mcp") -> dict`
  - `src.ssh_sources.service.resolve_source(org_id, project_id, ref, include_secret=False) -> dict`
  - `src.ssh_sources.service.decrypted_conn(record) -> dict`
  - `src.ssh_sources.client.write_file(conn, path, content) -> dict`
  - `src.scheduler` registers jobs in `start_scheduler()` with
    `_scheduler.add_job(fn, trigger, id=..., replace_existing=True)`; `CronTrigger`
    is already imported there.
- Produces, used by Task 5:
  - `async def approve(request_id: int, user_id: int, note: str = "") -> dict`
  - `async def reject(request_id: int, user_id: int, note: str = "") -> dict`
  - Both raise `WriteRequestNotFound` / `WriteRequestForbidden` / `WriteRequestState`.

- [ ] **Step 1: Write the failing test**

Create `services/server/tests/test_write_request_approval.py`:

```python
"""Approvazione, esecuzione e scadenza delle write request (nessun DB reale)."""
import asyncio
import inspect
import json

import pytest

from src import scheduler, tenancy, write_requests


class FakeConn:
    def __init__(self, rows_by_call=None):
        self.rows_by_call = list(rows_by_call or [])
        self.calls = []

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        return self.rows_by_call.pop(0) if self.rows_by_call else None

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return []

    async def fetchval(self, sql, *args):
        self.calls.append((sql, args))
        return 0

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        return "UPDATE 0"


class FakeAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return FakeAcquire(self.conn)


def _patch_pool(monkeypatch, conn):
    async def fake_pool():
        return FakePool(conn)

    monkeypatch.setattr(write_requests, "get_pool", fake_pool)


class _Stamp:
    def __init__(self, text):
        self.text = text

    def isoformat(self):
        return self.text


def _row(**over):
    row = {
        "id": 12, "org_id": 1, "project_id": 2, "kind": "db_execute",
        "status": "pending", "target": "erp",
        "payload": json.dumps({"sql": "UPDATE t SET a=1 WHERE id=1"}),
        "preview": json.dumps({"plan_rows": 3}), "reason": "fix a bad row",
        "requested_by_kind": "api_key", "requested_by_id": 5, "requested_by": "ci-bot",
        "decided_by": None, "decided_at": None, "decision_note": None,
        "result": None, "error": None,
        "created_at": _Stamp("2026-09-07T10:00:00+00:00"),
        "expires_at": _Stamp("2026-09-14T10:00:00+00:00"),
    }
    row.update(over)
    return row


def _patch_role(monkeypatch, role, perms=frozenset({"*"})):
    async def fake_role(org_id, user_id):
        return role

    async def fake_perms(org_id, r):
        return perms

    monkeypatch.setattr(tenancy, "get_membership_role", fake_role)
    monkeypatch.setattr(tenancy, "resolve_role_permissions", fake_perms)


def test_approve_executes_and_records_the_result(monkeypatch):
    _patch_role(monkeypatch, "owner")
    executed = {}

    async def fake_db_execute(record):
        executed.update(record["payload"])
        return {"connection": "erp", "row_count": 1, "duration_ms": 4}

    monkeypatch.setattr(write_requests, "_run_db_execute", fake_db_execute)
    conn = FakeConn([
        _row(),                                              # get()
        _row(status="approved", decided_by=9),               # UPDATE -> approved
        _row(status="executed", result=json.dumps({"row_count": 1})),  # _finish
    ])
    _patch_pool(monkeypatch, conn)

    out = asyncio.run(write_requests.approve(12, user_id=9, note="looks fine"))

    assert out["status"] == "executed"
    assert out["result"] == {"row_count": 1}
    assert executed == {"sql": "UPDATE t SET a=1 WHERE id=1"}
    approve_sql, approve_args = conn.calls[1]
    assert "SET status='approved'" in approve_sql
    assert "AND status='pending'" in approve_sql
    assert "expires_at > NOW()" in approve_sql
    assert approve_args == (12, 9, "looks fine")


def test_approve_records_a_failed_execution(monkeypatch):
    _patch_role(monkeypatch, "owner")

    async def boom(record):
        raise RuntimeError("deadlock detected")

    monkeypatch.setattr(write_requests, "_run_db_execute", boom)
    conn = FakeConn([
        _row(),
        _row(status="approved"),
        _row(status="failed", error="deadlock detected"),
    ])
    _patch_pool(monkeypatch, conn)

    out = asyncio.run(write_requests.approve(12, user_id=9))
    assert out["status"] == "failed"
    finish_sql, finish_args = conn.calls[2]
    assert "SET status=$2" in finish_sql
    assert finish_args[1] == "failed"
    assert finish_args[3] == "deadlock detected"


def test_approve_requires_the_write_permission(monkeypatch):
    _patch_role(monkeypatch, "admin", perms=frozenset({"context-read", "jobs"}))
    _patch_pool(monkeypatch, FakeConn([_row()]))
    with pytest.raises(write_requests.WriteRequestForbidden, match="db-write"):
        asyncio.run(write_requests.approve(12, user_id=9))


def test_approve_allows_an_admin_granted_the_permission(monkeypatch):
    _patch_role(monkeypatch, "admin", perms=frozenset({"db-write"}))

    async def fake_db_execute(record):
        return {"row_count": 1}

    monkeypatch.setattr(write_requests, "_run_db_execute", fake_db_execute)
    _patch_pool(monkeypatch, FakeConn([
        _row(), _row(status="approved"), _row(status="executed"),
    ]))
    out = asyncio.run(write_requests.approve(12, user_id=9))
    assert out["status"] == "executed"


def test_approve_rejects_a_non_member(monkeypatch):
    _patch_role(monkeypatch, None)
    _patch_pool(monkeypatch, FakeConn([_row()]))
    with pytest.raises(write_requests.WriteRequestForbidden):
        asyncio.run(write_requests.approve(12, user_id=9))


def test_approve_missing_request(monkeypatch):
    _patch_role(monkeypatch, "owner")
    _patch_pool(monkeypatch, FakeConn([None]))
    with pytest.raises(write_requests.WriteRequestNotFound):
        asyncio.run(write_requests.approve(12, user_id=9))


def test_approve_refuses_a_non_pending_request(monkeypatch):
    _patch_role(monkeypatch, "owner")
    _patch_pool(monkeypatch, FakeConn([_row(status="executed")]))
    with pytest.raises(write_requests.WriteRequestState, match="executed"):
        asyncio.run(write_requests.approve(12, user_id=9))


def test_approve_loses_the_race_when_the_guarded_update_matches_nothing(monkeypatch):
    """Doppia approvazione o richiesta scaduta: la UPDATE guardata non trova righe."""
    _patch_role(monkeypatch, "owner")

    async def no_exec(record):
        raise AssertionError("must not execute when the guarded update matched nothing")

    monkeypatch.setattr(write_requests, "_run_db_execute", no_exec)
    _patch_pool(monkeypatch, FakeConn([_row(), None]))
    with pytest.raises(write_requests.WriteRequestState):
        asyncio.run(write_requests.approve(12, user_id=9))


def test_approve_dispatches_ssh_writes(monkeypatch):
    _patch_role(monkeypatch, "owner")
    seen = {}

    async def fake_ssh(record):
        seen.update(record["payload"])
        return {"path": "app.yml", "bytes_written": 11}

    monkeypatch.setattr(write_requests, "_run_ssh_write", fake_ssh)
    ssh_row = _row(
        kind="ssh_write_file", target="web1:app.yml",
        payload=json.dumps({"source": "web1", "path": "app.yml", "content": "key: value\n"}),
    )
    _patch_pool(monkeypatch, FakeConn([
        ssh_row, dict(ssh_row, status="approved"), dict(ssh_row, status="executed"),
    ]))
    out = asyncio.run(write_requests.approve(12, user_id=9))
    assert out["status"] == "executed"
    assert seen["path"] == "app.yml"


def test_reject_marks_the_request_rejected(monkeypatch):
    _patch_role(monkeypatch, "owner")
    conn = FakeConn([_row(), _row(status="rejected", decision_note="too risky")])
    _patch_pool(monkeypatch, conn)

    out = asyncio.run(write_requests.reject(12, user_id=9, note="too risky"))
    assert out["status"] == "rejected"
    sql, args = conn.calls[1]
    assert "SET status='rejected'" in sql
    assert args == (12, 9, "too risky")


def test_reject_refuses_a_decided_request(monkeypatch):
    _patch_role(monkeypatch, "owner")
    _patch_pool(monkeypatch, FakeConn([_row(status="rejected")]))
    with pytest.raises(write_requests.WriteRequestState):
        asyncio.run(write_requests.reject(12, user_id=9))


def test_scheduler_registers_the_expiry_job():
    source = inspect.getsource(scheduler)
    assert "expire_pending" in source
    assert "write_requests_expiry" in source
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_write_request_approval.py`
Expected: FAIL with `AttributeError: module 'src.write_requests' has no attribute 'approve'`.

- [ ] **Step 3: Add approval, execution and rejection to the service**

In `services/server/src/write_requests.py`, add `from . import tenancy` to the
imports at the top (next to `from .db import get_pool`), then append:

```python
async def _require_approver(record: dict[str, Any], user_id: int) -> None:
    """The approver must hold the write permission the request needs."""
    role = await tenancy.get_membership_role(record["org_id"], user_id)
    if role is None:
        raise WriteRequestForbidden("Not a member of this organization")
    if role == "owner":
        return
    permission = KIND_PERMISSION.get(record["kind"], "")
    perms = await tenancy.resolve_role_permissions(record["org_id"], role)
    if "*" not in perms and permission not in perms:
        raise WriteRequestForbidden(
            f"Approving a {record['kind']} request requires the '{permission}' permission"
        )


async def _run_db_execute(record: dict[str, Any]) -> dict[str, Any]:
    from .datasources import service

    return await service.run_write(
        record["org_id"], record["project_id"], record["target"],
        record["payload"]["sql"], source="approval",
    )


async def _run_ssh_write(record: dict[str, Any]) -> dict[str, Any]:
    from .ssh_sources import client
    from .ssh_sources import service as ssh_service

    payload = record["payload"]
    source = await ssh_service.resolve_source(
        record["org_id"], record["project_id"], payload["source"], include_secret=True
    )
    return await asyncio.to_thread(
        client.write_file, ssh_service.decrypted_conn(source),
        payload["path"], payload["content"],
    )


async def _execute(record: dict[str, Any]) -> dict[str, Any]:
    if record["kind"] == "db_execute":
        return await _run_db_execute(record)
    if record["kind"] == "ssh_write_file":
        return await _run_ssh_write(record)
    raise WriteRequestError(f"Unknown write request kind '{record['kind']}'")


async def _finish(
    request_id: int, status: str, result: Optional[dict] = None, error: Optional[str] = None
) -> dict[str, Any]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE write_requests SET status=$2, result=$3::jsonb, error=$4 "
            f"WHERE id=$1 RETURNING {_FIELDS}",
            request_id, status,
            json.dumps(result) if result is not None else None,
            error,
        )
    return _to_dict(row)


async def approve(request_id: int, user_id: int, note: str = "") -> dict[str, Any]:
    """Approve and execute a pending request under the approver's identity."""
    record = await get(request_id)
    if record is None:
        raise WriteRequestNotFound(f"Write request {request_id} not found")
    await _require_approver(record, user_id)
    if record["status"] != "pending":
        raise WriteRequestState(
            f"Write request {request_id} is '{record['status']}', not pending"
        )

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE write_requests SET status='approved', decided_by=$2, "
            f"decided_at=NOW(), decision_note=$3 "
            f"WHERE id=$1 AND status='pending' AND expires_at > NOW() "
            f"RETURNING {_FIELDS}",
            request_id, user_id, note or None,
        )
    if row is None:
        raise WriteRequestState(
            f"Write request {request_id} is no longer pending or has expired"
        )
    approved = _to_dict(row)

    try:
        result = await _execute(approved)
    except Exception as e:  # noqa: BLE001 - the failure belongs on the request
        logger.warning("Write request %s failed: %s", request_id, e)
        return await _finish(request_id, "failed", error=str(e))
    return await _finish(request_id, "executed", result=result)


async def reject(request_id: int, user_id: int, note: str = "") -> dict[str, Any]:
    record = await get(request_id)
    if record is None:
        raise WriteRequestNotFound(f"Write request {request_id} not found")
    await _require_approver(record, user_id)
    if record["status"] != "pending":
        raise WriteRequestState(
            f"Write request {request_id} is '{record['status']}', not pending"
        )
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE write_requests SET status='rejected', decided_by=$2, "
            f"decided_at=NOW(), decision_note=$3 "
            f"WHERE id=$1 AND status='pending' RETURNING {_FIELDS}",
            request_id, user_id, note or None,
        )
    if row is None:
        raise WriteRequestState(f"Write request {request_id} is no longer pending")
    return _to_dict(row)
```

Add `import asyncio` to the module imports (used by `_run_ssh_write`).

- [ ] **Step 4: Register the daily expiry job**

In `services/server/src/scheduler.py`, add this coroutine next to
`_purge_expired_oauth_flows`:

```python
async def _expire_write_requests() -> None:
    """Pending write requests must not stay approvable past their TTL."""
    from .write_requests import expire_pending

    expired = await expire_pending()
    if expired:
        logger.info("Expired %d pending write request(s)", expired)
```

and register it inside `start_scheduler()`, right after the
`oauth_flow_reaper` job and before `_scheduler.start()`:

```python
    # Daily: pending write requests past their expiry are no longer approvable.
    _scheduler.add_job(
        _expire_write_requests, CronTrigger(hour="3", minute="20"),
        id="write_requests_expiry", replace_existing=True,
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run:
`.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_write_request_approval.py tests/test_write_requests_service.py tests/test_oauth_bridge_reaper.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/server/src/write_requests.py services/server/src/scheduler.py services/server/tests/test_write_request_approval.py
git commit -m "feat: approve, execute and expire write requests"
```

---

### Task 5: REST routes for the approvals queue

**Files:**
- Create: `services/server/src/api/routes/write_requests.py`
- Modify: `services/server/src/api/app.py` (import + `include_router`)
- Test: `services/server/tests/test_write_requests_routes.py`

**Interfaces:**
- Consumes from Tasks 1 and 4 (`src.write_requests`):
  `list_for_org(org_id, status=None, limit=50, offset=0) -> {"requests", "total", "pending"}`,
  `get(request_id, org_id=None, project_id=None) -> dict | None`,
  `approve(request_id, user_id, note="") -> dict`,
  `reject(request_id, user_id, note="") -> dict`,
  `STATUSES`, `WriteRequestNotFound`, `WriteRequestForbidden`, `WriteRequestState`.
- Consumes existing code (`src.api.deps`): `ActiveOrg` (dataclass with
  `org_id: int`, `role: str`, `namespace: str`, `name: str`),
  `require_role(minimum: str)` returning a dependency that yields an `ActiveOrg`,
  and `get_current_user_id`. Org admins and owners already see every project of
  their organization (`projects.resolve_project_access` treats them as project
  superusers), so no per-project filter is needed on this router.
- Produces, used by Task 6 (the UI client):
  - `GET /api/write-requests?status=&limit=&offset=` → `{"requests": [...], "total": n, "pending": n}`
  - `GET /api/write-requests/{id}` → `{"request": {...}}`
  - `POST /api/write-requests/{id}/approve` body `{"note": ""}` → `{"status": "ok", "request": {...}}`
  - `POST /api/write-requests/{id}/reject` body `{"note": ""}` → `{"status": "ok", "request": {...}}`

- [ ] **Step 1: Write the failing test**

Create `services/server/tests/test_write_requests_routes.py`:

```python
"""Route REST della coda di approvazione: gating per ruolo e mappatura errori."""
import asyncio

import pytest
from fastapi import HTTPException

from src import write_requests as service
from src.api import deps
from src.api.routes import write_requests as routes


def _org(role="admin"):
    return deps.ActiveOrg(org_id=1, role=role, namespace="org-ns", name="Org")


def _record(**over):
    record = {
        "id": 12, "org_id": 1, "project_id": 2, "kind": "db_execute",
        "status": "pending", "target": "erp",
        "payload": {"sql": "UPDATE t SET a=1 WHERE id=1"},
        "preview": {"plan_rows": 3}, "reason": "fix a bad row",
        "requested_by_kind": "api_key", "requested_by_id": 5, "requested_by": "ci-bot",
        "decided_by": None, "decided_at": None, "decision_note": None,
        "result": None, "error": None,
        "created_at": "2026-09-07T10:00:00+00:00",
        "expires_at": "2026-09-14T10:00:00+00:00",
    }
    record.update(over)
    return record


def test_list_passes_the_filters_through(monkeypatch):
    seen = {}

    async def fake_list(org_id, status=None, limit=50, offset=0):
        seen.update(org_id=org_id, status=status, limit=limit, offset=offset)
        return {"requests": [_record()], "total": 1, "pending": 1}

    monkeypatch.setattr(routes.service, "list_for_org", fake_list)
    out = asyncio.run(routes.list_requests(status="pending", limit=25, offset=10, org=_org()))
    assert seen == {"org_id": 1, "status": "pending", "limit": 25, "offset": 10}
    assert out["pending"] == 1
    assert out["requests"][0]["id"] == 12


def test_list_rejects_an_unknown_status():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.list_requests(status="bogus", limit=50, offset=0, org=_org()))
    assert exc.value.status_code == 422


def test_get_returns_the_request(monkeypatch):
    async def fake_get(request_id, org_id=None, project_id=None):
        assert (request_id, org_id) == (12, 1)
        return _record()

    monkeypatch.setattr(routes.service, "get", fake_get)
    out = asyncio.run(routes.get_request(request_id=12, org=_org()))
    assert out["request"]["target"] == "erp"


def test_get_of_another_org_is_404(monkeypatch):
    async def fake_get(request_id, org_id=None, project_id=None):
        return None

    monkeypatch.setattr(routes.service, "get", fake_get)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.get_request(request_id=12, org=_org()))
    assert exc.value.status_code == 404


def test_approve_returns_the_executed_request(monkeypatch):
    async def fake_get(request_id, org_id=None, project_id=None):
        return _record()

    async def fake_approve(request_id, user_id, note=""):
        assert (request_id, user_id, note) == (12, 7, "ok by me")
        return _record(status="executed", result={"row_count": 1})

    monkeypatch.setattr(routes.service, "get", fake_get)
    monkeypatch.setattr(routes.service, "approve", fake_approve)
    out = asyncio.run(routes.approve_request(
        request_id=12, req=routes.DecisionRequest(note="ok by me"), org=_org(), user_id=7))
    assert out["status"] == "ok"
    assert out["request"]["status"] == "executed"


def test_approve_without_the_permission_is_403(monkeypatch):
    async def fake_get(request_id, org_id=None, project_id=None):
        return _record()

    async def forbidden(request_id, user_id, note=""):
        raise service.WriteRequestForbidden("requires the 'db-write' permission")

    monkeypatch.setattr(routes.service, "get", fake_get)
    monkeypatch.setattr(routes.service, "approve", forbidden)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.approve_request(
            request_id=12, req=routes.DecisionRequest(), org=_org(), user_id=7))
    assert exc.value.status_code == 403


def test_approve_of_a_decided_request_is_409(monkeypatch):
    async def fake_get(request_id, org_id=None, project_id=None):
        return _record(status="executed")

    async def bad_state(request_id, user_id, note=""):
        raise service.WriteRequestState("not pending")

    monkeypatch.setattr(routes.service, "get", fake_get)
    monkeypatch.setattr(routes.service, "approve", bad_state)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.approve_request(
            request_id=12, req=routes.DecisionRequest(), org=_org(), user_id=7))
    assert exc.value.status_code == 409


def test_approve_of_another_org_is_404(monkeypatch):
    async def fake_get(request_id, org_id=None, project_id=None):
        return None

    monkeypatch.setattr(routes.service, "get", fake_get)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.approve_request(
            request_id=12, req=routes.DecisionRequest(), org=_org(), user_id=7))
    assert exc.value.status_code == 404


def test_reject_returns_the_rejected_request(monkeypatch):
    async def fake_get(request_id, org_id=None, project_id=None):
        return _record()

    async def fake_reject(request_id, user_id, note=""):
        return _record(status="rejected", decision_note=note)

    monkeypatch.setattr(routes.service, "get", fake_get)
    monkeypatch.setattr(routes.service, "reject", fake_reject)
    out = asyncio.run(routes.reject_request(
        request_id=12, req=routes.DecisionRequest(note="too risky"), org=_org(), user_id=7))
    assert out["request"]["status"] == "rejected"
    assert out["request"]["decision_note"] == "too risky"


def test_member_is_denied_by_the_role_dependency():
    """require_role('admin') è il gate: un member non passa."""
    checker = deps.require_role("admin")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(checker(org=_org(role="member")))
    assert exc.value.status_code == 403


def test_router_is_mounted_on_the_api():
    from src.api.app import api

    paths = {r.path for r in api.routes}
    assert "/api/write-requests" in paths
    assert "/api/write-requests/{request_id}/approve" in paths
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_write_requests_routes.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.api.routes.write_requests'`.

- [ ] **Step 3: Write the routes module**

Create `services/server/src/api/routes/write_requests.py`:

```python
"""REST API for the approval queue of agent-proposed writes (org admin/owner)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ... import write_requests as service
from ..deps import ActiveOrg, get_current_user_id, require_role

router = APIRouter(prefix="/write-requests", tags=["write-requests"])


class DecisionRequest(BaseModel):
    note: str = ""


@router.get("")
async def list_requests(
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    org: ActiveOrg = Depends(require_role("admin")),
):
    if status is not None and status not in service.STATUSES:
        raise HTTPException(status_code=422, detail=f"Unknown status '{status}'")
    # Admins and owners already see every project of the organization.
    return await service.list_for_org(org.org_id, status=status, limit=limit, offset=offset)


@router.get("/{request_id}")
async def get_request(
    request_id: int, org: ActiveOrg = Depends(require_role("admin"))
):
    record = await service.get(request_id, org_id=org.org_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Write request not found")
    return {"request": record}


def _decision_error(exc: Exception) -> HTTPException:
    if isinstance(exc, service.WriteRequestForbidden):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, service.WriteRequestNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=409, detail=str(exc))


@router.post("/{request_id}/approve")
async def approve_request(
    request_id: int,
    req: DecisionRequest,
    org: ActiveOrg = Depends(require_role("admin")),
    user_id: int = Depends(get_current_user_id),
):
    if await service.get(request_id, org_id=org.org_id) is None:
        raise HTTPException(status_code=404, detail="Write request not found")
    try:
        record = await service.approve(request_id, user_id, req.note)
    except service.WriteRequestError as e:
        raise _decision_error(e) from e
    return {"status": "ok", "request": record}


@router.post("/{request_id}/reject")
async def reject_request(
    request_id: int,
    req: DecisionRequest,
    org: ActiveOrg = Depends(require_role("admin")),
    user_id: int = Depends(get_current_user_id),
):
    if await service.get(request_id, org_id=org.org_id) is None:
        raise HTTPException(status_code=404, detail="Write request not found")
    try:
        record = await service.reject(request_id, user_id, req.note)
    except service.WriteRequestError as e:
        raise _decision_error(e) from e
    return {"status": "ok", "request": record}
```

- [ ] **Step 4: Mount the router**

In `services/server/src/api/app.py`, add the import next to the other route
imports:

```python
from .routes import write_requests as write_requests_routes
```

and the inclusion next to the other `include_router` calls:

```python
api.include_router(write_requests_routes.router, prefix="/api")
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_write_requests_routes.py`
Expected: PASS (11 tests).

- [ ] **Step 6: Commit**

```bash
git add services/server/src/api/routes/write_requests.py services/server/src/api/app.py services/server/tests/test_write_requests_routes.py
git commit -m "feat: expose the write approval queue over REST"
```

---

### Task 6: Approvals page, nav badge and API client

**Files:**
- Create: `services/ui/src/lib/approvals.ts`
- Create: `services/ui/src/lib/approvals.test.ts`
- Create: `services/ui/src/pages/Approvals.tsx`
- Modify: `services/ui/src/lib/api.ts` (types + `writeRequests` client)
- Modify: `services/ui/src/store/index.ts` (`pendingApprovals`, `loadPendingApprovals`)
- Modify: `services/ui/src/App.tsx` (nav entry with badge, route)

**Interfaces:**
- Consumes from Task 5 (REST, all with the standard tenancy headers already
  added by `request()` in `lib/api.ts`):
  - `GET /api/write-requests?status=&limit=&offset=` → `{ requests, total, pending }`
  - `POST /api/write-requests/{id}/approve` body `{ note }` → `{ status, request }`
  - `POST /api/write-requests/{id}/reject` body `{ note }` → `{ status, request }`
- Consumes existing UI code:
  - `useAppStore` exposes `organizations: Organization[]` (each with `id`,
    `name`, `role`) and `activeOrgId: number | null`; the role of the active org
    is read as
    `useAppStore((s) => s.organizations.find((o) => o.id === s.activeOrgId)?.role)`
    (the pattern in `pages/SshSources.tsx`).
  - Kit from `../components/ui`: `Badge` (variants `default | success | warning
    | danger | accent | muted`), `Banner`, `Button` (`variant`, `size`,
    `loading`), `Card`, `Dialog` + `DialogFooter`, `Select` (`value`,
    `onValueChange`, `options: {value,label}[]`, `label`), `Table, Thead, Tbody,
    Tr, Th, Td`, `Textarea` (`label`, `value`, `onChange`), `useToast`.
- Produces:
  - `pendingBadge(count: number): string | null`
  - `formatAge(iso: string, now?: number): string`
  - `isExpired(iso: string, now?: number): boolean`
  - `api.writeRequests.list/get/approve/reject`
  - store fields `pendingApprovals: number` and `loadPendingApprovals(): Promise<void>`

- [ ] **Step 1: Write the failing test**

Create `services/ui/src/lib/approvals.test.ts`:

```ts
import { describe, it, expect } from 'vitest'
import { pendingBadge, formatAge, isExpired } from './approvals'

describe('pendingBadge', () => {
  it('is null when nothing is pending', () => {
    expect(pendingBadge(0)).toBeNull()
  })

  it('shows the exact count up to nine', () => {
    expect(pendingBadge(1)).toBe('1')
    expect(pendingBadge(9)).toBe('9')
  })

  it('caps at 9+', () => {
    expect(pendingBadge(10)).toBe('9+')
    expect(pendingBadge(250)).toBe('9+')
  })

  it('ignores negative or non-finite counts', () => {
    expect(pendingBadge(-3)).toBeNull()
    expect(pendingBadge(Number.NaN)).toBeNull()
  })
})

describe('formatAge', () => {
  const now = Date.parse('2026-09-07T12:00:00Z')

  it('reports fresh requests as just now', () => {
    expect(formatAge('2026-09-07T11:59:30Z', now)).toBe('just now')
  })

  it('reports minutes', () => {
    expect(formatAge('2026-09-07T11:20:00Z', now)).toBe('40m ago')
  })

  it('reports hours', () => {
    expect(formatAge('2026-09-07T04:00:00Z', now)).toBe('8h ago')
  })

  it('reports days', () => {
    expect(formatAge('2026-09-04T12:00:00Z', now)).toBe('3d ago')
  })

  it('handles an unparsable timestamp', () => {
    expect(formatAge('not-a-date', now)).toBe('unknown')
  })
})

describe('isExpired', () => {
  const now = Date.parse('2026-09-07T12:00:00Z')

  it('is true once the expiry has passed', () => {
    expect(isExpired('2026-09-07T11:59:59Z', now)).toBe(true)
  })

  it('is false while it is in the future', () => {
    expect(isExpired('2026-09-14T12:00:00Z', now)).toBe(false)
  })

  it('is false for an unparsable timestamp', () => {
    expect(isExpired('not-a-date', now)).toBe(false)
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run from `services/ui`: `npx vitest run src/lib/approvals.test.ts`
Expected: FAIL — cannot resolve `./approvals`.

- [ ] **Step 3: Write the helpers**

Create `services/ui/src/lib/approvals.ts`:

```ts
export function pendingBadge(count: number): string | null {
  if (!Number.isFinite(count) || count <= 0) return null
  return count > 9 ? '9+' : String(count)
}

export function formatAge(iso: string, now: number = Date.now()): string {
  const at = Date.parse(iso)
  if (Number.isNaN(at)) return 'unknown'
  const minutes = Math.floor((now - at) / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}

export function isExpired(iso: string, now: number = Date.now()): boolean {
  const at = Date.parse(iso)
  if (Number.isNaN(at)) return false
  return at <= now
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx vitest run src/lib/approvals.test.ts`
Expected: PASS (12 tests).

- [ ] **Step 5: Add the API client**

In `services/ui/src/lib/api.ts`, add the types next to the other exported
interfaces (e.g. above the `export const api = {` object):

```ts
export type WriteRequestKind = 'db_execute' | 'ssh_write_file'
export type WriteRequestStatus =
  | 'pending' | 'approved' | 'rejected' | 'executed' | 'failed' | 'expired'

export interface WriteRequest {
  id: number
  org_id: number
  project_id: number
  kind: WriteRequestKind
  status: WriteRequestStatus
  target: string
  payload: Record<string, unknown>
  preview: Record<string, unknown>
  reason: string | null
  requested_by_kind: string
  requested_by_id: number | null
  requested_by: string
  decided_by: number | null
  decided_at: string | null
  decision_note: string | null
  result: Record<string, unknown> | null
  error: string | null
  created_at: string
  expires_at: string
}

export interface WriteRequestList {
  requests: WriteRequest[]
  total: number
  pending: number
}
```

and this namespace inside the `api` object, next to `organizations`:

```ts
  writeRequests: {
    list: (opts?: { status?: string; limit?: number; offset?: number }) => {
      const params = new URLSearchParams()
      if (opts?.status) params.set('status', opts.status)
      if (opts?.limit !== undefined) params.set('limit', String(opts.limit))
      if (opts?.offset !== undefined) params.set('offset', String(opts.offset))
      const qs = params.toString()
      return request<WriteRequestList>(`/api/write-requests${qs ? `?${qs}` : ''}`)
    },
    get: (id: number) => request<{ request: WriteRequest }>(`/api/write-requests/${id}`),
    approve: (id: number, note = '') =>
      request<{ status: string; request: WriteRequest }>(
        `/api/write-requests/${id}/approve`,
        { method: 'POST', body: JSON.stringify({ note }) }
      ),
    reject: (id: number, note = '') =>
      request<{ status: string; request: WriteRequest }>(
        `/api/write-requests/${id}/reject`,
        { method: 'POST', body: JSON.stringify({ note }) }
      ),
  },
```

- [ ] **Step 6: Add the pending count to the store**

In `services/ui/src/store/index.ts`:

Add to the `AppStore` interface, after `activeProjectId: number | null`:

```ts
  pendingApprovals: number
  loadPendingApprovals: () => Promise<void>
```

Add to the initial state, after `activeProjectId: getActiveProjectId(),`:

```ts
  pendingApprovals: 0,
```

Add the action after `loadProjects`:

```ts
  loadPendingApprovals: async () => {
    const { organizations, activeOrgId } = get()
    const role = organizations.find((o) => o.id === activeOrgId)?.role
    if (role !== 'admin' && role !== 'owner') {
      set({ pendingApprovals: 0 })
      return
    }
    try {
      const res = await api.writeRequests.list({ status: 'pending', limit: 1 })
      set({ pendingApprovals: res.pending })
    } catch {
      // best-effort: the badge simply stays at zero
      set({ pendingApprovals: 0 })
    }
  },
```

In `loadIdentity`, after `await get().loadProjects()`, add:

```ts
    await get().loadPendingApprovals()
```

In `logout`, add `pendingApprovals: 0` to the `set({ ... })` call that clears
the session state.

- [ ] **Step 7: Write the Approvals page**

Create `services/ui/src/pages/Approvals.tsx`:

```tsx
import { useCallback, useEffect, useState } from 'react'
import { ShieldCheck, Database, FileText } from 'lucide-react'
import { api, type WriteRequest, type WriteRequestStatus } from '../lib/api'
import { formatAge, isExpired } from '../lib/approvals'
import { useAppStore } from '../store'
import {
  Badge, Banner, Button, Card, Dialog, DialogFooter, Select,
  Table, Tbody, Td, Textarea, Th, Thead, Tr, useToast,
} from '../components/ui'

const STATUS_OPTIONS = [
  { value: 'pending', label: 'Pending' },
  { value: 'approved', label: 'Approved' },
  { value: 'executed', label: 'Executed' },
  { value: 'failed', label: 'Failed' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'expired', label: 'Expired' },
  { value: 'all', label: 'All' },
]

function statusBadge(status: WriteRequestStatus) {
  if (status === 'pending') return <Badge variant="warning">Pending</Badge>
  if (status === 'executed') return <Badge variant="success">Executed</Badge>
  if (status === 'failed') return <Badge variant="danger">Failed</Badge>
  if (status === 'rejected') return <Badge variant="danger">Rejected</Badge>
  if (status === 'expired') return <Badge variant="muted">Expired</Badge>
  return <Badge variant="accent">Approved</Badge>
}

function KindIcon({ kind }: { kind: WriteRequest['kind'] }) {
  return kind === 'db_execute'
    ? <Database className="w-3.5 h-3.5 text-muted" />
    : <FileText className="w-3.5 h-3.5 text-muted" />
}

function summary(req: WriteRequest): string {
  if (req.kind === 'db_execute') return String(req.payload.sql ?? '')
  const preview = req.preview as { is_new?: boolean; content_bytes?: number }
  return `${preview.is_new ? 'New file' : 'Modified file'} — ${preview.content_bytes ?? 0} bytes`
}

function DetailDrawer({
  request, onClose, onDecided,
}: {
  request: WriteRequest
  onClose: () => void
  onDecided: () => void
}) {
  const toast = useToast()
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState<'approve' | 'reject' | null>(null)
  const preview = request.preview as Record<string, unknown>
  const expired = isExpired(request.expires_at)

  const decide = async (action: 'approve' | 'reject') => {
    setBusy(action)
    try {
      const res = action === 'approve'
        ? await api.writeRequests.approve(request.id, note)
        : await api.writeRequests.reject(request.id, note)
      toast.success(`Request ${res.request.status}`)
      onDecided()
      onClose()
    } catch (e) {
      toast.error(String(e))
    } finally {
      setBusy(null)
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`${request.kind === 'db_execute' ? 'SQL write' : 'File write'} — ${request.target}`}
      description={`Proposed by ${request.requested_by} (${request.requested_by_kind}) ${formatAge(request.created_at)}`}
      maxWidth="720px"
    >
      <div className="space-y-4">
        {request.reason && (
          <div>
            <div className="text-xs font-semibold uppercase tracking-wide text-muted">Reason</div>
            <p className="text-sm">{request.reason}</p>
          </div>
        )}

        {request.kind === 'db_execute' ? (
          <div className="space-y-2">
            <div className="text-xs font-semibold uppercase tracking-wide text-muted">Statement</div>
            <Card className="p-3 overflow-x-auto">
              <pre className="text-xs font-mono whitespace-pre-wrap">{String(request.payload.sql ?? '')}</pre>
            </Card>
            <div className="text-sm text-muted">
              Estimated rows: {preview.plan_rows === null || preview.plan_rows === undefined
                ? 'unknown'
                : String(preview.plan_rows)}
            </div>
            {typeof preview.explain_error === 'string' && (
              <Banner variant="warning">EXPLAIN unavailable: {preview.explain_error}</Banner>
            )}
            {typeof preview.plan_text === 'string' && preview.plan_text !== '' && (
              <Card className="p-3 overflow-x-auto">
                <pre className="text-xs font-mono whitespace-pre-wrap">{preview.plan_text}</pre>
              </Card>
            )}
          </div>
        ) : (
          <div className="space-y-2">
            <div className="text-xs font-semibold uppercase tracking-wide text-muted">File</div>
            <Table>
              <Tbody>
                <Tr><Td className="text-muted">Path</Td><Td className="font-mono">{String(preview.path ?? '')}</Td></Tr>
                <Tr><Td className="text-muted">Change</Td><Td>{preview.is_new ? 'New file' : 'Overwrite'}</Td></Tr>
                <Tr><Td className="text-muted">New size</Td><Td>{String(preview.content_bytes ?? 0)} bytes</Td></Tr>
                <Tr><Td className="text-muted">New sha256</Td><Td className="font-mono text-xs break-all">{String(preview.content_sha256 ?? '')}</Td></Tr>
                <Tr><Td className="text-muted">Current size</Td><Td>{preview.existing_size === null || preview.existing_size === undefined ? '—' : `${String(preview.existing_size)} bytes`}</Td></Tr>
                <Tr><Td className="text-muted">Current sha256</Td><Td className="font-mono text-xs break-all">{String(preview.existing_sha256 ?? '—')}</Td></Tr>
              </Tbody>
            </Table>
            <Card className="p-3 max-h-64 overflow-auto">
              <pre className="text-xs font-mono whitespace-pre-wrap">{String(request.payload.content ?? '')}</pre>
            </Card>
          </div>
        )}

        {request.status === 'executed' && request.result && (
          <Banner variant="success">
            Executed: {JSON.stringify(request.result)}
          </Banner>
        )}
        {request.status === 'failed' && request.error && (
          <Banner variant="danger">Failed: {request.error}</Banner>
        )}
        {request.decision_note && (
          <div className="text-sm text-muted">Decision note: {request.decision_note}</div>
        )}

        {request.status === 'pending' && !expired && (
          <Textarea
            label="Note (optional)"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={2}
          />
        )}
        {request.status === 'pending' && expired && (
          <Banner variant="warning">This request has expired and can no longer be approved.</Banner>
        )}
      </div>

      <DialogFooter>
        <Button variant="ghost" onClick={onClose}>Close</Button>
        {request.status === 'pending' && !expired && (
          <>
            <Button
              variant="danger"
              loading={busy === 'reject'}
              onClick={() => decide('reject')}
            >
              Reject
            </Button>
            <Button
              variant="primary"
              loading={busy === 'approve'}
              onClick={() => decide('approve')}
            >
              Approve and run
            </Button>
          </>
        )}
      </DialogFooter>
    </Dialog>
  )
}

export default function Approvals() {
  const toast = useToast()
  const role = useAppStore((s) => s.organizations.find((o) => o.id === s.activeOrgId)?.role)
  const loadPendingApprovals = useAppStore((s) => s.loadPendingApprovals)
  const canApprove = role === 'admin' || role === 'owner'
  const [status, setStatus] = useState('pending')
  const [requests, setRequests] = useState<WriteRequest[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<WriteRequest | null>(null)

  const load = useCallback(async () => {
    if (!canApprove) {
      setLoading(false)
      return
    }
    setLoading(true)
    try {
      const res = await api.writeRequests.list({
        status: status === 'all' ? undefined : status,
        limit: 100,
      })
      setRequests(res.requests)
      setTotal(res.total)
    } catch (e) {
      toast.error(String(e))
    } finally {
      setLoading(false)
    }
  }, [canApprove, status, toast])

  useEffect(() => {
    void load()
  }, [load])

  const afterDecision = async () => {
    await load()
    await loadPendingApprovals()
  }

  if (!canApprove) {
    return (
      <div className="p-4 sm:p-8">
        <div className="page-wide">
          <Banner variant="info">
            Only organization admins and owners can review write requests.
          </Banner>
        </div>
      </div>
    )
  }

  return (
    <div className="p-4 sm:p-8">
      <div className="page-wide space-y-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1>Approvals</h1>
            <p className="text-sm text-muted">
              SQL statements and file writes proposed by agents that lack the write
              permission. Approving runs the change under your identity.
            </p>
          </div>
          <div className="page-header-actions">
            <Select
              value={status}
              onValueChange={setStatus}
              options={STATUS_OPTIONS}
              className="w-40"
            />
          </div>
        </div>

        {loading ? (
          <p className="text-sm text-muted">Loading…</p>
        ) : requests.length === 0 ? (
          <div className="rounded border border-dashed border-border p-8 text-center">
            <ShieldCheck className="w-6 h-6 mx-auto text-muted" />
            <p className="mt-2 text-sm text-muted">Nothing to review here.</p>
          </div>
        ) : (
          <>
            <Table>
              <Thead>
                <Tr>
                  <Th>Target</Th>
                  <Th>Change</Th>
                  <Th>Requested by</Th>
                  <Th>Age</Th>
                  <Th>Status</Th>
                  <Th className="w-24" />
                </Tr>
              </Thead>
              <Tbody>
                {requests.map((r) => (
                  <Tr key={r.id}>
                    <Td>
                      <span className="inline-flex items-center gap-1.5 font-medium">
                        <KindIcon kind={r.kind} /> {r.target}
                      </span>
                    </Td>
                    <Td className="font-mono text-xs max-w-md truncate">{summary(r)}</Td>
                    <Td className="text-sm">{r.requested_by}</Td>
                    <Td className="text-sm text-muted">
                      {formatAge(r.created_at)}
                      {r.status === 'pending' && isExpired(r.expires_at) && (
                        <Badge variant="muted" className="ml-2">expired</Badge>
                      )}
                    </Td>
                    <Td>{statusBadge(r.status)}</Td>
                    <Td>
                      <div className="flex justify-end">
                        <Button size="sm" variant="secondary" onClick={() => setSelected(r)}>
                          Review
                        </Button>
                      </div>
                    </Td>
                  </Tr>
                ))}
              </Tbody>
            </Table>
            <p className="text-xs text-muted">{total} request(s) in this view.</p>
          </>
        )}

        {selected && (
          <DetailDrawer
            request={selected}
            onClose={() => setSelected(null)}
            onDecided={afterDecision}
          />
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 8: Add the nav entry with the badge and the route**

In `services/ui/src/App.tsx`:

Import the page and the badge helper next to the other page imports:

```tsx
import Approvals from './pages/Approvals'
import { pendingBadge } from './lib/approvals'
```

Add `ShieldCheck` to the existing `lucide-react` import list.

Give `navLinks` an optional admin flag and add the entry after `/ssh-sources`:

```tsx
const navLinks: { to: string; icon: typeof GitBranch; label: string; adminOnly?: boolean }[] = [
  { to: '/chat', icon: MessagesSquare, label: 'Agent Chat' },
  { to: '/repos', icon: GitBranch, label: 'Repositories' },
  { to: '/datasources', icon: Database, label: 'Data Sources' },
  { to: '/ssh-sources', icon: TerminalSquare, label: 'SSH Files' },
  { to: '/approvals', icon: ShieldCheck, label: 'Approvals', adminOnly: true },
  { to: '/contracts', icon: Braces, label: 'API Contracts' },
  { to: '/knowledge', icon: Library, label: 'Knowledge Base' },
  { to: '/web', icon: Globe, label: 'Web Pages' },
  { to: '/memory', icon: Brain, label: 'Memory' },
  { to: '/environments', icon: Server, label: 'Environments' },
  { to: '/settings', icon: SlidersHorizontal, label: 'Settings' },
  { to: '/tools', icon: Wrench, label: 'MCP Tools' },
  //{ to: '/jobs', icon: Activity, label: 'Async Jobs' },
]
```

In `NavItems`, gate the admin entries and render the badge. Replace the `<nav>`
block with:

```tsx
  const role = useAppStore((s) => s.organizations.find((o) => o.id === s.activeOrgId)?.role)
  const pendingApprovals = useAppStore((s) => s.pendingApprovals)
  const isOrgAdmin = role === 'admin' || role === 'owner'
  const badge = pendingBadge(pendingApprovals)
  return (
    <>
      <nav className="flex-1 py-2 px-2 overflow-y-auto scrollbar-thin">
        {navLinks
          .filter((link) => !link.adminOnly || isOrgAdmin)
          .map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              onClick={onNavigate}
              className={({ isActive }) =>
                [
                  'flex items-center gap-2.5 px-3 h-10 my-0.5 text-sm rounded border transition-colors',
                  isActive
                    ? 'text-white border-primary bg-[rgba(124,201,242,0.12)]'
                    : 'text-sidebar-text border-transparent hover:text-white',
                ].join(' ')
              }
            >
              <Icon className="w-3.5 h-3.5 flex-shrink-0" />
              <span className="flex-1">{label}</span>
              {to === '/approvals' && badge && (
                <span className="px-1.5 py-0.5 text-xs font-medium rounded bg-primary text-on-primary">
                  {badge}
                </span>
              )}
            </NavLink>
          ))}
      </nav>
```

Keep the rest of `NavItems` (the `showLogout` block and closing tags) unchanged;
the existing `const logout = useAppStore((s) => s.logout)` line stays at the top
of the component.

Add the route inside `<Routes>`, next to `/ssh-sources`:

```tsx
                <Route path="/approvals" element={<Approvals />} />
```

- [ ] **Step 9: Run the UI checks**

Run from `services/ui`:

```bash
npx tsc --noEmit
npx vitest run
npm run build
```

Expected: no TypeScript errors, all vitest suites pass (including
`src/lib/tenancy.test.ts` and the new `src/lib/approvals.test.ts`), build succeeds.

- [ ] **Step 10: Commit**

```bash
git add services/ui/src/lib/approvals.ts services/ui/src/lib/approvals.test.ts services/ui/src/pages/Approvals.tsx services/ui/src/lib/api.ts services/ui/src/store/index.ts services/ui/src/App.tsx
git commit -m "feat: review write requests from the UI"
```

---

### Task 7: README and full verification

**Files:**
- Modify: `README.md` (Features, MCP tools, Security)
- Verify: whole server suite and the UI checks

**Interfaces:**
- Consumes: everything the previous tasks produced — the tool
  `write_request_status`, the `Approvals` page, the approval semantics
  (approving requires the write permission the request needs).
- Produces: no code.

- [ ] **Step 1: Update the Features bullets**

In `README.md`, in the `## Features` list, replace the last sentence of the
**Data sources** bullet:

Before:
> With the `db-write` permission, `db_execute` runs a single validated DML statement (real top-level WHERE required) inside a transaction with a DB-side timeout.

After:
> With the `db-write` permission, `db_execute` runs a single validated DML statement (real top-level WHERE required) inside a transaction with a DB-side timeout; without it, a caller holding `context-write` gets a **proposal** instead — the statement is stored with an `EXPLAIN` preview for an admin to approve from the Approvals page.

And replace the **SSH file sources** bullet:

Before:
> - **SSH file sources** — register a host and a root directory reachable over SSH; agents list, read (head/tail/offset windows), and grep files there, and with `ssh-write` can atomically write files confined to that root.

After:
> - **SSH file sources** — register a host and a root directory reachable over SSH; agents list, read (head/tail/offset windows), and grep files there, and with `ssh-write` can atomically write files confined to that root. Without `ssh-write`, a caller holding `context-write` proposes the write instead: the content is stored with a size/hash preview for an admin to approve.

- [ ] **Step 2: Update the MCP tools list**

In `## MCP tools`, replace the Data sources and SSH files lines:

```markdown
- **Data sources:** `db_list`, `db_schema`, `db_describe`, `db_query`, `db_execute` (`db-write`, otherwise a proposal), `db_add` (`sources-write`)
- **SSH files:** `ssh_sources`, `ssh_list_files`, `ssh_read_file`, `ssh_grep`, `ssh_write_file` (`ssh-write`, otherwise a proposal), `ssh_source_add` (`sources-write`)
```

and add a new bullet after the SSH files one:

```markdown
- **Write approvals:** `write_request_status` — check whether a proposed write was approved, rejected, or executed
```

- [ ] **Step 3: Add the approval flow to the Security section**

In `## Security`, immediately before the `### MCP permissions` subsection, add:

```markdown
### Human approvals for writes

`db_execute` and `ssh_write_file` execute immediately only for a caller holding
`db-write` / `ssh-write`. A caller holding `context-write` but not the write
permission does not execute: the tool stores a **write request** — the statement
or the file content, plus a preview (estimated rows and plan excerpt for SQL,
sizes and sha256 hashes for files) — and returns `pending_approval` with a
request id to poll via `write_request_status`. A caller with neither permission
is denied as before.

Organization admins and owners review the queue under **Approvals** in the
dashboard. Approving requires the same write permission the request needs
(owners always qualify), and the change then runs under the approver's identity.
Pending requests expire after 7 days; a daily job marks them `expired`.
```

- [ ] **Step 4: Update the permissions table note**

In `### MCP permissions`, replace the `db-write` and `ssh-read / ssh-write` rows:

```markdown
| `db-write` | `db_execute` (validated single-statement DML) and approving SQL write requests |
```

```markdown
| `ssh-read` / `ssh-write` | read / write files on SSH sources; `ssh-write` also approves file write requests |
```

and append one sentence to the "Defaults:" paragraph, after
"Write capabilities (`db-write`, `ssh-write`, `repo-write`) are owner-only until granted explicitly.":

> Members holding `context-write` can still *propose* SQL and file writes; an admin with the matching write permission approves them under **Approvals**.

- [ ] **Step 5: Run the full server suite**

Run from `services/server`:

```bash
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests
```

Expected: PASS, no failures and no errors. If anything fails, fix it before
committing — in particular `tests/test_db_execute_surfaces.py`,
`tests/test_ssh_write_file.py`, `tests/test_permissions.py`,
`tests/test_tool_gating.py` and `tests/test_role_permissions.py`, which pin
behaviour this feature touches.

- [ ] **Step 6: Run the full UI checks**

Run from `services/ui`:

```bash
npx tsc --noEmit
npm run build
npx vitest run
```

Expected: no TypeScript errors, a successful build, all vitest suites green.

- [ ] **Step 7: Commit**

```bash
git add README.md
git commit -m "docs: document the write approval flow"
```

---

## Self-Review

**1. Spec coverage**

| Spec requirement | Task |
|---|---|
| Caller with the write permission executes immediately, as today | 3 (acceptance tests `test_db_execute_direct_path_is_unchanged`, `test_ssh_write_file_direct_path_is_unchanged`) |
| Caller with only `context-write` gets `pending_approval` with `request_id`, `preview`, `message` | 3 (`pending_response`, asserted verbatim) |
| Caller with neither permission is denied as today | 3 (`requires_permission` primary permission unchanged in the message) |
| `write_request_status(request_id)` gated with `context-read`, scoped to org/project | 3 |
| SQL preview: `EXPLAIN (FORMAT JSON)` through the read-only executor, rolled back; `plan_rows`, `plan_text` (2000 chars), `statement`; validation first | 2 (preview) and 3 (`validate_write_query` before `sql_preview`) |
| File preview: `path`, `content` (max 512 KiB else refused), `content_sha256`, `existing_sha256`, `existing_size`, `is_new` | 2 (`file_preview`) and 3 (content in `payload`) |
| Migration `0004_write_requests` with the given DDL and index | 1 |
| Both tools gain optional `reason: str = ""` stored on the request | 3 |
| Service `src/write_requests.py` with `create`, `get`, `list_for_org`, `approve`, `reject`, `expire_pending` | 1 (first four) and 4 (approve/reject) |
| `approve`: approver holds the needed write permission via `resolve_role_permissions` (owner always) | 4 (`_require_approver`) |
| `approve`: status must be pending and not expired | 4 (in-Python check plus the guarded `UPDATE ... AND status='pending' AND expires_at > NOW()`) |
| `approve`: set approved, execute with the existing executors, then executed/failed | 4 (`_run_db_execute`, `_run_ssh_write`, `_finish`) |
| Execution under the approver's identity | 4 (`decided_by` is the approver; `run_write` is called from the REST request of that user, `source="approval"`) |
| Scheduler daily `expire_pending` | 4 |
| REST: list with filters and counts, get, approve, reject; org admin/owner | 5 |
| UI: Approvals page and route, admin/owner only, nav badge with the pending count, status filter, detail with SQL or file diff summary, Approve/Reject with a note, executed result shown | 6 |
| Tests without a live DB for dispatch, previews, approve, state machine, scoping, REST gating, pending count, UI badge logic in `lib/` | 1–6 |
| README: features, tools list, security section | 7 |

No spec requirement is left without a task.

**2. Placeholder scan** — no "TBD", no "similar to Task N", no "add error
handling"; every code step carries the real code, every test step the real test.

**3. Type consistency** — names used across tasks match their definitions:
`create`/`get`/`list_for_org`/`approve`/`reject`/`expire_pending`,
`WriteRequestNotFound`/`WriteRequestForbidden`/`WriteRequestState`,
`KIND_PERMISSION`/`STATUSES`, `sql_preview`/`file_preview`/`extract_plan`/
`explain_statement`/`MAX_PROPOSAL_CONTENT_BYTES`/`PLAN_TEXT_CHARS`,
`has_permission`/`requires_permission(..., alternatives=...)`,
`current_requester`/`pending_response`, `pendingBadge`/`formatAge`/`isExpired`,
`api.writeRequests.list|get|approve|reject`, store `pendingApprovals`/
`loadPendingApprovals`. The record key for the request's own state is `status`
in the service and REST, and `state` in the `write_request_status` tool reply
(where `status` is reserved for the tool-level `ok`/`error` convention).
