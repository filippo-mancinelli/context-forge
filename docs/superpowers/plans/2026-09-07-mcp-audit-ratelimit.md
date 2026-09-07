# MCP tool audit and per-key rate limits — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record every MCP tool call in an auditable table with the calling principal, outcome and duration, and enforce an optional per-API-key rate limit, both surfaced in the UI.

**Architecture:** A `Principal` context var is populated by the MCP auth middleware and read by an `audited()` async context manager that the `requires_permission` decorator (and a new `audit_only` decorator) wraps around every tool. Rows are enqueued on an in-process `asyncio.Queue` and flushed in batches by a background writer task, so the tool path never waits on the database. A sliding-window in-memory limiter rejects api-key callers over their limit; a daily scheduler job prunes old rows; REST routes and an Organization tab expose the trail.

**Tech Stack:** Python 3.11 (dev 3.14), asyncpg raw SQL, FastMCP 3.x, FastAPI, APScheduler, prometheus-client, React 18 + TypeScript + Vite + Tailwind.

**Spec:** `docs/superpowers/specs/2026-09-07-mcp-audit-ratelimit-design.md`

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

Feature-specific constraints:

- This feature adds **no** new MCP permission to `PERMISSIONS`.
- This feature adds **no** new Python dependency: `prometheus-client` already
  arrived with the ops-robustness feature.
- The counter `contextforge_mcp_tool_calls_total{tool,outcome}` is **declared**
  in `src/metrics.py` by the ops-robustness feature. This plan only increments
  it; never re-declare it (a second declaration on the same registry raises).
- Audit and rate-limit code must never raise into a tool. Any failure is
  logged and swallowed.
- Rate limiting is single-process by design; multi-replica deployments need a
  shared store, which is out of scope.

---

## File Structure

| File | Responsibility |
|---|---|
| `services/server/src/mcp/context.py` (modify) | `Principal` dataclass + `set_current_principal` / `get_current_principal` context var |
| `services/server/src/mcp/auth.py` (modify) | Sets the principal for OIDC users, API keys and anonymous callers |
| `services/server/src/api/security.py` (modify) | `rate_limit_per_minute` read on key validation, written on create, updated, listed |
| `services/server/src/migrations/versions/0003_mcp_tool_calls.py` (create) | `mcp_tool_calls` table, its indexes, `mcp_api_keys.rate_limit_per_minute` |
| `services/server/src/mcp/audit.py` (create) | `summarize_args`, `classify_outcome`, `record_call`, `audited`, queue writer, retention purge |
| `services/server/src/mcp/ratelimit.py` (create) | Sliding-window limiter and `enforce_rate_limit()` |
| `services/server/src/mcp/permissions.py` (modify) | `requires_permission` wraps with `audited` + rate limit; new `audit_only` |
| `services/server/src/mcp/project_tools.py` (modify) | `@audit_only` on the three ungated tools |
| `services/server/src/main.py` (modify) | Starts/stops the audit writer task |
| `services/server/src/config.py` (modify) | `mcp_audit_retention_days` setting |
| `services/server/src/scheduler.py` (modify) | Daily retention job |
| `services/server/src/api/routes/tool_calls.py` (create) | `GET .../tool-calls` and `.../tool-calls/stats` |
| `services/server/src/api/routes/mcp_keys.py` (modify) | `rate_limit_per_minute` on create + a PUT to change it |
| `services/server/src/api/app.py` (modify) | Includes the new router |
| `services/ui/src/lib/api.ts` (modify) | Tool-call types + client, key rate-limit fields |
| `services/ui/src/pages/Organization.tsx` (modify) | Tabs + **Tool activity** tab |
| `services/ui/src/pages/Settings.tsx` (modify) | Rate-limit field and column on MCP keys |
| `README.md`, `.env.example`, `docker-compose.yml` (modify) | Docs and config |

---

## Task 1: Principal in the MCP context, and migration 0003

**Files:**
- Modify: `services/server/src/mcp/context.py` (append at the end)
- Modify: `services/server/src/mcp/auth.py`
- Modify: `services/server/src/api/security.py` (the `validate_mcp_api_key` SELECT)
- Create: `services/server/src/migrations/versions/0003_mcp_tool_calls.py`
- Test: `services/server/tests/test_mcp_principal.py`
- Test: `services/server/tests/test_migration_0003.py`

**Interfaces:**
- Consumes: the migration runner contract from
  `docs/superpowers/specs/2026-09-07-versioned-migrations-design.md` — a version
  module in `src/migrations/versions/` named `NNNN_<name>.py` exposing
  `VERSION: int`, `NAME: str`, `TRANSACTIONAL: bool = True` and
  `async def upgrade(conn) -> None`. Transactional modules are run by the
  runner inside `async with conn.transaction()`; the module itself must not
  open a transaction.
- Produces: `src.mcp.context.Principal`, `set_current_principal`,
  `get_current_principal`, `ANONYMOUS`; the `mcp_tool_calls` table and the
  `mcp_api_keys.rate_limit_per_minute` column; `validate_mcp_api_key` returns
  `rate_limit_per_minute` in its dict.

### Context you need

`services/server/src/mcp/context.py` today holds only context vars and resolver
helpers (`set_current_org_id`, `get_current_org_id`, `get_selected_project_id`,
`set_current_user_id`, ...). It starts with `from __future__ import annotations`,
`from contextvars import ContextVar`, `from typing import Optional`.

`services/server/src/mcp/auth.py` has a `MCPAuthMiddleware.dispatch` that:
1. skips `/health`, `/oauth/`, `/mcp/oauth/`, `/.well-known/` and `OPTIONS`;
2. resolves org/project from the path;
3. returns early when `self.auth_mode == "disabled"`;
4. validates an OIDC JWT — after `user_id = await find_or_create_oidc_user(claims)`;
5. otherwise validates an API key — after `key_info = await validate_mcp_api_key(api_key)`,
   in the branch that ends with `set_current_permissions(key_perms); return await call_next(request)`;
6. in `transition` mode with no credentials, sets `frozenset({"context-read"})`.

- [ ] **Step 1: Write the failing tests for the principal**

Create `services/server/tests/test_mcp_principal.py`:

```python
"""Principal in the MCP context: default, explicit set, middleware wiring."""
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

import src.projects as projects_mod
from src.mcp import auth as mcp_auth
from src.mcp.context import (
    Principal,
    get_current_principal,
    set_current_namespace,
    set_current_org_id,
    set_current_principal,
    set_current_project_id,
)

PROJECT_PATH = "/mcp/acme/webshop"


def test_default_principal_is_anonymous():
    set_current_principal(None)
    p = get_current_principal()
    assert (p.kind, p.id, p.label, p.rate_limit_per_minute) == ("anonymous", None, "anonymous", None)


def test_principal_round_trips():
    set_current_principal(Principal(kind="api_key", id=7, label="ci-bot", rate_limit_per_minute=30))
    try:
        p = get_current_principal()
        assert (p.kind, p.id, p.label, p.rate_limit_per_minute) == ("api_key", 7, "ci-bot", 30)
    finally:
        set_current_principal(None)


async def echo(request):
    p = get_current_principal()
    return JSONResponse(
        {"kind": p.kind, "id": p.id, "label": p.label, "limit": p.rate_limit_per_minute}
    )


def _fake_project_resolver(org_id):
    async def resolver(org_slug, project_slug):
        return {
            "id": 5, "org_id": org_id, "name": "Webshop", "slug": "webshop",
            "memory_namespace": "acme--webshop", "org_slug": "acme",
        }
    return resolver


def _client(monkeypatch, auth_mode="enabled", org_id=1):
    set_current_org_id(None)
    set_current_project_id(None)
    set_current_namespace(None)
    set_current_principal(None)
    monkeypatch.setattr(projects_mod, "resolve_project_by_slugs", _fake_project_resolver(org_id))
    app = Starlette(routes=[Route("/mcp", echo, methods=["POST"])])
    return TestClient(mcp_auth.add_auth_middleware(app, auth_mode=auth_mode))


def test_api_key_principal_carries_name_and_limit(monkeypatch):
    async def fake_validate(api_key):
        return {
            "id": 11, "name": "ci-bot", "scope": "read", "permissions": "context-read",
            "expires_at": None, "org_id": 1, "project_id": 5, "allowed_projects": [5],
            "rate_limit_per_minute": 42,
        }

    monkeypatch.setattr(mcp_auth, "validate_mcp_api_key", fake_validate)
    client = _client(monkeypatch, org_id=1)
    resp = client.post(PROJECT_PATH, headers={"X-API-Key": "forge_abc"})
    assert resp.status_code == 200
    assert resp.json() == {"kind": "api_key", "id": 11, "label": "ci-bot", "limit": 42}


def test_oidc_principal_is_the_user(monkeypatch):
    def fake_validate(token):
        return {"sub": "u1", "preferred_username": "mario.rossi", "tenant_id": "lascaux"}

    async def fake_find_or_create(claims):
        return 42

    async def fake_role(org_id, user_id):
        return "member"

    async def fake_resolve_perms(org_id, role):
        return frozenset({"context-read"})

    async def fake_project_access(org_id, user_id, project_id):
        return "member"

    from src import config
    settings = config.get_settings()
    monkeypatch.setattr(settings, "oidc_enabled", True, raising=False)
    monkeypatch.setattr(mcp_auth, "validate_oidc_token", fake_validate)
    monkeypatch.setattr(mcp_auth, "find_or_create_oidc_user", fake_find_or_create)
    monkeypatch.setattr(mcp_auth, "get_membership_role", fake_role)
    monkeypatch.setattr(mcp_auth, "resolve_role_permissions", fake_resolve_perms)
    monkeypatch.setattr(projects_mod, "resolve_project_access", fake_project_access)

    client = _client(monkeypatch, org_id=7)
    resp = client.post(PROJECT_PATH, headers={"Authorization": "Bearer eyJhbGciOiJSUzI1NiJ9.x.y"})
    assert resp.status_code == 200
    assert resp.json() == {"kind": "user", "id": 42, "label": "mario.rossi", "limit": None}


def test_transition_mode_without_credentials_is_anonymous(monkeypatch):
    client = _client(monkeypatch, auth_mode="transition", org_id=1)
    resp = client.post(PROJECT_PATH)
    assert resp.status_code == 200
    assert resp.json() == {"kind": "anonymous", "id": None, "label": "anonymous", "limit": None}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run from `services/server`:
`.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_mcp_principal.py`
Expected: FAIL with `ImportError: cannot import name 'Principal' from 'src.mcp.context'`.

- [ ] **Step 3: Add the principal to the context module**

Append to `services/server/src/mcp/context.py` (and add `from dataclasses import dataclass`
to its imports at the top):

```python
@dataclass(frozen=True)
class Principal:
    """Chi sta chiamando un tool MCP: utente OIDC, API key o anonimo."""

    kind: str
    id: int | None
    label: str
    rate_limit_per_minute: int | None = None


ANONYMOUS = Principal(kind="anonymous", id=None, label="anonymous")

_current_principal: ContextVar[Optional[Principal]] = ContextVar("cf_principal", default=None)


def set_current_principal(principal: Optional[Principal]) -> None:
    _current_principal.set(principal)


def get_current_principal() -> Principal:
    """Mai None: un chiamante non autenticato è il principal anonimo."""
    return _current_principal.get() or ANONYMOUS
```

- [ ] **Step 4: Wire the principal into the auth middleware**

In `services/server/src/mcp/auth.py`:

1. Extend the existing `from .context import (...)` block with `ANONYMOUS`,
   `Principal` and `set_current_principal`.
2. In `MCPAuthMiddleware.dispatch`, immediately after the `skip_paths` early
   return (i.e. right before `request.state.mcp_org_level = False`), add:

```python
        set_current_principal(ANONYMOUS)
```

3. In the OIDC branch, right after `user_id = await find_or_create_oidc_user(claims)`
   and `set_current_user_id(user_id)`, add:

```python
                set_current_principal(
                    Principal(
                        kind="user",
                        id=user_id,
                        label=claims.get("preferred_username") or f"sub:{claims.get('sub')}",
                    )
                )
```

4. In the API-key branch, right before `set_current_permissions(key_perms)`, add:

```python
                set_current_principal(
                    Principal(
                        kind="api_key",
                        id=key_info.get("id"),
                        label=key_info.get("name") or f"key:{key_info.get('id')}",
                        rate_limit_per_minute=key_info.get("rate_limit_per_minute"),
                    )
                )
```

- [ ] **Step 5: Return the rate limit from key validation**

In `services/server/src/api/security.py`, inside `validate_mcp_api_key`, change
the SELECT from

```python
            """SELECT id, name, scope, permissions, expires_at, org_id, project_id
               FROM mcp_api_keys
               WHERE key_hash = $1 AND (expires_at IS NULL OR expires_at > $2)""",
```

to

```python
            """SELECT id, name, scope, permissions, expires_at, org_id, project_id,
                      rate_limit_per_minute
               FROM mcp_api_keys
               WHERE key_hash = $1 AND (expires_at IS NULL OR expires_at > $2)""",
```

- [ ] **Step 6: Run the principal tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_mcp_principal.py`
Expected: PASS (5 tests).

- [ ] **Step 7: Write the failing migration test**

Create `services/server/tests/test_migration_0003.py`:

```python
"""0003_mcp_tool_calls: contratto del modulo e DDL applicata."""
import asyncio
import importlib

MODULE = importlib.import_module("src.migrations.versions.0003_mcp_tool_calls")


class FakeConn:
    def __init__(self):
        self.executed = []

    async def execute(self, sql, *args):
        self.executed.append(sql)
        return "OK"


def test_module_contract():
    assert MODULE.VERSION == 3
    assert MODULE.NAME == "mcp_tool_calls"
    assert MODULE.TRANSACTIONAL is True


def test_upgrade_creates_table_indexes_and_column():
    conn = FakeConn()
    asyncio.run(MODULE.upgrade(conn))
    sql = "\n".join(conn.executed)
    assert "CREATE TABLE IF NOT EXISTS mcp_tool_calls" in sql
    assert "mcp_tool_calls_org_created_idx" in sql
    assert "mcp_tool_calls_org_tool_idx" in sql
    assert "ALTER TABLE mcp_api_keys ADD COLUMN IF NOT EXISTS rate_limit_per_minute INT" in sql
```

`importlib.import_module` is used because `0003_mcp_tool_calls` starts with a
digit and cannot appear in an `import` statement.

- [ ] **Step 8: Run the migration test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_migration_0003.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.migrations.versions.0003_mcp_tool_calls'`.

- [ ] **Step 9: Write the migration module**

Create `services/server/src/migrations/versions/0003_mcp_tool_calls.py`:

```python
"""Audit trail of MCP tool calls, plus a per-key rate limit column."""

VERSION = 3
NAME = "mcp_tool_calls"
TRANSACTIONAL = True

SQL = """
CREATE TABLE IF NOT EXISTS mcp_tool_calls (
    id             BIGSERIAL PRIMARY KEY,
    org_id         BIGINT,
    project_id     BIGINT,
    principal_kind TEXT NOT NULL,
    principal_id   BIGINT,
    principal      TEXT NOT NULL,
    tool           TEXT NOT NULL,
    permission     TEXT,
    outcome        TEXT NOT NULL,
    duration_ms    INT NOT NULL,
    error          TEXT,
    args_summary   JSONB,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS mcp_tool_calls_org_created_idx
    ON mcp_tool_calls (org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS mcp_tool_calls_org_tool_idx
    ON mcp_tool_calls (org_id, tool, created_at DESC);

ALTER TABLE mcp_api_keys ADD COLUMN IF NOT EXISTS rate_limit_per_minute INT;
"""


async def upgrade(conn) -> None:
    await conn.execute(SQL)
```

- [ ] **Step 10: Run both test files to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_mcp_principal.py tests/test_migration_0003.py`
Expected: PASS.

- [ ] **Step 11: Run the existing MCP auth tests for regressions**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_mcp_oidc_middleware.py tests/test_mcp_auth_permissions.py tests/test_mcp_key_org_level.py tests/test_mcp_key_project_binding.py tests/test_mcp_project_middleware.py tests/test_mcp_path_routing.py`
Expected: PASS.

- [ ] **Step 12: Commit**

```bash
git add services/server/src/mcp/context.py services/server/src/mcp/auth.py services/server/src/api/security.py services/server/src/migrations/versions/0003_mcp_tool_calls.py services/server/tests/test_mcp_principal.py services/server/tests/test_migration_0003.py
git commit -m "add MCP principal and tool call audit schema"
```

---

## Task 2: Audit module and decorator wiring

**Files:**
- Create: `services/server/src/mcp/audit.py`
- Create: `services/server/src/mcp/ratelimit.py` (stub; Task 3 fills it in)
- Modify: `services/server/src/mcp/permissions.py`
- Modify: `services/server/src/mcp/project_tools.py`
- Modify: `services/server/src/main.py`
- Test: `services/server/tests/test_mcp_audit.py`

**Interfaces:**
- Consumes: `src.mcp.context.get_current_principal()`, which returns a
  `Principal` dataclass with fields `kind` (`"user" | "api_key" | "anonymous"`),
  `id` (`int | None`), `label` (`str`), `rate_limit_per_minute` (`int | None`)
  and is never `None`; `get_current_org_id() -> int | None`;
  `get_selected_project_id() -> int | None`; the `mcp_tool_calls` table from
  Task 1; the counter `contextforge_mcp_tool_calls_total{tool,outcome}`
  declared in `src/metrics.py` as the module attribute `MCP_TOOL_CALLS`.
- Produces:
  - `summarize_args(kwargs: dict) -> dict`
  - `classify_outcome(exc: BaseException) -> str` → `"denied" | "rate_limited" | "error"`
  - `async record_call(*, tool: str, permission: str | None, outcome: str, duration_ms: int, error: str | None = None, args_summary: dict | None = None) -> None`
  - `audited(tool_name: str, permission: str | None = None, args_summary: dict | None = None)` — async context manager
  - `async flush_once() -> int`, `start_audit_writer() -> None`, `async stop_audit_writer() -> None`
  - `requires_permission(permission)` and `audit_only(fn)` in `src/mcp/permissions.py`

### Context you need

`services/server/src/mcp/permissions.py` currently ends with:

```python
def requires_permission(permission: str):
    """None (auth off / legacy caller) allows everything; otherwise the set must contain '*' or the permission."""
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            perms = _current_permissions.get()
            if perms is not None and "*" not in perms and permission not in perms:
                raise ToolError(
                    f"Access denied: tool requires permission '{permission}'. "
                    f"Ask an organization admin to grant it in the dashboard's "
                    f"Organization -> MCP permissions matrix."
                )
            return await fn(*args, **kwargs)
        return wrapper
    return decorator
```

`ToolError` is imported at the top of that file (`from fastmcp.exceptions import ToolError`,
falling back to `PermissionError`). Tools are decorated as `@mcp.tool()` **above**
`@requires_permission("...")`, so `functools.wraps` keeps `fn.__name__` equal to
the tool name.

`services/server/src/mcp/project_tools.py` has three tools carrying only
`@mcp.tool()`: `list_projects`, `use_project`, `current_project`.

`services/server/src/main.py` `main()` calls `await start_scheduler()` before
configuring the ASGI apps, and its `finally:` block runs `await stop_scheduler()`
then `await close_db()`.

`src/db.py` exposes `async def get_pool()`; `src/config.py` exposes `get_settings()`.

- [ ] **Step 1: Write the failing tests**

Create `services/server/tests/test_mcp_audit.py`:

```python
"""Audit dei tool MCP: redazione argomenti, esiti, coda e writer."""
import asyncio
import json

import pytest

from src.mcp import audit
from src.mcp.context import Principal, set_current_principal


def test_summarize_args_truncates_long_strings():
    out = audit.summarize_args({"query": "x" * 500})
    assert len(out["query"]) == 200


def test_summarize_args_redacts_secrets():
    out = audit.summarize_args(
        {"password": "hunter2", "api_token": "t", "private_key": "k", "content": "body"}
    )
    assert out == {
        "password": "<redacted>",
        "api_token": "<redacted>",
        "private_key": "<redacted>",
        "content": "<redacted>",
    }


def test_summarize_args_keeps_sql_truncated():
    out = audit.summarize_args({"sql": "SELECT " + "a" * 500})
    assert out["sql"].startswith("SELECT ")
    assert len(out["sql"]) == 200


def test_summarize_args_collapses_containers():
    out = audit.summarize_args({"opts": {"a": 1, "b": 2, "c": 3}, "items": [1, 2, 3, 4, 5]})
    assert out == {"opts": "<dict:3>", "items": "<list:5>"}


def test_summarize_args_keeps_scalars():
    out = audit.summarize_args({"limit": 10, "flag": True, "missing": None})
    assert out == {"limit": 10, "flag": True, "missing": None}


def test_classify_outcome():
    assert audit.classify_outcome(Exception("Access denied: tool requires permission 'jobs'")) == "denied"
    assert audit.classify_outcome(Exception("Rate limit exceeded: 5 calls per minute for this API key")) == "rate_limited"
    assert audit.classify_outcome(ValueError("boom")) == "error"


def _drain():
    rows = []
    while True:
        try:
            rows.append(audit._queue.get_nowait())
        except asyncio.QueueEmpty:
            return rows


def test_audited_records_ok():
    _drain()
    set_current_principal(Principal(kind="api_key", id=3, label="ci-bot"))

    async def scenario():
        async with audit.audited("repo_search", "context-read", {"query": "abc"}):
            return 1

    try:
        assert asyncio.run(scenario()) == 1
    finally:
        set_current_principal(None)

    row = _drain()[0]
    assert row["tool"] == "repo_search"
    assert row["permission"] == "context-read"
    assert row["outcome"] == "ok"
    assert row["principal_kind"] == "api_key"
    assert row["principal_id"] == 3
    assert row["principal"] == "ci-bot"
    assert row["error"] is None
    assert json.loads(row["args_summary"]) == {"query": "abc"}
    assert isinstance(row["duration_ms"], int) and row["duration_ms"] >= 0


@pytest.mark.parametrize(
    "exc,expected",
    [
        (Exception("Access denied: tool requires permission 'jobs'"), "denied"),
        (Exception("Rate limit exceeded: 5 calls per minute for this API key"), "rate_limited"),
        (ValueError("boom"), "error"),
    ],
)
def test_audited_records_failures_and_reraises(exc, expected):
    _drain()

    async def scenario():
        async with audit.audited("job_submit", "jobs", {}):
            raise exc

    with pytest.raises(type(exc)):
        asyncio.run(scenario())

    row = _drain()[0]
    assert row["outcome"] == expected
    assert row["error"]


def test_queue_drops_oldest_when_full(monkeypatch):
    _drain()
    monkeypatch.setattr(audit, "_queue", asyncio.Queue(maxsize=2))

    async def scenario():
        for i in range(3):
            await audit.record_call(
                tool=f"t{i}", permission=None, outcome="ok", duration_ms=1
            )

    asyncio.run(scenario())
    rows = []
    while not audit._queue.empty():
        rows.append(audit._queue.get_nowait())
    assert [r["tool"] for r in rows] == ["t1", "t2"]


class _FakeConn:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        if self.fail:
            raise RuntimeError("insert failed")
        return "INSERT 0 2"


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def test_flush_once_writes_a_batch(monkeypatch):
    _drain()
    conn = _FakeConn()

    async def fake_pool():
        return _FakePool(conn)

    monkeypatch.setattr(audit, "get_pool", fake_pool)

    async def scenario():
        await audit.record_call(tool="a", permission=None, outcome="ok", duration_ms=1)
        await audit.record_call(tool="b", permission="jobs", outcome="error", duration_ms=2)
        return await audit.flush_once()

    assert asyncio.run(scenario()) == 2
    sql, args = conn.calls[0]
    assert "INSERT INTO mcp_tool_calls" in sql
    assert "unnest(" in sql
    assert len(args) == 11
    assert args[5] == ["a", "b"]          # tool column
    assert args[8] == [1, 2]              # duration_ms column


def test_flush_once_never_raises_when_the_insert_fails(monkeypatch):
    _drain()
    conn = _FakeConn(fail=True)

    async def fake_pool():
        return _FakePool(conn)

    monkeypatch.setattr(audit, "get_pool", fake_pool)

    async def scenario():
        await audit.record_call(tool="a", permission=None, outcome="ok", duration_ms=1)
        return await audit.flush_once()

    assert asyncio.run(scenario()) == 0


def test_requires_permission_still_denies_and_audits():
    from src.mcp import permissions as perms

    _drain()

    @perms.requires_permission("jobs")
    async def fake_tool(x: int = 1) -> int:
        return x

    perms.set_current_permissions(frozenset({"context-read"}))
    try:
        with pytest.raises(Exception, match="jobs"):
            asyncio.run(fake_tool(x=2))
    finally:
        perms.set_current_permissions(None)

    row = _drain()[0]
    assert row["tool"] == "fake_tool"
    assert row["outcome"] == "denied"
    assert row["permission"] == "jobs"


def test_audit_only_records_ungated_tool():
    from src.mcp import permissions as perms

    _drain()

    @perms.audit_only
    async def current_project() -> dict:
        return {"selected_project_id": None}

    assert asyncio.run(current_project()) == {"selected_project_id": None}
    row = _drain()[0]
    assert row["tool"] == "current_project"
    assert row["permission"] is None
    assert row["outcome"] == "ok"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_mcp_audit.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.mcp.audit'`.

- [ ] **Step 3: Write the audit module**

Create `services/server/src/mcp/audit.py`:

```python
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

from ..config import get_settings
from ..db import get_pool
from .context import get_current_org_id, get_current_principal, get_selected_project_id

logger = logging.getLogger(__name__)

MAX_QUEUE = 1000
BATCH_SIZE = 50
MAX_STRING = 200
MAX_ERROR = 500

_REDACT = re.compile(r"secret|password|token|key|content|sql", re.IGNORECASE)

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
        elif isinstance(value, str):
            out[key] = value[:MAX_STRING]
        else:
            out[key] = value
    return out


def classify_outcome(exc: BaseException) -> str:
    message = str(exc)
    if message.startswith("Rate limit exceeded"):
        return "rate_limited"
    if "Access denied" in message:
        return "denied"
    return "error"


def _increment_metric(tool: str, outcome: str) -> None:
    try:
        from ..metrics import MCP_TOOL_CALLS

        MCP_TOOL_CALLS.labels(tool=tool, outcome=outcome).inc()
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
            "error": error[:MAX_ERROR] if error else None,
            "args_summary": json.dumps(args_summary) if args_summary else None,
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


@asynccontextmanager
async def audited(
    tool_name: str, permission: Optional[str] = None, args_summary: Optional[dict] = None
):
    """Misura una chiamata, ne classifica l'esito e la registra."""
    start = time.perf_counter()
    outcome = "ok"
    error: Optional[str] = None
    try:
        yield
    except BaseException as exc:
        outcome = classify_outcome(exc)
        error = str(exc)
        raise
    finally:
        duration_ms = int((time.perf_counter() - start) * 1000)
        _increment_metric(tool_name, outcome)
        await record_call(
            tool=tool_name,
            permission=permission,
            outcome=outcome,
            duration_ms=duration_ms,
            error=error,
            args_summary=args_summary,
        )


def _take_batch() -> list[dict]:
    batch: list[dict] = []
    while len(batch) < BATCH_SIZE:
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
        await _write_batch([row] + _take_batch())


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
```

- [ ] **Step 4: Wire the decorators in permissions.py**

Replace `requires_permission` in `services/server/src/mcp/permissions.py` with
this, and add `audit_only` right after it:

```python
def requires_permission(permission: str):
    """None (auth off / legacy caller) allows everything; otherwise the set must contain '*' or the permission."""
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            # Import differito: audit e ratelimit importano da questo modulo.
            from .audit import audited, summarize_args
            from .ratelimit import enforce_rate_limit

            async with audited(fn.__name__, permission, summarize_args(kwargs)):
                perms = _current_permissions.get()
                if perms is not None and "*" not in perms and permission not in perms:
                    raise ToolError(
                        f"Access denied: tool requires permission '{permission}'. "
                        f"Ask an organization admin to grant it in the dashboard's "
                        f"Organization -> MCP permissions matrix."
                    )
                enforce_rate_limit()
                return await fn(*args, **kwargs)
        return wrapper
    return decorator


def audit_only(fn):
    """Tool senza permesso dedicato: registrato comunque nell'audit."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        from .audit import audited, summarize_args

        async with audited(fn.__name__, None, summarize_args(kwargs)):
            return await fn(*args, **kwargs)
    return wrapper
```

`enforce_rate_limit` does not exist yet — Task 3 writes it. To keep this task
green on its own, create the stub `services/server/src/mcp/ratelimit.py` now:

```python
"""Per-API-key rate limiting (in-process sliding window)."""
from __future__ import annotations


def enforce_rate_limit() -> None:
    return None
```

- [ ] **Step 5: Decorate the ungated project tools**

In `services/server/src/mcp/project_tools.py`, change the import line
`from .context import get_selected_project_id, set_current_project_id` region by
adding `from .permissions import audit_only`, then put `@audit_only` directly
under each `@mcp.tool()`:

```python
@mcp.tool()
@audit_only
async def list_projects(ctx: Context = None) -> dict:
```

```python
@mcp.tool()
@audit_only
async def use_project(project: str, ctx: Context = None) -> dict:
```

```python
@mcp.tool()
@audit_only
async def current_project(ctx: Context = None) -> dict:
```

- [ ] **Step 6: Start and stop the writer in main.py**

In `services/server/src/main.py`, inside `main()`:

1. Right after `await start_scheduler()`, add:

```python
    from .mcp.audit import start_audit_writer, stop_audit_writer

    start_audit_writer()
```

2. In the `finally:` block, before `await stop_scheduler()`, add:

```python
        await stop_audit_writer()
```

- [ ] **Step 7: Run the audit tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_mcp_audit.py`
Expected: PASS.

- [ ] **Step 8: Run the tool-gating and project-tool tests for regressions**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_tool_gating.py tests/test_project_tools.py tests/test_permissions.py tests/test_permissions_model.py tests/test_project_admin_permissions.py`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add services/server/src/mcp/audit.py services/server/src/mcp/ratelimit.py services/server/src/mcp/permissions.py services/server/src/mcp/project_tools.py services/server/src/main.py services/server/tests/test_mcp_audit.py
git commit -m "record every MCP tool call in the audit trail"
```

---

## Task 3: Per-API-key rate limiter

**Files:**
- Modify: `services/server/src/mcp/ratelimit.py` (replace the Task 2 stub entirely)
- Test: `services/server/tests/test_mcp_ratelimit.py`

**Interfaces:**
- Consumes: `src.mcp.context.get_current_principal()` returning a `Principal`
  dataclass with fields `kind` (`"user" | "api_key" | "anonymous"`), `id`
  (`int | None`), `label` (`str`), `rate_limit_per_minute` (`int | None`);
  `ToolError` from `src.mcp.permissions`, imported lazily inside the function to
  avoid an import cycle (`permissions` imports `ratelimit` inside its decorator).
- Produces:
  - `check(key_id: int, limit: int, now: float | None = None) -> bool` — True when
    the call fits the limit (and records it), False when it does not.
  - `enforce_rate_limit() -> None` — raises
    `ToolError("Rate limit exceeded: N calls per minute for this API key")`.
  - `reset() -> None` — clears every window (used by tests).

### Context you need

`requires_permission` in `src/mcp/permissions.py` (wired in Task 2) calls
`enforce_rate_limit()` after the permission check and before running the tool,
inside the `audited(...)` context manager. `classify_outcome` in
`src/mcp/audit.py` maps an exception whose message starts with
`Rate limit exceeded` to the `rate_limited` outcome, so the message prefix is
part of the contract.

- [ ] **Step 1: Write the failing tests**

Create `services/server/tests/test_mcp_ratelimit.py`:

```python
"""Sliding-window rate limit per API key."""
import asyncio

import pytest

from src.mcp import ratelimit
from src.mcp.context import Principal, set_current_principal


def setup_function():
    ratelimit.reset()
    set_current_principal(None)


def teardown_function():
    ratelimit.reset()
    set_current_principal(None)


def test_under_the_limit_is_allowed():
    assert ratelimit.check(1, 3, now=100.0) is True
    assert ratelimit.check(1, 3, now=100.1) is True
    assert ratelimit.check(1, 3, now=100.2) is True


def test_over_the_limit_is_rejected():
    for i in range(3):
        assert ratelimit.check(1, 3, now=100.0 + i) is True
    assert ratelimit.check(1, 3, now=103.0) is False


def test_rejected_calls_do_not_extend_the_window():
    for i in range(3):
        ratelimit.check(1, 3, now=100.0 + i)
    ratelimit.check(1, 3, now=103.0)
    # 60s after the first allowed call the window has room again.
    assert ratelimit.check(1, 3, now=161.0) is True


def test_window_slides():
    for i in range(3):
        ratelimit.check(1, 3, now=100.0 + i)
    assert ratelimit.check(1, 3, now=200.0) is True


def test_keys_are_independent():
    for i in range(3):
        ratelimit.check(1, 3, now=100.0 + i)
    assert ratelimit.check(2, 3, now=100.0) is True


def test_enforce_ignores_users_and_anonymous():
    set_current_principal(Principal(kind="user", id=5, label="mario", rate_limit_per_minute=1))
    ratelimit.enforce_rate_limit()
    ratelimit.enforce_rate_limit()
    set_current_principal(None)
    ratelimit.enforce_rate_limit()


def test_enforce_ignores_api_key_without_a_limit():
    set_current_principal(Principal(kind="api_key", id=9, label="ci-bot"))
    for _ in range(5):
        ratelimit.enforce_rate_limit()


def test_enforce_raises_over_the_limit():
    set_current_principal(Principal(kind="api_key", id=9, label="ci-bot", rate_limit_per_minute=2))
    ratelimit.enforce_rate_limit()
    ratelimit.enforce_rate_limit()
    with pytest.raises(Exception, match="Rate limit exceeded: 2 calls per minute"):
        ratelimit.enforce_rate_limit()


def test_decorator_rate_limits_api_keys_only():
    from src.mcp import permissions as perms

    @perms.requires_permission("context-read")
    async def fake_tool() -> str:
        return "ok"

    perms.set_current_permissions(frozenset({"*"}))
    set_current_principal(Principal(kind="api_key", id=4, label="ci-bot", rate_limit_per_minute=1))
    try:
        assert asyncio.run(fake_tool()) == "ok"
        with pytest.raises(Exception, match="Rate limit exceeded"):
            asyncio.run(fake_tool())
        set_current_principal(Principal(kind="user", id=4, label="mario", rate_limit_per_minute=1))
        assert asyncio.run(fake_tool()) == "ok"
    finally:
        perms.set_current_permissions(None)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_mcp_ratelimit.py`
Expected: FAIL with `AttributeError: module 'src.mcp.ratelimit' has no attribute 'reset'`.

- [ ] **Step 3: Write the limiter**

Replace the whole content of `services/server/src/mcp/ratelimit.py`:

```python
"""Per-API-key rate limiting: in-process sliding window over the last 60 seconds.

Semantica single-process: con più repliche serve uno store condiviso.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque

from .context import get_current_principal

WINDOW_SECONDS = 60.0

_windows: dict[int, deque] = defaultdict(deque)


def reset() -> None:
    _windows.clear()


def check(key_id: int, limit: int, now: float | None = None) -> bool:
    """True quando la chiamata rientra nel limite (e la registra)."""
    moment = time.monotonic() if now is None else now
    window = _windows[key_id]
    cutoff = moment - WINDOW_SECONDS
    while window and window[0] <= cutoff:
        window.popleft()
    if len(window) >= limit:
        return False
    window.append(moment)
    return True


def enforce_rate_limit() -> None:
    """Applica il limite della API key corrente; solleva ToolError se superato."""
    principal = get_current_principal()
    if principal.kind != "api_key" or principal.id is None:
        return
    limit = principal.rate_limit_per_minute
    if not limit or limit <= 0:
        return
    if not check(principal.id, limit):
        from .permissions import ToolError

        raise ToolError(f"Rate limit exceeded: {limit} calls per minute for this API key")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_mcp_ratelimit.py`
Expected: PASS (9 tests).

- [ ] **Step 5: Run the audit and gating tests for regressions**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_mcp_audit.py tests/test_tool_gating.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/server/src/mcp/ratelimit.py services/server/tests/test_mcp_ratelimit.py
git commit -m "enforce per-key MCP rate limits"
```

---

## Task 4: Audit retention job

**Files:**
- Modify: `services/server/src/mcp/audit.py` (append `purge_old_calls`)
- Modify: `services/server/src/config.py`
- Modify: `services/server/src/scheduler.py`
- Test: `services/server/tests/test_mcp_audit_retention.py`

**Interfaces:**
- Consumes: `src.db.get_pool()` (already imported in `src/mcp/audit.py` as
  `get_pool`) and `src.config.get_settings()` (already imported there too).
- Produces: `async purge_old_calls() -> int` in `src/mcp/audit.py`, the
  `mcp_audit_retention_days: int = 90` field on `Settings`, and the scheduler
  job id `mcp_audit_retention` with its wrapper `_purge_old_tool_calls`.

### Context you need

`services/server/src/scheduler.py` registers global jobs inside
`start_scheduler()`, e.g.:

```python
    _scheduler.add_job(
        _purge_expired_oauth_flows, "interval", minutes=5, id="oauth_flow_reaper",
        replace_existing=True,
    )
```

and defines the wrapper above it as:

```python
async def _purge_expired_oauth_flows() -> None:
    """Delete expired OAuth bridge flows: they hold Keycloak tokens in clear text."""
    deleted = await purge_expired_flows()
    if deleted:
        logger.info("Purged %d expired OAuth bridge flow(s)", deleted)
```

with the import `from .mcp.oauth_bridge import purge_expired_flows` at the top.

`src/config.py` holds a pydantic-settings `Settings` class whose fields map to
uppercase env vars; `mcp_auth_mode: str = "disabled"` is one of them.

- [ ] **Step 1: Write the failing test**

Create `services/server/tests/test_mcp_audit_retention.py`:

```python
"""Retention dell'audit: cancella le righe più vecchie della finestra."""
import asyncio

from src import config, scheduler
from src.mcp import audit


class FakeConn:
    def __init__(self, result="DELETE 4"):
        self.result = result
        self.executed = []

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        return self.result


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def _patch(monkeypatch, conn):
    async def fake_pool():
        return FakePool(conn)

    monkeypatch.setattr(audit, "get_pool", fake_pool)


def test_default_retention_is_90_days():
    assert config.get_settings().mcp_audit_retention_days == 90


def test_purge_uses_the_configured_window(monkeypatch):
    conn = FakeConn()
    _patch(monkeypatch, conn)
    monkeypatch.setattr(config.get_settings(), "mcp_audit_retention_days", 7, raising=False)

    deleted = asyncio.run(audit.purge_old_calls())

    assert deleted == 4
    sql, args = conn.executed[0]
    assert "DELETE FROM mcp_tool_calls" in sql
    assert "make_interval(days => $1)" in sql
    assert args == (7,)


def test_purge_disabled_when_retention_is_zero(monkeypatch):
    conn = FakeConn()
    _patch(monkeypatch, conn)
    monkeypatch.setattr(config.get_settings(), "mcp_audit_retention_days", 0, raising=False)

    assert asyncio.run(audit.purge_old_calls()) == 0
    assert conn.executed == []


def test_purge_never_raises(monkeypatch):
    class Boom:
        def acquire(self):
            raise RuntimeError("db down")

    async def fake_pool():
        return Boom()

    monkeypatch.setattr(audit, "get_pool", fake_pool)
    monkeypatch.setattr(config.get_settings(), "mcp_audit_retention_days", 30, raising=False)
    assert asyncio.run(audit.purge_old_calls()) == 0


def test_scheduler_wrapper_calls_the_purge(monkeypatch):
    calls = []

    async def fake_purge():
        calls.append(True)
        return 3

    monkeypatch.setattr(scheduler, "purge_old_tool_calls", fake_purge, raising=False)
    asyncio.run(scheduler._purge_old_tool_calls())
    assert calls == [True]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_mcp_audit_retention.py`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'mcp_audit_retention_days'`.

- [ ] **Step 3: Add the setting**

In `services/server/src/config.py`, in the `Settings` class right after
`mcp_auth_mode: str = "disabled"`, add:

```python
    # Days of MCP tool-call audit history kept; 0 disables the retention job.
    mcp_audit_retention_days: int = 90
```

- [ ] **Step 4: Add the purge to the audit module**

Append to `services/server/src/mcp/audit.py`:

```python
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
```

(`get_settings` is already imported at the top of the module from Task 2.)

- [ ] **Step 5: Register the scheduler job**

In `services/server/src/scheduler.py`:

1. Add to the imports at the top, next to the oauth-bridge import:

```python
from .mcp.audit import purge_old_calls as purge_old_tool_calls
```

2. Add this wrapper right after `_purge_expired_oauth_flows`:

```python
async def _purge_old_tool_calls() -> None:
    """Delete MCP tool-call audit rows older than MCP_AUDIT_RETENTION_DAYS."""
    deleted = await purge_old_tool_calls()
    if deleted:
        logger.info("Purged %d MCP tool-call audit row(s)", deleted)
```

3. Register it inside `start_scheduler()`, after the `oauth_flow_reaper` job:

```python
    # Daily retention of the MCP tool-call audit trail.
    _scheduler.add_job(
        _purge_old_tool_calls, "interval", hours=24, id="mcp_audit_retention",
        replace_existing=True,
    )
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_mcp_audit_retention.py`
Expected: PASS (5 tests).

- [ ] **Step 7: Run the neighbouring suites for regressions**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_mcp_audit.py tests/test_oauth_bridge_reaper.py tests/test_oidc_settings.py tests/test_org_settings.py`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add services/server/src/mcp/audit.py services/server/src/config.py services/server/src/scheduler.py services/server/tests/test_mcp_audit_retention.py
git commit -m "purge old MCP audit rows daily"
```

---

## Task 5: REST routes for tool calls and key rate limits

**Files:**
- Create: `services/server/src/api/routes/tool_calls.py`
- Modify: `services/server/src/api/app.py`
- Modify: `services/server/src/api/routes/mcp_keys.py`
- Modify: `services/server/src/api/security.py`
- Test: `services/server/tests/test_tool_calls_routes.py`

**Interfaces:**
- Consumes: the `mcp_tool_calls` table (columns `id, org_id, project_id,
  principal_kind, principal_id, principal, tool, permission, outcome,
  duration_ms, error, args_summary, created_at`) and
  `mcp_api_keys.rate_limit_per_minute` from Task 1;
  `src.api.deps.get_current_user_id` (FastAPI dependency returning `int`);
  `src.tenancy.get_membership_role(org_id, user_id) -> str | None` and
  `src.tenancy.role_at_least(role, minimum) -> bool`.
- Produces:
  - `GET /api/organizations/{org_id}/tool-calls` → `{"calls": [...], "total": n}`
  - `GET /api/organizations/{org_id}/tool-calls/stats?window=24h|7d` →
    `{"by_tool": [...], "by_principal": [...], "totals": {...}, "total": n}`
  - `PUT /api/mcp/keys/{key_id}` accepting `{"rate_limit_per_minute": int | null}`
  - `rate_limit_per_minute` accepted by `POST /api/mcp/keys` and returned by
    `GET /api/mcp/keys`
  - `src.api.security.update_mcp_api_key_rate_limit(key_id, org_id, value) -> bool`

### Context you need

Org-scoped routes use this local gate (from
`services/server/src/api/routes/organizations.py`); tests monkeypatch
`tenancy.get_membership_role` **on the route module**:

```python
async def _require_role_in(org_id: int, user_id: int, minimum: str) -> str:
    role = await tenancy.get_membership_role(org_id, user_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    if not tenancy.role_at_least(role, minimum):
        raise HTTPException(status_code=403, detail=f"Requires '{minimum}' role or higher")
    return role
```

`services/server/src/api/routes/mcp_keys.py` has
`router = APIRouter(prefix="/mcp/keys", tags=["mcp-keys"])`, `CreateKeyRequest`,
`CreateKeyResponse`, `_serialize_key(row)` (isoformats `created_at` /
`last_used_at` / `expires_at`, passes everything else through), `create_key`,
`list_keys`, `revoke_key`, `validate_key`. It already imports
`from ... import tenancy`, `from ..deps import ActiveOrg, get_active_org, get_current_user_id, require_role`
and a `from ..security import (create_mcp_api_key, get_mcp_api_key, list_mcp_api_keys, revoke_mcp_api_key, validate_mcp_api_key)` block.
`revoke_key` gates with:

```python
    is_owner = key.get("created_by") == user_id
    if not is_owner and not tenancy.role_at_least(org.role, "admin"):
        raise HTTPException(status_code=403, detail="Only the key creator or an org admin can revoke this key")
```

`services/server/src/api/security.py` has `create_mcp_api_key(...)` whose INSERT is:

```python
            key_id = await conn.fetchval(
                """INSERT INTO mcp_api_keys (name, key_hash, scope, created_by, expires_at, org_id, permissions, project_id)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id""",
```

`list_mcp_api_keys(org_id)` has two SELECTs both starting
`SELECT k.id, k.name, k.scope, k.permissions, k.created_at, k.last_used_at, k.expires_at, k.created_by, k.org_id, k.project_id, p.slug AS project_slug`,
and `get_mcp_api_key(key_id)` returns `id, name, scope, permissions, created_by, org_id`.

`services/server/src/api/app.py` imports route modules as
`from .routes import organizations as organizations_routes` and includes them
with `api.include_router(organizations_routes.router, prefix="/api")`.

- [ ] **Step 1: Write the failing tests**

Create `services/server/tests/test_tool_calls_routes.py`:

```python
"""REST dell'audit: gating di ruolo, filtri passati alla SQL, forma delle stats."""
import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from src.api.routes import tool_calls as routes


class FakeConn:
    def __init__(self, fetch_rows=None, fetchval=0, fetchrow=None):
        self.fetch_rows = list(fetch_rows) if fetch_rows is not None else []
        self.fetchval_value = fetchval
        self.fetchrow_value = fetchrow
        self.calls = []

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        if self.fetch_rows and isinstance(self.fetch_rows[0], list):
            return self.fetch_rows.pop(0)
        return self.fetch_rows

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        return self.fetchval_value

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return self.fetchrow_value


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def _wire(monkeypatch, conn, role="admin"):
    async def fake_role(org_id, user_id):
        return role

    async def fake_pool():
        return FakePool(conn)

    monkeypatch.setattr(routes.tenancy, "get_membership_role", fake_role, raising=False)
    monkeypatch.setattr(routes, "get_pool", fake_pool)


def test_member_gets_403(monkeypatch):
    _wire(monkeypatch, FakeConn(), role="member")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.list_tool_calls(org_id=1, user_id=7))
    assert exc.value.status_code == 403


def test_non_member_gets_404(monkeypatch):
    _wire(monkeypatch, FakeConn(), role=None)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.list_tool_calls(org_id=1, user_id=7))
    assert exc.value.status_code == 404


def test_build_filters_only_org():
    where, params = routes._build_filters(3, None, None, None, None, None)
    assert where == "WHERE org_id = $1"
    assert params == [3]


def test_build_filters_all_criteria():
    since = datetime(2026, 9, 1, tzinfo=timezone.utc)
    where, params = routes._build_filters(3, "repo_search", "denied", "ci-bot", 9, since)
    assert where == (
        "WHERE org_id = $1 AND tool = $2 AND outcome = $3 "
        "AND principal = $4 AND project_id = $5 AND created_at >= $6"
    )
    assert params == [3, "repo_search", "denied", "ci-bot", 9, since]


def test_list_serializes_rows_and_total(monkeypatch):
    created = datetime(2026, 9, 7, 10, 0, tzinfo=timezone.utc)
    row = {
        "id": 1, "org_id": 3, "project_id": 9, "principal_kind": "api_key",
        "principal_id": 4, "principal": "ci-bot", "tool": "repo_search",
        "permission": "context-read", "outcome": "ok", "duration_ms": 12,
        "error": None, "args_summary": '{"query": "abc"}', "created_at": created,
    }
    conn = FakeConn(fetch_rows=[row], fetchval=1)
    _wire(monkeypatch, conn)

    out = asyncio.run(routes.list_tool_calls(org_id=3, user_id=7, limit=10, offset=0))

    assert out["total"] == 1
    call = out["calls"][0]
    assert call["created_at"] == "2026-09-07T10:00:00+00:00"
    assert call["args_summary"] == {"query": "abc"}
    _, sql, args = conn.calls[0]
    assert "ORDER BY created_at DESC" in sql
    assert args[-2:] == (10, 0)


def test_limit_is_capped_at_200(monkeypatch):
    conn = FakeConn(fetch_rows=[], fetchval=0)
    _wire(monkeypatch, conn)
    asyncio.run(routes.list_tool_calls(org_id=3, user_id=7, limit=999))
    _, _sql, args = conn.calls[0]
    assert args[-2] == 200


def test_filters_reach_the_query(monkeypatch):
    conn = FakeConn(fetch_rows=[], fetchval=0)
    _wire(monkeypatch, conn)
    asyncio.run(
        routes.list_tool_calls(
            org_id=3, user_id=7, tool="repo_search", outcome="denied", principal="ci-bot"
        )
    )
    _, sql, args = conn.calls[0]
    assert "tool = $2" in sql and "outcome = $3" in sql and "principal = $4" in sql
    assert args[:4] == (3, "repo_search", "denied", "ci-bot")


def test_invalid_since_is_422(monkeypatch):
    _wire(monkeypatch, FakeConn())
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.list_tool_calls(org_id=3, user_id=7, since="not-a-date"))
    assert exc.value.status_code == 422


def test_stats_shape(monkeypatch):
    by_tool = [{"tool": "repo_search", "calls": 5, "errors": 1, "denied": 0,
                "rate_limited": 0, "p95_ms": 40}]
    by_principal = [{"principal": "ci-bot", "calls": 5, "errors": 1, "denied": 0,
                     "rate_limited": 0, "p95_ms": 40}]
    conn = FakeConn(
        fetch_rows=[by_tool, by_principal],
        fetchrow={"calls": 5, "ok": 4, "errors": 1, "denied": 0, "rate_limited": 0},
    )
    _wire(monkeypatch, conn)

    out = asyncio.run(routes.tool_call_stats(org_id=3, user_id=7, window="7d"))

    assert out["by_tool"] == by_tool
    assert out["by_principal"] == by_principal
    assert out["totals"] == {"calls": 5, "ok": 4, "errors": 1, "denied": 0, "rate_limited": 0}
    assert out["total"] == 5
    _, _sql, args = conn.calls[0]
    assert args == (3, 168)


def test_stats_rejects_unknown_window(monkeypatch):
    _wire(monkeypatch, FakeConn())
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.tool_call_stats(org_id=3, user_id=7, window="1y"))
    assert exc.value.status_code == 422


def test_create_key_accepts_a_rate_limit():
    from src.api.routes.mcp_keys import CreateKeyRequest

    assert CreateKeyRequest(name="k", rate_limit_per_minute=60).rate_limit_per_minute == 60


def test_create_key_rejects_a_zero_rate_limit():
    from pydantic import ValidationError

    from src.api.routes.mcp_keys import CreateKeyRequest

    with pytest.raises(ValidationError):
        CreateKeyRequest(name="k", rate_limit_per_minute=0)


def test_serialize_key_passes_the_rate_limit_through():
    from src.api.routes.mcp_keys import _serialize_key

    out = _serialize_key({"id": 1, "name": "k", "rate_limit_per_minute": 30})
    assert out["rate_limit_per_minute"] == 30
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_tool_calls_routes.py`
Expected: FAIL with `ImportError: cannot import name 'tool_calls' from 'src.api.routes'`.

- [ ] **Step 3: Write the routes module**

Create `services/server/src/api/routes/tool_calls.py`:

```python
"""Read-only REST over the MCP tool-call audit trail (org admins and owners)."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ... import tenancy
from ...db import get_pool
from ..deps import get_current_user_id

router = APIRouter(prefix="/organizations", tags=["tool-calls"])

MAX_LIMIT = 200
WINDOW_HOURS = {"24h": 24, "7d": 168}

_COLUMNS = (
    "id, org_id, project_id, principal_kind, principal_id, principal, "
    "tool, permission, outcome, duration_ms, error, args_summary, created_at"
)


async def _require_admin(org_id: int, user_id: int) -> str:
    role = await tenancy.get_membership_role(org_id, user_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    if not tenancy.role_at_least(role, "admin"):
        raise HTTPException(status_code=403, detail="Requires 'admin' role or higher")
    return role


def _build_filters(
    org_id: int,
    tool: Optional[str],
    outcome: Optional[str],
    principal: Optional[str],
    project_id: Optional[int],
    since: Optional[datetime],
) -> tuple[str, list]:
    clauses = ["org_id = $1"]
    params: list = [org_id]
    for column, value in (
        ("tool", tool),
        ("outcome", outcome),
        ("principal", principal),
        ("project_id", project_id),
    ):
        if value is not None and value != "":
            params.append(value)
            clauses.append(f"{column} = ${len(params)}")
    if since is not None:
        params.append(since)
        clauses.append(f"created_at >= ${len(params)}")
    return "WHERE " + " AND ".join(clauses), params


def _serialize_call(row) -> dict:
    out = dict(row)
    created = out.get("created_at")
    if created is not None and hasattr(created, "isoformat"):
        out["created_at"] = created.isoformat()
    summary = out.get("args_summary")
    if isinstance(summary, str):
        try:
            out["args_summary"] = json.loads(summary)
        except ValueError:
            out["args_summary"] = None
    return out


@router.get("/{org_id}/tool-calls")
async def list_tool_calls(
    org_id: int,
    limit: int = 50,
    offset: int = 0,
    tool: Optional[str] = None,
    outcome: Optional[str] = None,
    principal: Optional[str] = None,
    project_id: Optional[int] = None,
    since: Optional[str] = None,
    user_id: int = Depends(get_current_user_id),
):
    """Recent MCP tool calls for the organization, newest first."""
    await _require_admin(org_id, user_id)

    parsed_since: Optional[datetime] = None
    if since:
        try:
            parsed_since = datetime.fromisoformat(since)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid 'since': expected ISO 8601")

    limit = max(1, min(int(limit), MAX_LIMIT))
    offset = max(0, int(offset))
    where, params = _build_filters(org_id, tool, outcome, principal, project_id, parsed_since)

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {_COLUMNS} FROM mcp_tool_calls {where} "
            f"ORDER BY created_at DESC, id DESC "
            f"LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}",
            *params,
            limit,
            offset,
        )
        total = await conn.fetchval(f"SELECT COUNT(*) FROM mcp_tool_calls {where}", *params)
    return {"calls": [_serialize_call(r) for r in rows], "total": int(total or 0)}


@router.get("/{org_id}/tool-calls/stats")
async def tool_call_stats(
    org_id: int,
    window: str = "24h",
    user_id: int = Depends(get_current_user_id),
):
    """Aggregates over the window: per tool, per principal, and totals."""
    await _require_admin(org_id, user_id)
    hours = WINDOW_HOURS.get(window)
    if hours is None:
        raise HTTPException(status_code=422, detail="Invalid 'window': use 24h or 7d")

    aggregates = (
        "COUNT(*)::int AS calls, "
        "COUNT(*) FILTER (WHERE outcome = 'error')::int AS errors, "
        "COUNT(*) FILTER (WHERE outcome = 'denied')::int AS denied, "
        "COUNT(*) FILTER (WHERE outcome = 'rate_limited')::int AS rate_limited, "
        "COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms), 0)::int AS p95_ms"
    )
    window_where = "WHERE org_id = $1 AND created_at >= NOW() - make_interval(hours => $2)"

    pool = await get_pool()
    async with pool.acquire() as conn:
        by_tool = await conn.fetch(
            f"SELECT tool, {aggregates} FROM mcp_tool_calls {window_where} "
            "GROUP BY tool ORDER BY calls DESC",
            org_id,
            hours,
        )
        by_principal = await conn.fetch(
            f"SELECT principal, {aggregates} FROM mcp_tool_calls {window_where} "
            "GROUP BY principal ORDER BY calls DESC",
            org_id,
            hours,
        )
        totals = await conn.fetchrow(
            "SELECT COUNT(*)::int AS calls, "
            "COUNT(*) FILTER (WHERE outcome = 'ok')::int AS ok, "
            "COUNT(*) FILTER (WHERE outcome = 'error')::int AS errors, "
            "COUNT(*) FILTER (WHERE outcome = 'denied')::int AS denied, "
            "COUNT(*) FILTER (WHERE outcome = 'rate_limited')::int AS rate_limited "
            f"FROM mcp_tool_calls {window_where}",
            org_id,
            hours,
        )

    totals_dict = (
        dict(totals)
        if totals
        else {"calls": 0, "ok": 0, "errors": 0, "denied": 0, "rate_limited": 0}
    )
    return {
        "by_tool": [dict(r) for r in by_tool],
        "by_principal": [dict(r) for r in by_principal],
        "totals": totals_dict,
        "total": totals_dict.get("calls", 0),
    }
```

- [ ] **Step 4: Include the router**

In `services/server/src/api/app.py`, add the import next to the other route
imports:

```python
from .routes import tool_calls as tool_calls_routes
```

and the include next to the other includes:

```python
api.include_router(tool_calls_routes.router, prefix="/api")
```

- [ ] **Step 5: Persist and expose the key rate limit**

In `services/server/src/api/security.py`:

1. `create_mcp_api_key` gains a keyword argument
   `rate_limit_per_minute: Optional[int] = None` (after `project_ids`), and its
   INSERT becomes:

```python
            key_id = await conn.fetchval(
                """INSERT INTO mcp_api_keys (name, key_hash, scope, created_by, expires_at, org_id, permissions, project_id, rate_limit_per_minute)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) RETURNING id""",
                name,
                key_hash,
                scope,
                created_by,
                expires_at,
                org_id,
                permissions,
                legacy_project_id,
                rate_limit_per_minute,
            )
```

2. Both SELECTs in `list_mcp_api_keys` gain `k.rate_limit_per_minute` right after
   `k.project_id`.

3. Add after `get_mcp_api_key`:

```python
async def update_mcp_api_key_rate_limit(
    key_id: int, org_id: int, rate_limit_per_minute: Optional[int]
) -> bool:
    """Set (or clear, with None) the per-minute rate limit of a key."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE mcp_api_keys SET rate_limit_per_minute = $1 WHERE id = $2 AND org_id = $3",
            rate_limit_per_minute,
            key_id,
            org_id,
        )
    return result == "UPDATE 1"
```

- [ ] **Step 6: Accept the rate limit in the key routes**

In `services/server/src/api/routes/mcp_keys.py`:

1. Add to `CreateKeyRequest`:

```python
    # Chiamate al minuto consentite alla key. None = illimitata.
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=100000)
```

2. Pass it through in `create_key`:

```python
    raw_key = await create_mcp_api_key(
        name=req.name,
        scope=req.scope or "read,write",
        created_by=user_id,
        expires_days=req.expires_days,
        org_id=org.org_id,
        permissions=permissions_csv,
        project_ids=project_ids,
        rate_limit_per_minute=req.rate_limit_per_minute,
    )
```

3. Add `update_mcp_api_key_rate_limit` to the `from ..security import (...)`
   block, and add this model plus route after `list_keys`:

```python
class UpdateKeyRequest(BaseModel):
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=100000)


@router.put("/{key_id}")
async def update_key(
    key_id: int,
    req: UpdateKeyRequest,
    user_id: int = Depends(get_current_user_id),
    org: ActiveOrg = Depends(get_active_org),
):
    """Change a key's rate limit. Creator or org admin only; null = unlimited."""
    key = await get_mcp_api_key(key_id)
    if not key or key.get("org_id") != org.org_id:
        raise HTTPException(status_code=404, detail="Key not found")
    if key.get("created_by") != user_id and not tenancy.role_at_least(org.role, "admin"):
        raise HTTPException(
            status_code=403, detail="Only the key creator or an org admin can update this key"
        )
    if not await update_mcp_api_key_rate_limit(key_id, org.org_id, req.rate_limit_per_minute):
        raise HTTPException(status_code=404, detail="Key not found")
    return {"status": "ok"}
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_tool_calls_routes.py tests/test_mcp_keys_route.py`
Expected: PASS.

- [ ] **Step 8: Run the API suites for regressions**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_mcp_key_permissions.py tests/test_mcp_key_org_level.py tests/test_mcp_key_project_binding.py tests/test_org_owner_only_management.py tests/test_projects_routes.py`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add services/server/src/api/routes/tool_calls.py services/server/src/api/app.py services/server/src/api/routes/mcp_keys.py services/server/src/api/security.py services/server/tests/test_tool_calls_routes.py
git commit -m "expose tool call audit over REST"
```

---

## Task 6: UI — Tool activity tab in Organization

**Files:**
- Modify: `services/ui/src/lib/api.ts`
- Modify: `services/ui/src/pages/Organization.tsx`

**Interfaces:**
- Consumes: `GET /api/organizations/{org_id}/tool-calls?limit&offset&tool&outcome&principal&project_id&since`
  → `{"calls": ToolCall[], "total": number}` and
  `GET /api/organizations/{org_id}/tool-calls/stats?window=24h|7d`
  → `{by_tool: ToolStatRow[], by_principal: PrincipalStatRow[], totals: {calls, ok, errors, denied, rate_limited}, total}`.
- Produces: `ToolCall`, `ToolStatRow`, `PrincipalStatRow`, `ToolCallStats`,
  `ToolCallFilters` types and `api.toolCalls.list` / `api.toolCalls.stats` in
  `lib/api.ts`; the **Tool activity** tab in `pages/Organization.tsx`.

### Context you need

`services/ui/src/lib/api.ts` has a private
`request<T>(path, options?, includeAuth?)` helper that attaches the auth token
plus `X-Org-Id` / `X-Project-Id` headers and throws on non-2xx. Clients are
grouped in the exported `api` object, e.g.:

```ts
  organizations: {
    members: (orgId: number) =>
      request<{ members: OrgMember[] }>(`/api/organizations/${orgId}/members`),
```

`services/ui/src/pages/Organization.tsx` is currently a flat page: its default
export renders `<div className="p-4 sm:p-8"><div className="page-wide">` with a
header `<div className="mb-6">`, then `<ProjectsSection .../>`, an error
`Banner`, an owner-only Settings section, the Members section,
`<AddUserSection .../>` when `canAddMembers`, and `<McpPermissionsSection .../>`
when `isOwner`. It computes:

```tsx
  const myRole: OrgRole = activeOrg?.role ?? 'viewer'
  const isOwner = myRole === 'owner'
  const canAddMembers = ROLE_RANK[myRole] >= ROLE_RANK.admin
```

It already imports `useCallback, useEffect, useState, type FormEvent` from React,
`useAppStore` from `'../store'`, and from `'../components/ui'`:
`Banner, Button, Input, Select, Badge, Dialog, DialogFooter, Table, Thead, Tbody, Tr, Th, Td, useConfirm, useToast`.
`Card`, `Tabs`, `TabsList`, `TabsTrigger` and `TabsContent` are exported by the
same module but not yet imported here. The mobile pattern used across the app is
a `<div className="space-y-3 md:hidden">` of `<Card>`s next to a
`<Card className="hidden md:block">` wrapping the `<Table>` (see `pages/Jobs.tsx`).
`useAppStore((s) => s.projects)` yields `{ id, name, slug }[]`.

- [ ] **Step 1: Add the types and client to lib/api.ts**

Insert right before `export interface MCPApiKey {` in `services/ui/src/lib/api.ts`:

```ts
export interface ToolCall {
  id: number
  org_id: number | null
  project_id: number | null
  principal_kind: string
  principal_id: number | null
  principal: string
  tool: string
  permission: string | null
  outcome: string
  duration_ms: number
  error: string | null
  args_summary: Record<string, unknown> | null
  created_at: string
}

export interface ToolStatRow {
  tool: string
  calls: number
  errors: number
  denied: number
  rate_limited: number
  p95_ms: number
}

export interface PrincipalStatRow {
  principal: string
  calls: number
  errors: number
  denied: number
  rate_limited: number
  p95_ms: number
}

export interface ToolCallStats {
  by_tool: ToolStatRow[]
  by_principal: PrincipalStatRow[]
  totals: { calls: number; ok: number; errors: number; denied: number; rate_limited: number }
  total: number
}

export interface ToolCallFilters {
  limit?: number
  offset?: number
  tool?: string
  outcome?: string
  principal?: string
  project_id?: number
}
```

and add this client immediately after the closing `},` of the
`organizations: { ... }` group (i.e. just before `projects: {`):

```ts
  toolCalls: {
    list: (orgId: number, filters: ToolCallFilters = {}) => {
      const query = new URLSearchParams()
      Object.entries(filters).forEach(([key, value]) => {
        if (value !== undefined && value !== '') query.set(key, String(value))
      })
      const suffix = query.toString() ? `?${query.toString()}` : ''
      return request<{ calls: ToolCall[]; total: number }>(
        `/api/organizations/${orgId}/tool-calls${suffix}`
      )
    },
    stats: (orgId: number, window: '24h' | '7d') =>
      request<ToolCallStats>(`/api/organizations/${orgId}/tool-calls/stats?window=${window}`),
  },
```

- [ ] **Step 2: Extend the imports in Organization.tsx**

```tsx
import {
  api,
  MCP_PERMISSION_VALUES,
  type McpPermissionsMatrix,
  type OrgMember,
  type OrgRole,
  type ProjectMember,
  type ToolCall,
  type ToolCallStats,
} from '../lib/api'
```

```tsx
import { Banner, Button, Card, Input, Select, Badge, Dialog, DialogFooter, Table, Thead, Tbody, Tr, Th, Td, Tabs, TabsList, TabsTrigger, TabsContent, useConfirm, useToast } from '../components/ui'
```

- [ ] **Step 3: Add the Tool activity section**

Add this immediately before `export default function Organization() {` in
`services/ui/src/pages/Organization.tsx`:

```tsx
const OUTCOME_OPTIONS = [
  { value: '', label: 'Any outcome' },
  { value: 'ok', label: 'ok' },
  { value: 'denied', label: 'denied' },
  { value: 'error', label: 'error' },
  { value: 'rate_limited', label: 'rate limited' },
]

const PAGE_SIZE = 50

function outcomeVariant(outcome: string): 'success' | 'warning' | 'danger' | 'muted' {
  if (outcome === 'ok') return 'success'
  if (outcome === 'denied' || outcome === 'rate_limited') return 'warning'
  if (outcome === 'error') return 'danger'
  return 'muted'
}

function ToolActivitySection({ orgId }: { orgId: number }) {
  const toast = useToast()
  const projects = useAppStore((s) => s.projects)
  const [calls, setCalls] = useState<ToolCall[]>([])
  const [total, setTotal] = useState(0)
  const [stats, setStats] = useState<ToolCallStats | null>(null)
  const [statsWindow, setStatsWindow] = useState<'24h' | '7d'>('24h')
  const [tool, setTool] = useState('')
  const [outcome, setOutcome] = useState('')
  const [principal, setPrincipal] = useState('')
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [list, agg] = await Promise.all([
        api.toolCalls.list(orgId, {
          limit: PAGE_SIZE,
          offset,
          tool: tool || undefined,
          outcome: outcome || undefined,
          principal: principal || undefined,
        }),
        api.toolCalls.stats(orgId, statsWindow),
      ])
      setCalls(list.calls)
      setTotal(list.total)
      setStats(agg)
    } catch (e) {
      toast.error(String(e))
    } finally {
      setLoading(false)
    }
  }, [orgId, offset, tool, outcome, principal, statsWindow, toast])

  useEffect(() => { void load() }, [load])

  const projectName = (id: number | null) =>
    id === null ? '—' : projects.find((p) => p.id === id)?.name ?? `#${id}`

  const fmtTime = (iso: string) =>
    new Date(iso).toLocaleString(undefined, {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit',
    })

  const toolOptions = [
    { value: '', label: 'Any tool' },
    ...(stats?.by_tool ?? []).map((row) => ({ value: row.tool, label: row.tool })),
  ]

  const tiles = [
    { label: 'Calls', value: stats?.totals.calls ?? 0 },
    { label: 'Errors', value: stats?.totals.errors ?? 0 },
    { label: 'Denied', value: stats?.totals.denied ?? 0 },
    { label: 'Rate limited', value: stats?.totals.rate_limited ?? 0 },
  ]

  return (
    <section className="border border-border mb-6 p-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between mb-4">
        <div>
          <h2 className="text-base font-semibold">Tool activity</h2>
          <p className="text-sm text-muted">
            Every MCP tool call made against this organization, with who called it and how it ended.
          </p>
        </div>
        <div className="flex gap-1 self-start">
          {(['24h', '7d'] as const).map((w) => (
            <Button
              key={w}
              size="sm"
              variant={statsWindow === w ? 'primary' : 'ghost'}
              onClick={() => setStatsWindow(w)}
            >
              {w}
            </Button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
        {tiles.map((tile) => (
          <Card key={tile.label} className="p-3">
            <p className="text-xs uppercase tracking-wide text-muted">{tile.label}</p>
            <p className="text-xl font-semibold">{tile.value}</p>
          </Card>
        ))}
      </div>

      <div className="flex flex-col sm:flex-row gap-2 sm:items-end mb-4">
        <Select
          label="Tool"
          value={tool}
          onValueChange={(v) => { setOffset(0); setTool(v) }}
          options={toolOptions}
          className="sm:max-w-[220px]"
        />
        <Select
          label="Outcome"
          value={outcome}
          onValueChange={(v) => { setOffset(0); setOutcome(v) }}
          options={OUTCOME_OPTIONS}
          className="sm:max-w-[180px]"
        />
        <Input
          label="Principal"
          value={principal}
          onChange={(e) => { setOffset(0); setPrincipal(e.target.value) }}
          placeholder="ci-bot"
          className="sm:max-w-[200px]"
        />
      </div>

      {loading ? (
        <p className="text-sm text-muted">Loading…</p>
      ) : calls.length === 0 ? (
        <p className="text-sm text-muted">No tool calls recorded yet.</p>
      ) : (
        <>
          {/* Mobile: stacked cards */}
          <div className="space-y-3 md:hidden">
            {calls.map((call) => (
              <Card key={call.id} className="p-3">
                <div className="flex items-start justify-between gap-2">
                  <code className="font-mono text-xs break-all">{call.tool}</code>
                  <Badge variant={outcomeVariant(call.outcome)}>{call.outcome}</Badge>
                </div>
                {call.error && <p className="text-xs text-danger mt-1 break-words">{call.error}</p>}
                <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2 text-xs text-muted">
                  <span>{call.principal}</span>
                  <span>{projectName(call.project_id)}</span>
                  <span className="font-mono">{call.duration_ms} ms</span>
                  <span>{fmtTime(call.created_at)}</span>
                </div>
              </Card>
            ))}
          </div>

          {/* Desktop: table */}
          <Card className="hidden md:block">
            <Table>
              <Thead>
                <Tr>
                  <Th>Time</Th>
                  <Th>Principal</Th>
                  <Th>Tool</Th>
                  <Th>Project</Th>
                  <Th>Outcome</Th>
                  <Th>Duration</Th>
                </Tr>
              </Thead>
              <Tbody>
                {calls.map((call) => (
                  <Tr key={call.id}>
                    <Td className="text-xs text-muted whitespace-nowrap">{fmtTime(call.created_at)}</Td>
                    <Td className="text-xs">
                      {call.principal}
                      <span className="text-muted ml-1">({call.principal_kind})</span>
                    </Td>
                    <Td><code className="font-mono text-xs">{call.tool}</code></Td>
                    <Td className="text-xs text-muted">{projectName(call.project_id)}</Td>
                    <Td>
                      <Badge variant={outcomeVariant(call.outcome)}>{call.outcome}</Badge>
                      {call.error && (
                        <p className="text-xs text-danger mt-1 max-w-xs truncate">{call.error}</p>
                      )}
                    </Td>
                    <Td className="text-xs text-muted font-mono">{call.duration_ms} ms</Td>
                  </Tr>
                ))}
              </Tbody>
            </Table>
          </Card>

          <div className="flex items-center gap-2 mt-3">
            <Button
              size="sm"
              variant="ghost"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              Previous
            </Button>
            <span className="text-xs text-muted">
              {offset + 1}–{Math.min(offset + calls.length, total)} of {total}
            </span>
            <Button
              size="sm"
              variant="ghost"
              disabled={offset + PAGE_SIZE >= total}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              Next
            </Button>
          </div>
        </>
      )}
    </section>
  )
}
```

- [ ] **Step 4: Wrap the page in tabs**

In the default `Organization()` component, add the tab state next to the other
`useState` hooks:

```tsx
  const [tab, setTab] = useState('overview')
```

Then, inside the returned `<div className="page-wide">`, keep the header
`<div className="mb-6"> ... </div>` as is and wrap everything that follows it:

```tsx
        <Tabs value={tab} onValueChange={setTab}>
          <TabsList>
            <TabsTrigger value="overview">Overview</TabsTrigger>
            {canAddMembers && <TabsTrigger value="activity">Tool activity</TabsTrigger>}
          </TabsList>

          <TabsContent value="overview">
            {/* the existing children, moved here unchanged and in the same order:
                <ProjectsSection .../>, the error Banner, the owner-only Settings
                section, the Members section, <AddUserSection .../>,
                <McpPermissionsSection .../> */}
          </TabsContent>

          {canAddMembers && activeOrgId && (
            <TabsContent value="activity">
              <ToolActivitySection orgId={activeOrgId} />
            </TabsContent>
          )}
        </Tabs>
```

Move the existing JSX — do not retype it. The only structural change is the
nesting; no section's markup, props or handlers change.

- [ ] **Step 5: Type-check**

Run from `services/ui`: `npx tsc --noEmit`
Expected: no output (exit 0).

- [ ] **Step 6: Build**

Run from `services/ui`: `npm run build`
Expected: `built in ...`, exit 0.

- [ ] **Step 7: Commit**

```bash
git add services/ui/src/lib/api.ts services/ui/src/pages/Organization.tsx
git commit -m "add tool activity tab to Organization"
```

---

## Task 7: UI — rate limit field on MCP keys

**Files:**
- Modify: `services/ui/src/lib/api.ts`
- Modify: `services/ui/src/pages/Settings.tsx`

**Interfaces:**
- Consumes: `POST /api/mcp/keys` accepting an optional
  `rate_limit_per_minute: number` (integer 1–100000; omit for unlimited), and
  `GET /api/mcp/keys` returning `rate_limit_per_minute: number | null` per key.
- Produces: `MCPApiKey.rate_limit_per_minute` and the widened
  `api.mcpKeys.create` body in `lib/api.ts`; the create-dialog field and the
  list column in `pages/Settings.tsx`.

### Context you need

`services/ui/src/lib/api.ts` declares:

```ts
export interface MCPApiKey {
  id: number
  name: string
  scope: string
  permissions?: string
  created_at: string
  last_used_at?: string
  expires_at?: string
  created_by: number
  project_id?: number
  project_slug?: string
}
```

and its client:

```ts
    create: (body: { name: string; permissions?: string[]; scope?: string; expires_days?: number; project_id?: number }) =>
      request<{ key: string; id: number; name: string; scope: string; permissions: string; expires_at: string | null }>('/api/mcp/keys', {
        method: 'POST',
        body: JSON.stringify(body),
      }),
```

`services/ui/src/pages/Settings.tsx` has a `McpKeysTab()` component with state
`keyName`, `keyPermissions`, `expiresDays`, `keyProjectId`; a `handleCreate`
calling `api.mcpKeys.create({ name, permissions, expires_days, project_id })`
that then resets `setKeyName('')`, `setExpiresDays('')` and
`setKeyPermissions(['context-read', 'context-write'])`; a `<Table>` whose headers
are `Name | Project | Permissions | Created | Last used | Expires | (actions)`;
and a `<Dialog>` whose body ends with the "Expires in days (optional)" `<Input>`.
The `Input` component accepts `label`, `hint`, and any native input prop.

- [ ] **Step 1: Extend the API client**

In `services/ui/src/lib/api.ts`, add to `MCPApiKey` after `project_slug?: string`:

```ts
  rate_limit_per_minute?: number | null
```

and widen the `create` body type:

```ts
    create: (body: { name: string; permissions?: string[]; scope?: string; expires_days?: number; project_id?: number; rate_limit_per_minute?: number }) =>
```

- [ ] **Step 2: Add the form field**

In `McpKeysTab()` in `services/ui/src/pages/Settings.tsx`:

1. Add state next to `const [expiresDays, setExpiresDays] = useState('')`:

```tsx
  const [rateLimit, setRateLimit] = useState('')
```

2. In `handleCreate`, the create call becomes:

```tsx
      const response = await api.mcpKeys.create({
        name: keyName,
        permissions: keyPermissions,
        expires_days: expiresDays ? parseInt(expiresDays, 10) : undefined,
        project_id: keyProjectId ?? undefined,
        rate_limit_per_minute: rateLimit ? parseInt(rateLimit, 10) : undefined,
      })
```

and add `setRateLimit('')` next to `setExpiresDays('')` in the reset block that
follows `await loadKeys()`.

3. In the `<Dialog>` body, add after the "Expires in days (optional)" `<Input>`:

```tsx
          <Input
            label="Rate limit (calls/min)"
            type="number"
            min={1}
            value={rateLimit}
            onChange={e => setRateLimit(e.target.value)}
            placeholder="Leave empty for unlimited"
            hint="Applies to this key only, per server process."
          />
```

- [ ] **Step 3: Show it in the list**

In the same component's `<Table>`, add a header between `Permissions` and `Created`:

```tsx
                <Th>Rate limit</Th>
```

and the matching cell between the permissions `<Td>` and the created `<Td>`:

```tsx
                  <Td className="text-xs text-muted">
                    {key.rate_limit_per_minute ? `${key.rate_limit_per_minute}/min` : 'Unlimited'}
                  </Td>
```

- [ ] **Step 4: Type-check**

Run from `services/ui`: `npx tsc --noEmit`
Expected: no output (exit 0).

- [ ] **Step 5: Build**

Run from `services/ui`: `npm run build`
Expected: `built in ...`, exit 0.

- [ ] **Step 6: Commit**

```bash
git add services/ui/src/lib/api.ts services/ui/src/pages/Settings.tsx
git commit -m "add key rate limit field to Settings"
```

---

## Task 8: Docs, config, and full verification

**Files:**
- Modify: `README.md`
- Modify: `.env.example`
- Modify: `docker-compose.yml`

**Interfaces:**
- Consumes: everything produced by Tasks 1–7 — the `MCP_AUDIT_RETENTION_DAYS`
  setting (`src/config.py` field `mcp_audit_retention_days`, default 90), the
  **Organization → Tool activity** tab, and the **Settings → MCP keys**
  rate-limit field.
- Produces: no code. This task closes the feature with the full server suite and
  the UI checks green.

### Context you need

`.env.example` contains:

```
# ─── MCP endpoint auth ────────────────────────────
# disabled   → no authentication on :4000/mcp (local development only)
# transition → API keys and OIDC tokens are honored when present;
#              anonymous callers only get read-only tools
# enabled    → every MCP request must carry an API key (X-API-Key or
#              Authorization: Bearer) or an OIDC bearer token
MCP_AUTH_MODE=disabled
```

`docker-compose.yml` has, in the `context-forge` service `environment:` list:

```yaml
      # MCP endpoint auth: disabled | transition | enabled (see .env.example)
      - MCP_AUTH_MODE=${MCP_AUTH_MODE:-disabled}
      - ENCRYPTION_KEY=${ENCRYPTION_KEY:-}
```

`README.md` has a `## Security` section ending with a paragraph about
`ENCRYPTION_KEY`, followed by `### MCP permissions`, whose last paragraph is:

```
API keys get granular permissions chosen at creation (**Settings → MCP keys**), never more than the creator's own role allows. OIDC groups under `OIDC_GROUP_PREFIX` are only used to suggest the initial role when a user first logs in.
```

and a `## Features` list containing a **Multi-tenancy** bullet.

- [ ] **Step 1: Add the env var to `.env.example`**

Insert immediately after the `MCP_AUTH_MODE=disabled` line:

```
# Days of MCP tool-call audit history kept in mcp_tool_calls. A daily job
# deletes older rows; 0 disables the purge and keeps everything.
MCP_AUDIT_RETENTION_DAYS=90
```

- [ ] **Step 2: Add the env var to compose**

In `docker-compose.yml`, insert right after the `MCP_AUTH_MODE` line in the
`context-forge` service `environment:` list:

```yaml
      - MCP_AUDIT_RETENTION_DAYS=${MCP_AUDIT_RETENTION_DAYS:-90}
```

- [ ] **Step 3: Update the README**

1. In `## Security`, add this paragraph right before `### MCP permissions`:

```
Every MCP tool call is recorded: who called it (OIDC user, API key, or anonymous), the tool, the project, the outcome (`ok`, `denied`, `error`, `rate_limited`), the duration, and a redacted summary of the arguments — secrets, tokens and payloads never reach the log, while audited SQL is kept truncated. Org admins and owners read the trail under **Organization → Tool activity**, with a 24h/7d stats strip and filters by tool, outcome and principal. History is pruned daily according to `MCP_AUDIT_RETENTION_DAYS` (default 90).
```

2. Replace the last paragraph of `### MCP permissions` with:

```
API keys get granular permissions chosen at creation (**Settings → MCP keys**), never more than the creator's own role allows, plus an optional rate limit in calls per minute (empty = unlimited; enforced per server process). OIDC groups under `OIDC_GROUP_PREFIX` are only used to suggest the initial role when a user first logs in.
```

3. In `## Features`, append this sentence to the **Multi-tenancy** bullet:

```
Every MCP tool call is audited per organization, and API keys can carry a per-minute rate limit.
```

- [ ] **Step 4: Run the full server test suite**

Run from `services/server`:
`.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`
Expected: all tests pass, 0 failures. If anything fails, fix it before
continuing — do not proceed with a red suite.

- [ ] **Step 5: Run the UI checks**

Run from `services/ui`: `npx tsc --noEmit`
Expected: no output (exit 0).

Run from `services/ui`: `npm run build`
Expected: `built in ...`, exit 0.

Run from `services/ui`: `npx vitest run`
Expected: the existing `src/lib/tenancy.test.ts` suite passes.

- [ ] **Step 6: Confirm the results explicitly**

Report the three command outputs (the pytest summary line, the `tsc` exit
status, the `vite build` summary line) before claiming the feature is done.
Do not claim success without having seen them.

- [ ] **Step 7: Commit**

```bash
git add README.md .env.example docker-compose.yml
git commit -m "document MCP audit trail and rate limits"
```

---

## Self-review notes

- **Spec coverage.** Principal (Task 1) · migration `0003_mcp_tool_calls` with
  the table, both indexes and `rate_limit_per_minute` (Task 1) · `summarize_args`,
  `record_call` with the bounded queue, drop-oldest and the once-a-minute
  warning, the batched `unnest` writer, `audited` with the four outcomes and the
  metric increment (Task 2) · `requires_permission` wiring plus `audit_only` on
  `list_projects` / `use_project` / `current_project` (Task 2) · the sliding
  window enforced only for api-key principals with a non-null limit, with the
  documented single-process semantics (Task 3) · `MCP_AUDIT_RETENTION_DAYS` and
  the daily purge (Task 4) · both REST endpoints and `rate_limit_per_minute` on
  key create/update/list (Task 5) · the Organization **Tool activity** tab with
  stats strip, window toggle, filters, pagination and mobile cards (Task 6) ·
  the Settings field and column (Task 7) · README, `.env.example`, compose
  (Task 8). Every "Tests (no live DB)" bullet in the spec has a matching test
  file, including "`permissions_from_*` untouched", covered by the regression
  runs of `test_permissions.py` / `test_permissions_model.py` in Task 2.
- **Ambiguities resolved.** (a) The writer task starts from `src/main.py`, which
  owns both server lifecycles, instead of a FastMCP lifespan hook —
  `mcp.http_app(path="/mcp")` already owns its own lifespan in fastmcp 3.4 and
  takes no user hook here. (b) The metric is imported as `MCP_TOOL_CALLS` from
  `src/metrics.py` inside a `try/except`, so audit never raises if the symbol
  moved. (c) The stats response adds a `rate_limited` column per row and a
  `totals` block, because the UI stats strip needs a rate-limited count the
  spec's `by_tool` columns do not carry; `total` is kept for spec compliance.
  (d) `PUT /api/mcp/keys/{key_id}` is new — the spec says "POST/PUT on MCP keys
  accept `rate_limit_per_minute`", but only POST exists today.
- **Type consistency.** `Principal(kind, id, label, rate_limit_per_minute)` is
  identical in Tasks 1, 2 and 3. `audited(tool_name, permission, args_summary)`
  and `record_call(tool=, permission=, outcome=, duration_ms=, error=, args_summary=)`
  match between the module (Task 2) and its callers (Tasks 2, 3). The four
  outcome strings `ok | denied | error | rate_limited` are the same in the
  migration comment, `classify_outcome`, the stats SQL and the UI badges.
  `rate_limit_per_minute` is spelled identically in the DB column, the auth
  dict, the pydantic models, `MCPApiKey` and the UI. `ToolCallStats.totals` in
  `lib/api.ts` matches the `totals` dict built in `tool_call_stats`.
