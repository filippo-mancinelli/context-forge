# Operational Robustness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make async jobs survive restarts and transient failures, and expose the platform's operational state through Prometheus metrics and an authenticated detailed health endpoint.

**Architecture:** Job execution moves out of `asyncio.create_task` inside the MCP tool and into a scheduler tick that claims due `pending` rows with `FOR UPDATE SKIP LOCKED`, runs them concurrently, and writes back `done` / `error` / a backed-off `pending` / `dead`. A new `src/metrics.py` owns a single `prometheus_client` registry plus one SQL round-trip (`collect_queue_stats`) feeding both the `GET /metrics` exposition and the new `GET /api/health/details` route. The schema change is migration module `src/migrations/versions/0002_jobs_retry.py`.

**Tech Stack:** Python 3.11 (local dev 3.14), FastAPI, FastMCP, asyncpg (raw SQL), APScheduler 3.11, httpx 0.28, prometheus-client (new), pytest.

**Spec:** `docs/superpowers/specs/2026-09-07-ops-robustness-design.md`

## Global Constraints

Copied verbatim from `docs/superpowers/specs/2026-09-07-platform-improvements-roadmap.md`, section "Global constraints (bind every plan)":

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

Feature-specific constraints that bind every task below:

- This feature depends on feature 1 (versioned migrations) having landed: `src/migrations/runner.py` must expose `async def current_version(pool) -> int` and the package `src/migrations/versions/` must exist. If it does not, stop and report — do not recreate it.
- No new MCP tool and no new MCP permission: `job_submit` / `job_status` / `job_result` keep `@requires_permission("jobs")`.
- No UI change. Nothing under `services/ui` is touched.
- Every command runs from `services/server` unless stated otherwise.
- Test command: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/<file>`.

---

### Task 1: Retry schema (migration `0002_jobs_retry`) and the retry policy helpers

**Files:**
- Create: `services/server/src/migrations/versions/0002_jobs_retry.py`
- Modify: `services/server/src/mcp/jobs.py` (insert constants and three pure functions right after `logger = logging.getLogger(__name__)`)
- Test: `services/server/tests/test_job_retry_policy.py`

**Interfaces:**
- Consumes: the migration module contract from feature 1 — a module in `src/migrations/versions/` named `NNNN_<name>.py` exposing `VERSION: int`, `NAME: str`, `TRANSACTIONAL: bool = True`, and `async def upgrade(conn) -> None`. The runner wraps a transactional module in `async with conn.transaction()`, so the module must never open its own transaction.
- Produces, all in `src/mcp/jobs.py`:
  - `JOB_STATUSES = ("pending", "running", "done", "error", "dead")`
  - `BASE_BACKOFF_SECONDS = 5`
  - `MAX_BACKOFF_SECONDS = 300`
  - `RETRYABLE_STATUS_CODES = frozenset({408, 429})`
  - `def next_backoff_seconds(attempts: int) -> int`
  - `def classify_http_status(status_code: int) -> str` returning `"done" | "retry" | "error"`
  - `def classify_exception(exc: BaseException) -> str` returning `"retry" | "error"`
- Produces, schema: `jobs` gains `attempts INT NOT NULL DEFAULT 0`, `max_attempts INT NOT NULL DEFAULT 3`, `next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`, `last_error TEXT`, plus index `jobs_due_idx ON jobs (status, next_attempt_at)`.

**Background you need (do not go looking for it):**

The `jobs` table is created by the inline DDL string in `src/db.py`, which feature 1 froze: never edit it. Its current shape is `id UUID PRIMARY KEY`, `tool TEXT NOT NULL`, `params JSONB`, `status TEXT DEFAULT 'pending'`, `result JSONB`, `error_message TEXT`, `created_at TIMESTAMPTZ`, `updated_at TIMESTAMPTZ`, plus `org_id BIGINT` and `project_id BIGINT` added by later `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements in the same string.

Two error columns coexist after this task, on purpose. `last_error` holds the failure text of the most recent attempt and survives across retries while the job returns to `pending`. `error_message` stays the terminal error and is written only when the job reaches `error` or `dead`, so the existing readers (`src/api/routes/jobs.py`, `job_result`) keep working unchanged.

Backoff, from the spec: `5s * 4^(attempts-1)`, capped at 300 s. `attempts` is the number of attempts already made — the claim query increments it before the attempt runs — so after the 1st failure the wait is 5 s, after the 2nd 20 s, after the 3rd 80 s, after the 4th 320 s capped to 300 s.

Classification, from the spec: 2xx → `done`; 4xx except 408 and 429 → `error`, no retry; 5xx, 408, 429, timeout, connection error → `retry`.

`httpx.TimeoutException` subclasses `httpx.TransportError`, which also covers `ConnectError`, `ReadError`, `ProxyError` and `UnsupportedProtocol`. A single `isinstance(exc, httpx.TransportError)` therefore covers exactly "timeout, connection error".

A module whose name starts with a digit cannot be imported with an `import` statement. Use `importlib.import_module("src.migrations.versions.0002_jobs_retry")`.

- [ ] **Step 1: Write the failing test**

Create `services/server/tests/test_job_retry_policy.py`:

```python
"""Backoff schedule, outcome classification, and the 0002_jobs_retry migration module."""
import asyncio
import importlib
import inspect

import httpx
import pytest

from src.mcp import jobs as jobs_tools

# Module names starting with a digit need importlib, not an import statement.
migration = importlib.import_module("src.migrations.versions.0002_jobs_retry")


@pytest.mark.parametrize("attempts,expected", [(1, 5), (2, 20), (3, 80), (4, 300), (5, 300)])
def test_backoff_grows_by_four_and_caps_at_five_minutes(attempts, expected):
    assert jobs_tools.next_backoff_seconds(attempts) == expected


def test_backoff_of_zero_attempts_is_the_base_delay():
    assert jobs_tools.next_backoff_seconds(0) == 5


@pytest.mark.parametrize("code", [200, 201, 204, 299])
def test_2xx_is_done(code):
    assert jobs_tools.classify_http_status(code) == "done"


@pytest.mark.parametrize("code", [400, 401, 403, 404, 422, 451])
def test_4xx_is_a_permanent_error(code):
    assert jobs_tools.classify_http_status(code) == "error"


@pytest.mark.parametrize("code", [408, 429, 500, 502, 503, 504])
def test_5xx_408_and_429_are_retryable(code):
    assert jobs_tools.classify_http_status(code) == "retry"


def test_redirect_is_a_permanent_error():
    assert jobs_tools.classify_http_status(302) == "error"


def test_timeout_is_retryable():
    assert jobs_tools.classify_exception(httpx.ReadTimeout("slow")) == "retry"


def test_connection_error_is_retryable():
    assert jobs_tools.classify_exception(httpx.ConnectError("refused")) == "retry"


def test_programming_error_is_not_retryable():
    assert jobs_tools.classify_exception(ValueError("bad url")) == "error"


class _FakeConn:
    def __init__(self):
        self.executed = []

    async def execute(self, sql, *args):
        self.executed.append(sql)
        return "ALTER TABLE"


def test_migration_declares_the_module_contract():
    assert migration.VERSION == 2
    assert migration.NAME == "jobs_retry"
    assert migration.TRANSACTIONAL is True


def test_migration_adds_the_four_retry_columns_and_the_due_index():
    conn = _FakeConn()
    asyncio.run(migration.upgrade(conn))
    sql = " ".join(conn.executed).lower()
    assert "attempts int not null default 0" in sql
    assert "max_attempts int not null default 3" in sql
    assert "next_attempt_at timestamptz not null default now()" in sql
    assert "last_error text" in sql
    assert "jobs_due_idx" in sql
    assert sql.count("add column if not exists") == 4


def test_migration_does_not_open_its_own_transaction():
    """The runner wraps TRANSACTIONAL modules; the module must not nest one."""
    assert "transaction()" not in inspect.getsource(migration.upgrade)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_job_retry_policy.py`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'src.migrations.versions.0002_jobs_retry'`.

- [ ] **Step 3: Write the migration module**

Create `services/server/src/migrations/versions/0002_jobs_retry.py`:

```python
"""Durable retries for async jobs: attempt counters, schedule and last error."""
from __future__ import annotations

VERSION = 2
NAME = "jobs_retry"
TRANSACTIONAL = True


async def upgrade(conn) -> None:
    await conn.execute(
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS attempts INT NOT NULL DEFAULT 0"
    )
    await conn.execute(
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS max_attempts INT NOT NULL DEFAULT 3"
    )
    await conn.execute(
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS next_attempt_at "
        "TIMESTAMPTZ NOT NULL DEFAULT NOW()"
    )
    await conn.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS last_error TEXT")
    # Serves the scheduler's claim query.
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS jobs_due_idx ON jobs (status, next_attempt_at)"
    )
```

- [ ] **Step 4: Write the policy helpers**

In `services/server/src/mcp/jobs.py`, insert immediately after the existing `logger = logging.getLogger(__name__)` line:

```python
JOB_STATUSES = ("pending", "running", "done", "error", "dead")
BASE_BACKOFF_SECONDS = 5
MAX_BACKOFF_SECONDS = 300
RETRYABLE_STATUS_CODES = frozenset({408, 429})


def next_backoff_seconds(attempts: int) -> int:
    """Wait before the next attempt: 5s * 4^(attempts-1), capped at five minutes."""
    exponent = max(attempts - 1, 0)
    return min(BASE_BACKOFF_SECONDS * (4 ** exponent), MAX_BACKOFF_SECONDS)


def classify_http_status(status_code: int) -> str:
    """Map an HTTP response status to 'done', 'retry' or 'error'."""
    if 200 <= status_code < 300:
        return "done"
    if status_code in RETRYABLE_STATUS_CODES or status_code >= 500:
        return "retry"
    return "error"


def classify_exception(exc: BaseException) -> str:
    """Timeouts and connection failures are retryable; anything else is permanent."""
    return "retry" if isinstance(exc, httpx.TransportError) else "error"
```

`httpx` is already imported at the top of that file. Leave the existing `_execute_http_job` in place — Task 2 replaces it.

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_job_retry_policy.py`
Expected: PASS, 29 tests.

- [ ] **Step 6: Commit**

```bash
git add services/server/src/migrations/versions/0002_jobs_retry.py services/server/src/mcp/jobs.py services/server/tests/test_job_retry_policy.py
git commit -m "add job retry columns and backoff policy"
```

---

### Task 2: Scheduler-owned job executor with claim, retry and stuck reset

**Files:**
- Modify: `services/server/src/mcp/jobs.py` (delete `_execute_http_job`, add `finalize_job` and `run_claimed_job`)
- Modify: `services/server/src/scheduler.py` (module-level imports, `_check_jobs`, registration in `start_scheduler`)
- Test: `services/server/tests/test_job_executor.py`

**Interfaces:**
- Consumes, from Task 1, all in `src/mcp/jobs.py`:
  - `def next_backoff_seconds(attempts: int) -> int` — `5s * 4^(attempts-1)` capped at 300.
  - `def classify_http_status(status_code: int) -> str` — `"done" | "retry" | "error"`.
  - `def classify_exception(exc: BaseException) -> str` — `"retry" | "error"`.
  - Table `jobs` with `attempts`, `max_attempts`, `next_attempt_at`, `last_error`.
- Produces:
  - `src/mcp/jobs.py`: `async def finalize_job(job_id: str, outcome: str, attempts: int, max_attempts: int, result, error: str | None) -> str` — writes the attempt's outcome and returns the new job status (`"done" | "pending" | "error" | "dead"`).
  - `src/mcp/jobs.py`: `async def run_claimed_job(job: dict) -> None` — runs one claimed row (keys `id`, `params`, `attempts`, `max_attempts`) and calls `finalize_job`.
  - `src/scheduler.py`: `async def _check_jobs() -> None`, registered as APScheduler job id `"jobs"` on a 5-second interval.
  - `src/scheduler.py` gains module-level `from .db import get_pool` and `from .mcp.jobs import run_claimed_job` so tests can monkeypatch `scheduler.get_pool` and `scheduler.run_claimed_job`.

**Background you need (do not go looking for it):**

`src/mcp/jobs.py` today runs jobs in a fire-and-forget task created by `job_submit`:

```python
async def _execute_http_job(job_id: str, url: str, method: str, payload: dict, headers: dict) -> None:
    """Background task: run HTTP call and update job status in DB."""
    ...
```

Delete that function entirely in this task. The `asyncio.create_task(_execute_http_job(...))` call inside `job_submit` is removed in Task 3; until then leave `job_submit` alone, and keep `import asyncio` at the top of the module (Task 2 uses it in the scheduler only, but `job_submit` still references it).

`jobs.params` is a JSONB column holding `{"url": ..., "method": ..., "payload": {...}, "headers": {...}}`, written by `job_submit` as `json.dumps(...)`. asyncpg may hand it back as a `str` or as a `dict` depending on codec registration, so normalize both.

The scheduler module currently declares its periodic jobs like this, inside `async def start_scheduler()`:

```python
    _scheduler.add_job(
        _check_index_requests, "interval", seconds=10, id="index_requests", replace_existing=True
    )
```

Existing scheduler imports are module-level (`from .indexer.git_manager import pull_all_repos`, `from .mcp.oauth_bridge import purge_expired_flows`, ...), so adding `from .db import get_pool` and `from .mcp.jobs import run_claimed_job` at module level introduces no import cycle — this was verified.

The existing repo test pattern for a fake pool (see `tests/test_oauth_bridge_reaper.py`) is:

```python
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
```

- [ ] **Step 1: Write the failing test**

Create `services/server/tests/test_job_executor.py`:

```python
"""Scheduler-owned job execution: claim, run, finalise, retry, dead letter, stuck reset."""
import asyncio
import inspect
import json

import httpx
import pytest

from src import scheduler
from src.mcp import jobs as jobs_tools


class FakeConn:
    def __init__(self, fetch_rows=None):
        self.fetch_rows = fetch_rows or []
        self.executed = []
        self.fetched = []

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        return "UPDATE 1"

    async def fetch(self, sql, *args):
        self.fetched.append((sql, args))
        return self.fetch_rows


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


def _wire_pool(monkeypatch, module, conn):
    async def fake_pool():
        return FakePool(conn)

    monkeypatch.setattr(module, "get_pool", fake_pool)
    return conn


# ── finalize_job ──────────────────────────────────────────────────────────────

def test_success_marks_the_job_done(monkeypatch):
    conn = _wire_pool(monkeypatch, jobs_tools, FakeConn())
    status = asyncio.run(
        jobs_tools.finalize_job("j1", "done", 1, 3, {"ok": True}, None)
    )
    assert status == "done"
    sql, args = conn.executed[0]
    assert "UPDATE jobs" in sql
    assert args[0] == "done"


def test_permanent_error_does_not_retry(monkeypatch):
    _wire_pool(monkeypatch, jobs_tools, FakeConn())
    status = asyncio.run(jobs_tools.finalize_job("j1", "error", 1, 3, None, "HTTP 404"))
    assert status == "error"


def test_retryable_failure_goes_back_to_pending_with_backoff(monkeypatch):
    conn = _wire_pool(monkeypatch, jobs_tools, FakeConn())
    status = asyncio.run(jobs_tools.finalize_job("j1", "retry", 1, 3, None, "HTTP 503"))
    assert status == "pending"
    _sql, args = conn.executed[0]
    # delay parameter carries the 5s backoff for the first failed attempt
    assert 5.0 in args


def test_retryable_failure_at_max_attempts_is_dead_lettered(monkeypatch):
    conn = _wire_pool(monkeypatch, jobs_tools, FakeConn())
    status = asyncio.run(jobs_tools.finalize_job("j1", "retry", 3, 3, None, "HTTP 503"))
    assert status == "dead"
    _sql, args = conn.executed[0]
    assert args[0] == "dead"


def test_terminal_states_write_error_message_but_retries_do_not(monkeypatch):
    conn = _wire_pool(monkeypatch, jobs_tools, FakeConn())
    asyncio.run(jobs_tools.finalize_job("j1", "retry", 1, 3, None, "HTTP 503"))
    _sql, retry_args = conn.executed[0]
    conn.executed.clear()
    asyncio.run(jobs_tools.finalize_job("j1", "error", 1, 3, None, "HTTP 404"))
    _sql, error_args = conn.executed[0]
    # last_error is always written; error_message only on terminal states.
    assert "HTTP 503" in retry_args and None in retry_args
    assert error_args.count("HTTP 404") == 2


# ── run_claimed_job ───────────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeClient:
    def __init__(self, response=None, raises=None):
        self.response = response
        self.raises = raises
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        self.calls.append(("POST", url, json, headers))
        if self.raises:
            raise self.raises
        return self.response

    async def get(self, url, headers=None):
        self.calls.append(("GET", url, None, headers))
        if self.raises:
            raise self.raises
        return self.response


def _capture_finalize(monkeypatch):
    captured = {}

    async def fake_finalize(job_id, outcome, attempts, max_attempts, result, error):
        captured.update(
            job_id=job_id, outcome=outcome, attempts=attempts,
            max_attempts=max_attempts, result=result, error=error,
        )
        return "done"

    monkeypatch.setattr(jobs_tools, "finalize_job", fake_finalize)
    return captured


def test_run_claimed_job_posts_the_stored_params(monkeypatch):
    client = FakeClient(FakeResponse(200, {"answer": 42}))
    monkeypatch.setattr(jobs_tools.httpx, "AsyncClient", lambda *a, **kw: client)
    captured = _capture_finalize(monkeypatch)

    job = {
        "id": "j1",
        "params": json.dumps({
            "url": "http://svc/api", "method": "POST",
            "payload": {"q": "hi"}, "headers": {"X": "1"},
        }),
        "attempts": 1,
        "max_attempts": 3,
    }
    asyncio.run(jobs_tools.run_claimed_job(job))

    assert client.calls == [("POST", "http://svc/api", {"q": "hi"}, {"X": "1"})]
    assert captured["outcome"] == "done"
    assert captured["result"] == {"answer": 42}


def test_run_claimed_job_accepts_params_already_decoded(monkeypatch):
    client = FakeClient(FakeResponse(200, {"ok": True}))
    monkeypatch.setattr(jobs_tools.httpx, "AsyncClient", lambda *a, **kw: client)
    captured = _capture_finalize(monkeypatch)

    job = {"id": "j1", "params": {"url": "http://svc", "method": "GET"},
           "attempts": 1, "max_attempts": 3}
    asyncio.run(jobs_tools.run_claimed_job(job))

    assert client.calls[0][0] == "GET"
    assert captured["outcome"] == "done"


def test_run_claimed_job_classifies_a_5xx_as_retry(monkeypatch):
    client = FakeClient(FakeResponse(503, None, "boom"))
    monkeypatch.setattr(jobs_tools.httpx, "AsyncClient", lambda *a, **kw: client)
    captured = _capture_finalize(monkeypatch)

    asyncio.run(jobs_tools.run_claimed_job(
        {"id": "j1", "params": {"url": "http://svc"}, "attempts": 2, "max_attempts": 3}
    ))

    assert captured["outcome"] == "retry"
    assert captured["error"] == "HTTP 503"
    assert captured["attempts"] == 2


def test_run_claimed_job_classifies_a_timeout_as_retry(monkeypatch):
    client = FakeClient(raises=httpx.ReadTimeout("slow"))
    monkeypatch.setattr(jobs_tools.httpx, "AsyncClient", lambda *a, **kw: client)
    captured = _capture_finalize(monkeypatch)

    asyncio.run(jobs_tools.run_claimed_job(
        {"id": "j1", "params": {"url": "http://svc"}, "attempts": 1, "max_attempts": 3}
    ))

    assert captured["outcome"] == "retry"


def test_run_claimed_job_classifies_a_404_as_permanent_error(monkeypatch):
    client = FakeClient(FakeResponse(404, {"detail": "nope"}))
    monkeypatch.setattr(jobs_tools.httpx, "AsyncClient", lambda *a, **kw: client)
    captured = _capture_finalize(monkeypatch)

    asyncio.run(jobs_tools.run_claimed_job(
        {"id": "j1", "params": {"url": "http://svc"}, "attempts": 1, "max_attempts": 3}
    ))

    assert captured["outcome"] == "error"


# ── _check_jobs ───────────────────────────────────────────────────────────────

def test_check_jobs_resets_stuck_running_rows_then_claims(monkeypatch):
    conn = FakeConn(fetch_rows=[])
    _wire_pool(monkeypatch, scheduler, conn)

    asyncio.run(scheduler._check_jobs())

    reset_sql = conn.executed[0][0]
    assert "status = 'running'" in reset_sql
    assert "status = 'pending'" in reset_sql
    assert "10 minutes" in reset_sql
    claim_sql = conn.fetched[0][0]
    assert "FOR UPDATE SKIP LOCKED" in claim_sql
    assert "attempts = attempts + 1" in claim_sql
    assert "next_attempt_at <= NOW()" in claim_sql
    assert "LIMIT 5" in claim_sql


def test_check_jobs_runs_every_claimed_row(monkeypatch):
    rows = [
        {"id": "j1", "params": {}, "attempts": 1, "max_attempts": 3},
        {"id": "j2", "params": {}, "attempts": 1, "max_attempts": 3},
    ]
    _wire_pool(monkeypatch, scheduler, FakeConn(fetch_rows=rows))
    ran = []

    async def fake_run(job):
        ran.append(job["id"])

    monkeypatch.setattr(scheduler, "run_claimed_job", fake_run)
    asyncio.run(scheduler._check_jobs())

    assert sorted(ran) == ["j1", "j2"]


def test_check_jobs_survives_one_failing_job(monkeypatch):
    rows = [
        {"id": "j1", "params": {}, "attempts": 1, "max_attempts": 3},
        {"id": "j2", "params": {}, "attempts": 1, "max_attempts": 3},
    ]
    _wire_pool(monkeypatch, scheduler, FakeConn(fetch_rows=rows))
    ran = []

    async def fake_run(job):
        if job["id"] == "j1":
            raise RuntimeError("boom")
        ran.append(job["id"])

    monkeypatch.setattr(scheduler, "run_claimed_job", fake_run)
    asyncio.run(scheduler._check_jobs())

    assert ran == ["j2"]


def test_scheduler_registers_the_jobs_tick_every_five_seconds():
    source = inspect.getsource(scheduler.start_scheduler)
    assert "_check_jobs" in source
    assert 'id="jobs"' in source
    assert "seconds=5" in source
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_job_executor.py`
Expected: FAIL with `AttributeError: module 'src.mcp.jobs' has no attribute 'finalize_job'` and `module 'src.scheduler' has no attribute 'get_pool'`.

- [ ] **Step 3: Add `finalize_job` and `run_claimed_job`, delete `_execute_http_job`**

In `services/server/src/mcp/jobs.py`, delete the whole `async def _execute_http_job(...)` function and put this in its place:

```python
async def finalize_job(
    job_id: str,
    outcome: str,
    attempts: int,
    max_attempts: int,
    result: Any = None,
    error: Optional[str] = None,
) -> str:
    """Write one attempt's outcome. Returns the resulting job status."""
    if outcome == "done":
        status, delay = "done", 0
    elif outcome == "retry" and attempts < max_attempts:
        status, delay = "pending", next_backoff_seconds(attempts)
    elif outcome == "retry":
        status, delay = "dead", 0
    else:
        status, delay = "error", 0

    terminal_error = error if status in ("error", "dead") else None
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE jobs
            SET status = $1,
                result = COALESCE($2::jsonb, result),
                last_error = $3,
                error_message = COALESCE($4, error_message),
                next_attempt_at = NOW() + make_interval(secs => $5),
                updated_at = NOW()
            WHERE id = $6
            """,
            status,
            json.dumps(result) if result is not None else None,
            error,
            terminal_error,
            float(delay),
            job_id,
        )
    return status


async def run_claimed_job(job: dict) -> None:
    """Execute one claimed job row and persist its outcome."""
    job_id = str(job["id"])
    params = job["params"]
    if isinstance(params, str):
        params = json.loads(params)
    params = params or {}

    url = params.get("url", "")
    method = (params.get("method") or "POST").upper()
    payload = params.get("payload") or {}
    headers = params.get("headers") or {}

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            if method == "GET":
                resp = await client.get(url, headers=headers)
            else:
                resp = await client.post(url, json=payload, headers=headers)
        try:
            result_data = resp.json()
        except Exception:
            result_data = {"text": resp.text, "status_code": resp.status_code}
        outcome = classify_http_status(resp.status_code)
        error = None if outcome == "done" else f"HTTP {resp.status_code}"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Job %s attempt %s failed: %s", job_id, job["attempts"], exc)
        result_data = None
        outcome = classify_exception(exc)
        error = str(exc) or exc.__class__.__name__

    await finalize_job(
        job_id, outcome, int(job["attempts"]), int(job["max_attempts"]), result_data, error
    )
```

`Any`, `Optional`, `json`, `httpx`, `logger` and `get_pool` are already imported at the top of the module.

- [ ] **Step 4: Add the scheduler tick**

In `services/server/src/scheduler.py`, add to the module-level imports (next to `from .mcp.oauth_bridge import purge_expired_flows`):

```python
from .db import get_pool
from .mcp.jobs import run_claimed_job
```

Add `import asyncio` to the top of the file if it is not there, and add this function next to the other `_check_*` functions:

```python
_JOB_CLAIM_BATCH = 5
_JOB_STUCK_MINUTES = 10


async def _check_jobs() -> None:
    """Requeue jobs stuck in 'running', then claim and run the jobs that are due."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE jobs SET status = 'pending', updated_at = NOW() "
            "WHERE status = 'running' AND updated_at < NOW() - INTERVAL '10 minutes'"
        )
        rows = await conn.fetch(
            """
            UPDATE jobs
            SET status = 'running', attempts = attempts + 1, updated_at = NOW()
            WHERE id IN (
                SELECT id FROM jobs
                WHERE status = 'pending' AND next_attempt_at <= NOW()
                ORDER BY next_attempt_at
                LIMIT 5
                FOR UPDATE SKIP LOCKED
            )
            RETURNING id, params, attempts, max_attempts
            """
        )

    if not rows:
        return
    await asyncio.gather(
        *(run_claimed_job(dict(row)) for row in rows), return_exceptions=True
    )
```

In `start_scheduler`, register it right after the `index_requests` job:

```python
    # Claim and run due async jobs; also requeues jobs stranded by a crash.
    _scheduler.add_job(
        _check_jobs, "interval", seconds=5, id="jobs", replace_existing=True
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_job_executor.py`
Expected: PASS, 14 tests.

- [ ] **Step 6: Run the neighbouring suites that touch jobs and the scheduler**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_jobs_project_scope.py tests/test_oauth_bridge_reaper.py tests/test_job_retry_policy.py`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add services/server/src/mcp/jobs.py services/server/src/scheduler.py services/server/tests/test_job_executor.py
git commit -m "run jobs from the scheduler with retries"
```

---

### Task 3: Job tools report attempts and accept `max_attempts`

**Files:**
- Modify: `services/server/src/mcp/jobs.py` (`job_submit`, `job_status`, `job_result`)
- Test: `services/server/tests/test_job_tools_retry.py`

**Interfaces:**
- Consumes, from Task 1 and 2: the `jobs` columns `attempts`, `max_attempts`, `next_attempt_at`, `last_error`; the `dead` status; and the fact that `src/scheduler.py::_check_jobs` (5 s interval) is now the only thing that executes jobs, so `job_submit` must not create a task.
- Produces: `job_submit(url, method="POST", payload=None, headers=None, max_attempts=3)` inserting `max_attempts` and returning the unchanged shape `{"status": "ok", "job_id": ..., "message": ...}`; `job_status` returning `attempts`, `max_attempts`, `next_attempt_at`; `job_result` returning a `dead` job as an error with its `last_error`.

**Background you need (do not go looking for it):**

The three tools are decorated exactly like this and must keep both decorators, in this order:

```python
@mcp.tool()
@requires_permission("jobs")
async def job_submit(...):
```

`job_submit` today ends with:

```python
    asyncio.create_task(
        _execute_http_job(job_id, url, method, payload or {}, headers or {})
    )

    return {
        "status": "ok",
        "job_id": job_id,
        "message": f"Job submitted. Poll with job_status('{job_id}')",
    }
```

`_execute_http_job` no longer exists after Task 2 — remove the `asyncio.create_task(...)` call. `import asyncio` at the top of `src/mcp/jobs.py` becomes unused in this module and must be removed too.

`job_submit` resolves tenancy with a function-local import that must stay:

```python
    from .context import resolve_org_id, require_project_id
    ...
    org_id = await resolve_org_id()
    project_id = await require_project_id()
```

`job_status` and `job_result` scope their reads with `project_id = await require_project_id()` and a `WHERE id = $1 AND project_id = $2` clause. `tests/test_jobs_project_scope.py` asserts by source inspection that `job_submit` contains `resolve_org_id`, `require_project_id` and the literal `org_id, project_id`, and that `job_status` / `job_result` mention `project_id` — keep all of that intact.

The existing `job_status` return shape, which must be preserved and extended:

```python
    return {
        "status": "ok",
        "job_id": job_id,
        "job_status": row["status"],
        "error_message": row["error_message"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
        "hint": "Call job_result() to get the full result when job_status is 'done'",
    }
```

FastMCP wraps tool functions. Call the underlying coroutine in tests with `jobs_tools.job_submit.fn(...)` when the attribute exists, falling back to the plain callable — the helper below does that for you.

- [ ] **Step 1: Write the failing test**

Create `services/server/tests/test_job_tools_retry.py`:

```python
"""job_submit / job_status / job_result under the retry model."""
import asyncio
import datetime as dt
import inspect
import json

import pytest

from src.mcp import jobs as jobs_tools


def _fn(tool):
    """`@mcp.tool()` returns the function itself here; `.fn` is the fallback if that changes."""
    return getattr(tool, "fn", tool)


class FakeConn:
    def __init__(self, row=None):
        self.row = row
        self.executed = []

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        return "INSERT 0 1"

    async def fetchrow(self, sql, *args):
        return self.row


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


@pytest.fixture
def wired(monkeypatch):
    conn = FakeConn()

    async def fake_pool():
        return FakePool(conn)

    async def fake_org():
        return 1

    async def fake_project():
        return 42

    monkeypatch.setattr(jobs_tools, "get_pool", fake_pool)
    monkeypatch.setattr("src.mcp.context.resolve_org_id", fake_org)
    monkeypatch.setattr("src.mcp.context.require_project_id", fake_project)
    return conn


NOW = dt.datetime(2026, 9, 7, 10, 0, 0, tzinfo=dt.timezone.utc)


# ── job_submit ────────────────────────────────────────────────────────────────

def test_submit_only_inserts_and_never_starts_a_task(wired):
    out = asyncio.run(_fn(jobs_tools.job_submit)(url="http://svc/api"))
    assert out["status"] == "ok"
    assert "job_id" in out
    sql, _args = wired.executed[0]
    assert "INSERT INTO jobs" in sql
    assert "asyncio.create_task" not in inspect.getsource(_fn(jobs_tools.job_submit))


def test_submit_persists_max_attempts(wired):
    asyncio.run(_fn(jobs_tools.job_submit)(url="http://svc/api", max_attempts=7))
    sql, args = wired.executed[0]
    assert "max_attempts" in sql
    # args = (job_id, params_json, org_id, project_id, max_attempts)
    assert args[4] == 7


def test_submit_defaults_to_three_attempts(wired):
    asyncio.run(_fn(jobs_tools.job_submit)(url="http://svc/api"))
    _sql, args = wired.executed[0]
    assert args[4] == 3


@pytest.mark.parametrize("bad", [0, -1, 11, 100])
def test_submit_rejects_out_of_range_max_attempts(wired, bad):
    out = asyncio.run(_fn(jobs_tools.job_submit)(url="http://svc/api", max_attempts=bad))
    assert out["status"] == "error"
    assert "max_attempts" in out["error"]
    assert wired.executed == []


# ── job_status ────────────────────────────────────────────────────────────────

def _status_row(status="pending", attempts=1):
    return {
        "id": "j1",
        "status": status,
        "error_message": None,
        "attempts": attempts,
        "max_attempts": 3,
        "next_attempt_at": NOW,
        "last_error": "HTTP 503",
        "created_at": NOW,
        "updated_at": NOW,
    }


def test_status_reports_attempt_counters(monkeypatch):
    conn = FakeConn(row=_status_row(attempts=2))

    async def fake_pool():
        return FakePool(conn)

    async def fake_project():
        return 7

    monkeypatch.setattr(jobs_tools, "get_pool", fake_pool)
    monkeypatch.setattr("src.mcp.context.require_project_id", fake_project)

    out = asyncio.run(_fn(jobs_tools.job_status)("j1"))
    assert out["attempts"] == 2
    assert out["max_attempts"] == 3
    assert out["next_attempt_at"] == NOW.isoformat()
    assert out["job_status"] == "pending"
    assert out["last_error"] == "HTTP 503"


def test_status_surfaces_the_dead_state(monkeypatch):
    conn = FakeConn(row=_status_row(status="dead", attempts=3))

    async def fake_pool():
        return FakePool(conn)

    async def fake_project():
        return 7

    monkeypatch.setattr(jobs_tools, "get_pool", fake_pool)
    monkeypatch.setattr("src.mcp.context.require_project_id", fake_project)

    out = asyncio.run(_fn(jobs_tools.job_status)("j1"))
    assert out["job_status"] == "dead"


# ── job_result ────────────────────────────────────────────────────────────────

def _result_row(status, result=None):
    return {
        "id": "j1",
        "status": status,
        "result": result,
        "error_message": "HTTP 503",
        "last_error": "HTTP 503",
        "attempts": 3,
        "max_attempts": 3,
    }


def _run_result(monkeypatch, row):
    conn = FakeConn(row=row)

    async def fake_pool():
        return FakePool(conn)

    async def fake_project():
        return 7

    monkeypatch.setattr(jobs_tools, "get_pool", fake_pool)
    monkeypatch.setattr("src.mcp.context.require_project_id", fake_project)
    return asyncio.run(_fn(jobs_tools.job_result)("j1"))


def test_result_of_a_dead_job_is_an_error_with_the_attempt_count(monkeypatch):
    out = _run_result(monkeypatch, _result_row("dead"))
    assert out["status"] == "error"
    assert out["job_status"] == "dead"
    assert out["error"] == "HTTP 503"
    assert out["attempts"] == 3


def test_result_of_a_pending_job_is_not_ready(monkeypatch):
    out = _run_result(monkeypatch, _result_row("pending"))
    assert out["status"] == "not_ready"


def test_result_of_a_done_job_decodes_json(monkeypatch):
    out = _run_result(monkeypatch, _result_row("done", json.dumps({"a": 1})))
    assert out["status"] == "ok"
    assert out["result"] == {"a": 1}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_job_tools_retry.py`
Expected: FAIL — `job_submit` has no `max_attempts` parameter (`TypeError: ... got an unexpected keyword argument 'max_attempts'`).

- [ ] **Step 3: Rewrite the three tools**

In `services/server/src/mcp/jobs.py`, remove the now-unused `import asyncio` from the top of the file, then replace the three tool bodies.

`job_submit` — signature, docstring addition, validation, insert, and the removal of the task creation:

```python
@mcp.tool()
@requires_permission("jobs")
async def job_submit(
    url: str,
    method: str = "POST",
    payload: Optional[dict] = None,
    headers: Optional[dict] = None,
    max_attempts: int = 3,
) -> dict:
    """Submit a long-running HTTP request as an async background job.

    Returns immediately with a job_id. Use job_status() to poll for completion
    and job_result() to retrieve the result.

    Ideal for slow AI agents, data pipelines, or any HTTP endpoint that takes
    more than a few seconds to respond (which would otherwise cause MCP timeouts).

    The job is executed by the server's scheduler, so it survives a restart.
    Transient failures (5xx, 408, 429, timeouts, connection errors) are retried
    with exponential backoff; after max_attempts the job becomes "dead".

    Args:
        url: The HTTP URL to call
        method: HTTP method: "GET" or "POST" (default "POST")
        payload: Request body as a dict (for POST requests)
        headers: Optional HTTP headers (e.g. {"Authorization": "Bearer token"})
        max_attempts: How many times to try before dead-lettering (1-10, default 3)

    Returns:
        dict with job_id to use with job_status() and job_result()
    """
    from .context import resolve_org_id, require_project_id

    if not 1 <= max_attempts <= 10:
        return {"status": "error", "error": "max_attempts must be between 1 and 10"}

    job_id = str(uuid.uuid4())
    org_id = await resolve_org_id()
    project_id = await require_project_id()
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO jobs (id, tool, params, status, org_id, project_id,
                              max_attempts, next_attempt_at)
            VALUES ($1, 'http', $2, 'pending', $3, $4, $5, NOW())
            """,
            job_id,
            json.dumps({"url": url, "method": method, "payload": payload or {}, "headers": headers or {}}),
            org_id,
            project_id,
            max_attempts,
        )

    return {
        "status": "ok",
        "job_id": job_id,
        "message": f"Job submitted. Poll with job_status('{job_id}')",
    }
```

`job_status` — widen the SELECT and the returned dict:

```python
@mcp.tool()
@requires_permission("jobs")
async def job_status(job_id: str) -> dict:
    """Check the status of a submitted async job.

    Args:
        job_id: The job ID returned by job_submit()

    Returns:
        dict with status: "pending" | "running" | "done" | "error" | "dead",
        the attempt counters, and when the next attempt is due.
    """
    from .context import require_project_id

    project_id = await require_project_id()
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, status, error_message, attempts, max_attempts, "
            "next_attempt_at, last_error, created_at, updated_at "
            "FROM jobs WHERE id = $1 AND project_id = $2",
            job_id, project_id,
        )

    if not row:
        return {"status": "error", "error": f"Job not found: {job_id}"}

    next_attempt_at = row["next_attempt_at"]
    return {
        "status": "ok",
        "job_id": job_id,
        "job_status": row["status"],
        "error_message": row["error_message"],
        "last_error": row["last_error"],
        "attempts": row["attempts"],
        "max_attempts": row["max_attempts"],
        "next_attempt_at": next_attempt_at.isoformat() if next_attempt_at else None,
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
        "hint": "Call job_result() to get the full result when job_status is 'done'",
    }
```

`job_result` — handle `dead` alongside `error`:

```python
@mcp.tool()
@requires_permission("jobs")
async def job_result(job_id: str) -> dict:
    """Retrieve the result of a completed async job.

    Args:
        job_id: The job ID returned by job_submit()

    Returns:
        dict with the job result, or an error if the job is not yet done, failed,
        or exhausted its retries ("dead").
    """
    from .context import require_project_id

    project_id = await require_project_id()
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, status, result, error_message, last_error, attempts, max_attempts "
            "FROM jobs WHERE id = $1 AND project_id = $2",
            job_id, project_id,
        )

    if not row:
        return {"status": "error", "error": f"Job not found: {job_id}"}

    if row["status"] in ("pending", "running"):
        return {
            "status": "not_ready",
            "job_status": row["status"],
            "attempts": row["attempts"],
            "max_attempts": row["max_attempts"],
            "message": f"Job is still {row['status']}. Try again in a few seconds.",
        }

    if row["status"] in ("error", "dead"):
        return {
            "status": "error",
            "job_id": job_id,
            "job_status": row["status"],
            "error": row["error_message"] or row["last_error"],
            "attempts": row["attempts"],
            "max_attempts": row["max_attempts"],
        }

    raw_result = row["result"]
    result = json.loads(raw_result) if isinstance(raw_result, str) else raw_result
    return {
        "status": "ok",
        "job_id": job_id,
        "result": result,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_job_tools_retry.py`
Expected: PASS, 12 tests.

- [ ] **Step 5: Confirm the existing project-scope test still holds**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_jobs_project_scope.py tests/test_job_executor.py tests/test_job_retry_policy.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/server/src/mcp/jobs.py services/server/tests/test_job_tools_retry.py
git commit -m "expose job attempts and dead state"
```

---

### Task 4: Metrics registry and the `/metrics` endpoint

**Files:**
- Modify: `services/server/pyproject.toml` (add `prometheus-client>=0.20`)
- Create: `services/server/src/metrics.py`
- Modify: `services/server/src/config.py` (add `metrics_token` to `Settings`)
- Modify: `services/server/src/api/app.py` (add the `/metrics` route after the `/api/health` route)
- Test: `services/server/tests/test_metrics_endpoint.py`

**Interfaces:**
- Consumes, from Task 1: `JOB_STATUSES = ("pending", "running", "done", "error", "dead")` in `src/mcp/jobs.py`.
- Produces — **this is the public interface of `src/metrics.py`, stated verbatim; later features import these names and must find them unchanged**:

```python
registry = CollectorRegistry()

jobs_by_status = Gauge(
    "contextforge_jobs", "Async jobs by status", ["status"], registry=registry
)
index_requests_pending = Gauge(
    "contextforge_index_requests_pending", "Index requests not yet processed",
    registry=registry,
)
index_requests_oldest_age_seconds = Gauge(
    "contextforge_index_requests_oldest_age_seconds",
    "Age of the oldest unprocessed index request", registry=registry,
)
kb_documents_pending = Gauge(
    "contextforge_kb_documents_pending", "Knowledge-base documents awaiting processing",
    registry=registry,
)
web_pages_pending = Gauge(
    "contextforge_web_pages_pending", "Web pages awaiting processing", registry=registry
)
scheduler_tick_timestamp_seconds = Gauge(
    "contextforge_scheduler_tick_timestamp_seconds",
    "Unix time of the last scheduler metrics tick", registry=registry,
)
schema_version = Gauge(
    "contextforge_schema_version", "Applied schema migration version", registry=registry
)
mcp_tool_calls_total = Counter(
    "contextforge_mcp_tool_calls_total", "MCP tool calls", ["tool", "outcome"],
    registry=registry,
)


def render_metrics() -> bytes: ...
```

  `mcp_tool_calls_total` is declared here and left at zero: the MCP audit feature (roadmap item 4) increments it with `mcp_tool_calls_total.labels(tool=..., outcome=...).inc()`. Do not add an org-id label to any metric.
- Produces: `Settings.metrics_token: str = ""` in `src/config.py`.
- Produces: `GET /metrics` on the FastAPI `api` app, text exposition format.

**Background you need (do not go looking for it):**

`prometheus_client` strips a trailing `_total` from a `Counter` name internally and appends it back at exposition time, so declaring `Counter("contextforge_mcp_tool_calls_total", ...)` exposes exactly `contextforge_mcp_tool_calls_total`. Do not name it `contextforge_mcp_tool_calls`.

A `Gauge` with labels exports no sample until a label combination is used, so the labelled `contextforge_jobs` series must be pre-initialised to 0 at import time for every status. A metric with labels and no children still emits its `# HELP` / `# TYPE` lines, which is what the `mcp_tool_calls_total` assertion relies on.

`src/api/app.py` has an `auth_guard` HTTP middleware whose first real check is:

```python
    if not path.startswith("/api"):
        return await call_next(request)
```

so `/metrics` bypasses the session guard entirely and must enforce `METRICS_TOKEN` itself. The health route below the middleware is the model for where to put the new route:

```python
@api.get("/api/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "context-forge-api"}
```

`Settings` in `src/config.py` is a `pydantic_settings.BaseSettings` subclass whose fields map to upper-case env vars; add the new field near `log_level`.

The venv already contains `httpx`, `fastapi` and `pytest`; `prometheus-client` is **not** installed yet and this task installs it.

- [ ] **Step 1: Add the dependency and install it**

In `services/server/pyproject.toml`, inside `[project].dependencies`, add after the `# Scheduling` block:

```toml
    # Metrics
    "prometheus-client>=0.20",
```

Run from `services/server`:

```bash
uv pip install --python .venv/Scripts/python.exe -e .
```

Verify: `.venv/Scripts/python.exe -c "import prometheus_client; print('ok')"` prints `ok`.

- [ ] **Step 2: Write the failing test**

Create `services/server/tests/test_metrics_endpoint.py`:

```python
"""GET /metrics: declared metric names and METRICS_TOKEN enforcement."""
import pytest
from fastapi.testclient import TestClient

from src import config
from src.api.app import api


@pytest.fixture
def client(monkeypatch):
    settings = config.get_settings()
    monkeypatch.setattr(settings, "metrics_token", "", raising=False)
    return TestClient(api)


DECLARED = [
    "contextforge_jobs",
    "contextforge_index_requests_pending",
    "contextforge_index_requests_oldest_age_seconds",
    "contextforge_kb_documents_pending",
    "contextforge_web_pages_pending",
    "contextforge_scheduler_tick_timestamp_seconds",
    "contextforge_schema_version",
    "contextforge_mcp_tool_calls_total",
]


def test_metrics_exposes_every_declared_metric(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.text
    for name in DECLARED:
        assert name in body, name


def test_jobs_gauge_is_preinitialised_for_every_status(client):
    body = client.get("/metrics").text
    for status in ("pending", "running", "done", "error", "dead"):
        assert f'contextforge_jobs{{status="{status}"}}' in body


def test_metrics_content_type_is_the_prometheus_text_format(client):
    resp = client.get("/metrics")
    assert "text/plain" in resp.headers["content-type"]


def test_metrics_never_labels_by_organization(client):
    body = client.get("/metrics").text
    assert "org_id" not in body
    assert "organization" not in body


def test_open_when_no_token_is_configured(client):
    assert client.get("/metrics").status_code == 200


def test_token_is_required_when_configured(monkeypatch):
    settings = config.get_settings()
    monkeypatch.setattr(settings, "metrics_token", "s3cret", raising=False)
    client = TestClient(api)
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = client.get("/metrics", headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200
    assert "contextforge_jobs" in ok.text


def test_metrics_is_not_behind_the_session_guard(monkeypatch):
    """The guard only covers /api/*; /metrics must answer without a session token."""
    settings = config.get_settings()
    monkeypatch.setattr(settings, "metrics_token", "", raising=False)
    assert TestClient(api).get("/metrics").status_code == 200
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_metrics_endpoint.py`
Expected: FAIL — every request returns 404 because `/metrics` does not exist.

- [ ] **Step 4: Create `src/metrics.py`**

Create `services/server/src/metrics.py`:

```python
"""Prometheus metrics for context-forge. One registry, no per-organization labels."""
from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, generate_latest

from .mcp.jobs import JOB_STATUSES

registry = CollectorRegistry()

jobs_by_status = Gauge(
    "contextforge_jobs", "Async jobs by status", ["status"], registry=registry
)
index_requests_pending = Gauge(
    "contextforge_index_requests_pending", "Index requests not yet processed",
    registry=registry,
)
index_requests_oldest_age_seconds = Gauge(
    "contextforge_index_requests_oldest_age_seconds",
    "Age of the oldest unprocessed index request", registry=registry,
)
kb_documents_pending = Gauge(
    "contextforge_kb_documents_pending", "Knowledge-base documents awaiting processing",
    registry=registry,
)
web_pages_pending = Gauge(
    "contextforge_web_pages_pending", "Web pages awaiting processing", registry=registry
)
scheduler_tick_timestamp_seconds = Gauge(
    "contextforge_scheduler_tick_timestamp_seconds",
    "Unix time of the last scheduler metrics tick", registry=registry,
)
schema_version = Gauge(
    "contextforge_schema_version", "Applied schema migration version", registry=registry
)
# Declared here so the whole platform shares one registry; incremented by the
# MCP audit feature.
mcp_tool_calls_total = Counter(
    "contextforge_mcp_tool_calls_total", "MCP tool calls", ["tool", "outcome"],
    registry=registry,
)

for _status in JOB_STATUSES:
    jobs_by_status.labels(status=_status).set(0)


def render_metrics() -> bytes:
    """Render the registry in the Prometheus text exposition format."""
    return generate_latest(registry)
```

- [ ] **Step 5: Add the setting**

In `services/server/src/config.py`, inside `class Settings(BaseSettings)`, add next to `log_level`:

```python
    # Bearer token required by GET /metrics. Empty leaves the endpoint open
    # (intended for private scraping networks).
    metrics_token: str = ""
```

- [ ] **Step 6: Add the endpoint**

In `services/server/src/api/app.py`, add `Request` and `PlainTextResponse` to the existing imports:

```python
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
```

and add this route immediately after the `@api.get("/api/health")` handler:

```python
@api.get("/metrics")
async def metrics(request: Request):
    """Prometheus exposition. Guarded by METRICS_TOKEN when it is set."""
    from prometheus_client import CONTENT_TYPE_LATEST

    from ..metrics import render_metrics

    token = get_settings().metrics_token
    if token and request.headers.get("Authorization") != f"Bearer {token}":
        return PlainTextResponse("Unauthorized", status_code=401)
    return PlainTextResponse(render_metrics(), media_type=CONTENT_TYPE_LATEST)
```

`get_settings` is already imported at the top of `app.py`.

- [ ] **Step 7: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_metrics_endpoint.py`
Expected: PASS, 7 tests.

- [ ] **Step 8: Commit**

```bash
git add services/server/pyproject.toml services/server/src/metrics.py services/server/src/config.py services/server/src/api/app.py services/server/tests/test_metrics_endpoint.py
git commit -m "add prometheus registry and metrics endpoint"
```

---

### Task 5: Queue snapshot, scheduler heartbeat and the 30-second refresh tick

**Files:**
- Modify: `services/server/src/metrics.py` (add `collect_queue_stats`, `refresh_metrics`, `record_scheduler_tick`, `last_scheduler_tick`)
- Modify: `services/server/src/scheduler.py` (add `_refresh_metrics` job, `is_scheduler_running`, immediate first tick)
- Test: `services/server/tests/test_metrics_refresh.py`

**Interfaces:**
- Consumes, from Task 4, in `src/metrics.py`: `registry`, `jobs_by_status` (label `status`), `index_requests_pending`, `index_requests_oldest_age_seconds`, `kb_documents_pending`, `web_pages_pending`, `scheduler_tick_timestamp_seconds`, `schema_version`, `mcp_tool_calls_total`, `render_metrics()`.
- Consumes, from feature 1: `async def current_version(pool) -> int` in `src/migrations/runner.py`, returning 0 when `schema_migrations` is absent.
- Consumes, from Task 1: `JOB_STATUSES = ("pending", "running", "done", "error", "dead")`.
- Produces, in `src/metrics.py`:
  - `async def collect_queue_stats() -> dict[str, float]` with keys `jobs_pending`, `jobs_running`, `jobs_done`, `jobs_error`, `jobs_dead`, `index_requests_pending`, `index_requests_oldest_age_seconds`, `kb_documents_pending`, `web_pages_pending`.
  - `async def refresh_metrics() -> None`
  - `def record_scheduler_tick(now: float | None = None) -> None`
  - `def last_scheduler_tick() -> float | None`
- Produces, in `src/scheduler.py`: `async def _refresh_metrics() -> None` registered as APScheduler job id `"metrics"` on a 30-second interval, and `def is_scheduler_running() -> bool`.

**Background you need (do not go looking for it):**

The scheduler and the API run in the same process (`src/main.py` starts both uvicorn servers plus the scheduler with `asyncio.gather`), so a module-level variable in `src/metrics.py` is a valid place for the last-tick timestamp and is readable by the health route.

The queue sources are:

- `jobs.status`, values `pending | running | done | error | dead`;
- `index_requests`, pending means `processed_at IS NULL`, queued at `requested_at`;
- `kb_documents.status = 'pending'`;
- `web_pages.status = 'pending'`.

`src/scheduler.py` after Task 2 already has module-level `from .db import get_pool` and a `_scheduler: AsyncIOScheduler | None` module global set inside `start_scheduler()`. `AsyncIOScheduler` exposes a `.running` property.

APScheduler `interval` jobs first fire one interval after start, so `start_scheduler` must record one tick immediately; otherwise `/api/health/details` reports `degraded` for the first 30 seconds after boot.

`start_scheduler` currently ends with:

```python
    _scheduler.start()
    await sync_scheduler_jobs()
    logger.info("Scheduler started (per-organization refresh jobs)")
```

- [ ] **Step 1: Write the failing test**

Create `services/server/tests/test_metrics_refresh.py`:

```python
"""Queue snapshot, gauge refresh, and the scheduler heartbeat."""
import asyncio
import inspect
import time

import pytest

from src import metrics, scheduler


class FakeConn:
    def __init__(self, row):
        self.row = row
        self.queries = []

    async def fetchrow(self, sql, *args):
        self.queries.append(sql)
        return self.row


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


ROW = {
    "jobs_pending": 4,
    "jobs_running": 1,
    "jobs_done": 90,
    "jobs_error": 2,
    "jobs_dead": 3,
    "index_requests_pending": 7,
    "index_requests_oldest_age_seconds": 4200,
    "kb_documents_pending": 5,
    "web_pages_pending": 6,
}


@pytest.fixture
def wired(monkeypatch):
    conn = FakeConn(ROW)

    async def fake_pool():
        return FakePool(conn)

    async def fake_version(pool):
        return 2

    monkeypatch.setattr(metrics, "get_pool", fake_pool)
    monkeypatch.setattr(metrics, "current_version", fake_version)
    return conn


def test_queue_stats_come_from_a_single_round_trip(wired):
    stats = asyncio.run(metrics.collect_queue_stats())
    assert len(wired.queries) == 1
    assert stats["jobs_pending"] == 4
    assert stats["jobs_dead"] == 3
    assert stats["index_requests_oldest_age_seconds"] == 4200
    assert stats["kb_documents_pending"] == 5
    assert stats["web_pages_pending"] == 6


def test_queue_stats_query_reads_every_source(wired):
    asyncio.run(metrics.collect_queue_stats())
    sql = wired.queries[0].lower()
    assert "from jobs" in sql
    assert "from index_requests" in sql
    assert "from kb_documents" in sql
    assert "from web_pages" in sql


def test_queue_stats_tolerate_nulls(monkeypatch):
    row = dict(ROW, index_requests_oldest_age_seconds=None)
    conn = FakeConn(row)

    async def fake_pool():
        return FakePool(conn)

    monkeypatch.setattr(metrics, "get_pool", fake_pool)
    stats = asyncio.run(metrics.collect_queue_stats())
    assert stats["index_requests_oldest_age_seconds"] == 0.0


def _sample(name, labels=None):
    for metric in metrics.registry.collect():
        for s in metric.samples:
            if s.name == name and (labels is None or s.labels == labels):
                return s.value
    return None


def test_refresh_sets_every_gauge(wired):
    asyncio.run(metrics.refresh_metrics())
    assert _sample("contextforge_jobs", {"status": "pending"}) == 4
    assert _sample("contextforge_jobs", {"status": "dead"}) == 3
    assert _sample("contextforge_index_requests_pending") == 7
    assert _sample("contextforge_index_requests_oldest_age_seconds") == 4200
    assert _sample("contextforge_kb_documents_pending") == 5
    assert _sample("contextforge_web_pages_pending") == 6
    assert _sample("contextforge_schema_version") == 2


def test_refresh_records_the_heartbeat(wired):
    before = time.time()
    asyncio.run(metrics.refresh_metrics())
    assert metrics.last_scheduler_tick() >= before
    assert _sample("contextforge_scheduler_tick_timestamp_seconds") >= before


def test_record_scheduler_tick_accepts_an_explicit_time():
    metrics.record_scheduler_tick(1_000_000.0)
    assert metrics.last_scheduler_tick() == 1_000_000.0
    assert _sample("contextforge_scheduler_tick_timestamp_seconds") == 1_000_000.0


def test_scheduler_refresh_job_delegates_to_metrics(monkeypatch):
    called = []

    async def fake_refresh():
        called.append(True)

    monkeypatch.setattr(scheduler, "refresh_metrics", fake_refresh)
    asyncio.run(scheduler._refresh_metrics())
    assert called == [True]


def test_scheduler_registers_the_metrics_tick_and_an_immediate_first_tick():
    source = inspect.getsource(scheduler.start_scheduler)
    assert "_refresh_metrics" in source
    assert 'id="metrics"' in source
    assert "seconds=30" in source
    assert "record_scheduler_tick()" in source


def test_is_scheduler_running_reflects_the_global(monkeypatch):
    monkeypatch.setattr(scheduler, "_scheduler", None)
    assert scheduler.is_scheduler_running() is False

    class _Live:
        running = True

    monkeypatch.setattr(scheduler, "_scheduler", _Live())
    assert scheduler.is_scheduler_running() is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_metrics_refresh.py`
Expected: FAIL with `AttributeError: module 'src.metrics' has no attribute 'collect_queue_stats'`.

- [ ] **Step 3: Extend `src/metrics.py`**

Add these imports at the top of `services/server/src/metrics.py`:

```python
import time
from typing import Optional

from .db import get_pool
from .migrations.runner import current_version
```

and append below `render_metrics`:

```python
_QUEUE_STATS_SQL = """
SELECT
    (SELECT COUNT(*) FROM jobs WHERE status = 'pending')  AS jobs_pending,
    (SELECT COUNT(*) FROM jobs WHERE status = 'running')  AS jobs_running,
    (SELECT COUNT(*) FROM jobs WHERE status = 'done')     AS jobs_done,
    (SELECT COUNT(*) FROM jobs WHERE status = 'error')    AS jobs_error,
    (SELECT COUNT(*) FROM jobs WHERE status = 'dead')     AS jobs_dead,
    (SELECT COUNT(*) FROM index_requests WHERE processed_at IS NULL)
        AS index_requests_pending,
    (SELECT EXTRACT(EPOCH FROM (NOW() - MIN(requested_at)))
       FROM index_requests WHERE processed_at IS NULL)
        AS index_requests_oldest_age_seconds,
    (SELECT COUNT(*) FROM kb_documents WHERE status = 'pending') AS kb_documents_pending,
    (SELECT COUNT(*) FROM web_pages WHERE status = 'pending')    AS web_pages_pending
"""

_last_tick: Optional[float] = None


async def collect_queue_stats() -> dict[str, float]:
    """One round-trip snapshot of every processing queue. No per-org breakdown."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(_QUEUE_STATS_SQL)
    return {key: float(value or 0) for key, value in dict(row).items()}


def record_scheduler_tick(now: Optional[float] = None) -> None:
    """Record a scheduler heartbeat, for the gauge and for /api/health/details."""
    global _last_tick
    _last_tick = time.time() if now is None else now
    scheduler_tick_timestamp_seconds.set(_last_tick)


def last_scheduler_tick() -> Optional[float]:
    return _last_tick


async def refresh_metrics() -> None:
    """Repopulate every gauge from the database and beat the heartbeat."""
    stats = await collect_queue_stats()
    for status in JOB_STATUSES:
        jobs_by_status.labels(status=status).set(stats.get(f"jobs_{status}", 0.0))
    index_requests_pending.set(stats["index_requests_pending"])
    index_requests_oldest_age_seconds.set(stats["index_requests_oldest_age_seconds"])
    kb_documents_pending.set(stats["kb_documents_pending"])
    web_pages_pending.set(stats["web_pages_pending"])

    pool = await get_pool()
    schema_version.set(await current_version(pool))
    record_scheduler_tick()
```

- [ ] **Step 4: Wire the scheduler**

In `services/server/src/scheduler.py`, add to the module-level imports:

```python
from .metrics import record_scheduler_tick, refresh_metrics
```

Add next to the other `_check_*` functions:

```python
async def _refresh_metrics() -> None:
    """Repopulate the Prometheus gauges and beat the scheduler heartbeat."""
    await refresh_metrics()


def is_scheduler_running() -> bool:
    return _scheduler is not None and bool(getattr(_scheduler, "running", False))
```

Register the job in `start_scheduler`, right after the `jobs` job, and record the first tick at start-up:

```python
    # Refresh the Prometheus gauges and the heartbeat.
    _scheduler.add_job(
        _refresh_metrics, "interval", seconds=30, id="metrics", replace_existing=True
    )
```

and change the tail of `start_scheduler` to:

```python
    _scheduler.start()
    record_scheduler_tick()
    await sync_scheduler_jobs()
    logger.info("Scheduler started (per-organization refresh jobs)")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_metrics_refresh.py tests/test_metrics_endpoint.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/server/src/metrics.py services/server/src/scheduler.py services/server/tests/test_metrics_refresh.py
git commit -m "refresh queue gauges on a scheduler tick"
```

---

### Task 6: `GET /api/health/details` for org admins and owners

**Files:**
- Create: `services/server/src/api/routes/health.py`
- Modify: `services/server/src/api/app.py` (import and include the router; make the `/api/health` auth exclusion an exact match)
- Test: `services/server/tests/test_health_details.py`

**Interfaces:**
- Consumes, from Task 5, in `src/metrics.py`: `async def collect_queue_stats() -> dict[str, float]` with keys `jobs_pending`, `jobs_running`, `jobs_done`, `jobs_error`, `jobs_dead`, `index_requests_pending`, `index_requests_oldest_age_seconds`, `kb_documents_pending`, `web_pages_pending`; and `def last_scheduler_tick() -> float | None`.
- Consumes, from Task 5, in `src/scheduler.py`: `def is_scheduler_running() -> bool`.
- Consumes, from feature 1: `async def current_version(pool) -> int` in `src/migrations/runner.py`.
- Produces: `GET /api/health/details` returning exactly

```json
{
  "status": "ok" | "degraded",
  "database": {"ok": true, "latency_ms": 3},
  "schema_version": 2,
  "scheduler": {"running": true, "last_tick_at": "2026-09-07T10:00:00+00:00"},
  "queues": {
    "jobs_pending": 0, "jobs_running": 0, "jobs_dead": 0,
    "index_requests_pending": 0, "index_requests_oldest_age_seconds": 0,
    "kb_documents_pending": 0, "web_pages_pending": 0
  }
}
```

**Background you need (do not go looking for it):**

`GET /api/health` stays exactly as it is: public and minimal, defined inline in `src/api/app.py` as

```python
@api.get("/api/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "context-forge-api"}
```

**Critical:** the `auth_guard` middleware in `src/api/app.py` currently opens paths with a prefix match:

```python
    open_paths = (
        "/api/health",
        "/api/setup",
        ...
    )
    if path.startswith(open_paths):
        return await call_next(request)
```

`"/api/health"` as a prefix would leave `/api/health/details` public. Move it out of the tuple into an exact-match check.

Role gating uses the dependency factory in `src/api/deps.py`:

```python
def require_role(minimum: str):
    """Dependency factory enforcing a minimum role in the active organization."""

    async def _checker(org: ActiveOrg = Depends(get_active_org)) -> ActiveOrg:
        if not tenancy.role_at_least(org.role, minimum):
            raise HTTPException(status_code=403, detail=...)
        return org

    return _checker
```

`require_role("admin")` therefore admits `admin` and `owner` (ranks are `viewer < member < admin < owner`). `require_role` depends on `get_active_org`, so a test overrides `deps.get_active_org` — exactly as `tests/test_project_members.py` does:

```python
    app = FastAPI()
    app.include_router(project_routes.router)
    app.dependency_overrides[deps.get_active_org] = lambda: ORG
```

`ActiveOrg` is `@dataclass class ActiveOrg: org_id: int; role: str; namespace: str; name: str`.

Degraded rules, from the spec: the database check fails, **or** the scheduler has not ticked for more than 60 s, **or** `index_requests_oldest_age_seconds > 3600`.

Importing `src.scheduler` from a route module creates no cycle — this was verified against the current import graph.

- [ ] **Step 1: Write the failing test**

Create `services/server/tests/test_health_details.py`:

```python
"""GET /api/health/details: shape, degraded rules, and that it is not public."""
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import deps
from src.api.routes import health as health_routes

ADMIN = deps.ActiveOrg(org_id=1, role="admin", namespace="acme", name="Acme")

STATS = {
    "jobs_pending": 2.0,
    "jobs_running": 1.0,
    "jobs_done": 10.0,
    "jobs_error": 0.0,
    "jobs_dead": 3.0,
    "index_requests_pending": 4.0,
    "index_requests_oldest_age_seconds": 12.0,
    "kb_documents_pending": 5.0,
    "web_pages_pending": 6.0,
}


class FakeConn:
    async def fetchval(self, sql, *args):
        return 1


class FakeAcquire:
    async def __aenter__(self):
        return FakeConn()

    async def __aexit__(self, *exc):
        return False


class FakePool:
    def acquire(self):
        return FakeAcquire()


def _wire(monkeypatch, *, stats=None, tick=None, running=True, db_ok=True):
    async def fake_pool():
        if not db_ok:
            raise RuntimeError("connection refused")
        return FakePool()

    async def fake_stats():
        return dict(stats if stats is not None else STATS)

    async def fake_version(pool):
        return 2

    monkeypatch.setattr(health_routes, "get_pool", fake_pool)
    monkeypatch.setattr(health_routes, "collect_queue_stats", fake_stats)
    monkeypatch.setattr(health_routes, "current_version", fake_version)
    monkeypatch.setattr(
        health_routes, "last_scheduler_tick",
        lambda: time.time() if tick is None else tick,
    )
    monkeypatch.setattr(health_routes, "is_scheduler_running", lambda: running)

    app = FastAPI()
    app.include_router(health_routes.router, prefix="/api")
    app.dependency_overrides[deps.get_active_org] = lambda: ADMIN
    return TestClient(app)


def test_healthy_shape(monkeypatch):
    resp = _wire(monkeypatch).get("/api/health/details")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"]["ok"] is True
    assert isinstance(body["database"]["latency_ms"], int)
    assert body["schema_version"] == 2
    assert body["scheduler"]["running"] is True
    assert body["scheduler"]["last_tick_at"].endswith("+00:00")
    assert body["queues"] == {
        "jobs_pending": 2,
        "jobs_running": 1,
        "jobs_dead": 3,
        "index_requests_pending": 4,
        "index_requests_oldest_age_seconds": 12,
        "kb_documents_pending": 5,
        "web_pages_pending": 6,
    }


def test_database_failure_is_degraded(monkeypatch):
    body = _wire(monkeypatch, db_ok=False).get("/api/health/details").json()
    assert body["status"] == "degraded"
    assert body["database"] == {"ok": False, "latency_ms": 0}


def test_stale_scheduler_tick_is_degraded(monkeypatch):
    body = _wire(monkeypatch, tick=time.time() - 61).get("/api/health/details").json()
    assert body["status"] == "degraded"


def test_recent_scheduler_tick_is_healthy(monkeypatch):
    body = _wire(monkeypatch, tick=time.time() - 59).get("/api/health/details").json()
    assert body["status"] == "ok"


def test_never_ticked_is_degraded(monkeypatch):
    body = _wire(monkeypatch, tick=0).get("/api/health/details").json()
    assert body["status"] == "degraded"


def test_old_index_queue_is_degraded(monkeypatch):
    stats = dict(STATS, index_requests_oldest_age_seconds=3601.0)
    body = _wire(monkeypatch, stats=stats).get("/api/health/details").json()
    assert body["status"] == "degraded"
    assert body["queues"]["index_requests_oldest_age_seconds"] == 3601


def test_index_queue_at_the_threshold_is_healthy(monkeypatch):
    stats = dict(STATS, index_requests_oldest_age_seconds=3600.0)
    body = _wire(monkeypatch, stats=stats).get("/api/health/details").json()
    assert body["status"] == "ok"


def test_members_are_rejected(monkeypatch):
    client = _wire(monkeypatch)
    client.app.dependency_overrides[deps.get_active_org] = lambda: deps.ActiveOrg(
        org_id=1, role="member", namespace="acme", name="Acme"
    )
    assert client.get("/api/health/details").status_code == 403


def test_owners_are_admitted(monkeypatch):
    client = _wire(monkeypatch)
    client.app.dependency_overrides[deps.get_active_org] = lambda: deps.ActiveOrg(
        org_id=1, role="owner", namespace="acme", name="Acme"
    )
    assert client.get("/api/health/details").status_code == 200


# ── the auth guard must not treat /api/health as a prefix ─────────────────────

def test_details_is_behind_the_session_guard(monkeypatch):
    from src.api import security as api_security
    from src.api.app import api

    async def fake_is_configured():
        return True

    monkeypatch.setattr(api_security, "is_configured", fake_is_configured)
    resp = TestClient(api).get("/api/health/details")
    assert resp.status_code == 401


def test_public_health_stays_open():
    from src.api.app import api

    resp = TestClient(api).get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "context-forge-api"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_health_details.py`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'src.api.routes.health'`.

- [ ] **Step 3: Create the route module**

Create `services/server/src/api/routes/health.py`:

```python
"""Detailed health for organization admins and owners."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from ...db import get_pool
from ...metrics import collect_queue_stats, last_scheduler_tick
from ...migrations.runner import current_version
from ...scheduler import is_scheduler_running
from ..deps import ActiveOrg, require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["health"])

STALE_TICK_SECONDS = 60
MAX_INDEX_QUEUE_AGE_SECONDS = 3600


@router.get("/details")
async def health_details(org: ActiveOrg = Depends(require_role("admin"))) -> dict:
    """Database, schema version, scheduler heartbeat and queue depths."""
    database = {"ok": True, "latency_ms": 0}
    stats: dict[str, float] = {}
    schema = 0
    try:
        pool = await get_pool()
        started = time.perf_counter()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        database["latency_ms"] = int(round((time.perf_counter() - started) * 1000))
        stats = await collect_queue_stats()
        schema = await current_version(pool)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Health details database check failed: %s", exc)
        database = {"ok": False, "latency_ms": 0}

    tick = last_scheduler_tick()
    tick_age = None if not tick else time.time() - tick
    scheduler = {
        "running": is_scheduler_running(),
        "last_tick_at": None
        if not tick
        else datetime.fromtimestamp(tick, tz=timezone.utc).isoformat(),
    }

    queues = {
        "jobs_pending": int(stats.get("jobs_pending", 0)),
        "jobs_running": int(stats.get("jobs_running", 0)),
        "jobs_dead": int(stats.get("jobs_dead", 0)),
        "index_requests_pending": int(stats.get("index_requests_pending", 0)),
        "index_requests_oldest_age_seconds": int(
            stats.get("index_requests_oldest_age_seconds", 0)
        ),
        "kb_documents_pending": int(stats.get("kb_documents_pending", 0)),
        "web_pages_pending": int(stats.get("web_pages_pending", 0)),
    }

    degraded = (
        not database["ok"]
        or tick_age is None
        or tick_age > STALE_TICK_SECONDS
        or queues["index_requests_oldest_age_seconds"] > MAX_INDEX_QUEUE_AGE_SECONDS
    )
    return {
        "status": "degraded" if degraded else "ok",
        "database": database,
        "schema_version": schema,
        "scheduler": scheduler,
        "queues": queues,
    }
```

- [ ] **Step 4: Include the router and fix the auth exclusion**

In `services/server/src/api/app.py`, add the import next to the other route imports:

```python
from .routes import health as health_routes
```

and the inclusion next to the others:

```python
api.include_router(health_routes.router, prefix="/api")
```

Then, inside `auth_guard`, replace

```python
    open_paths = (
        "/api/health",
        "/api/setup",
```

with

```python
    # Exact match: /api/health/details is authenticated.
    if path == "/api/health":
        return await call_next(request)

    open_paths = (
        "/api/setup",
```

leaving the rest of the tuple and the `if path.startswith(open_paths):` check unchanged.

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_health_details.py`
Expected: PASS, 11 tests.

- [ ] **Step 6: Commit**

```bash
git add services/server/src/api/routes/health.py services/server/src/api/app.py services/server/tests/test_health_details.py
git commit -m "add authenticated detailed health endpoint"
```

---

### Task 7: Documentation, configuration, and full-suite verification

**Files:**
- Modify: `README.md` (Features bullet on async jobs; Architecture section)
- Modify: `.env.example` (new Metrics section)
- Modify: `docker-compose.yml` (`METRICS_TOKEN` in the `context-forge` service environment)
- Test: the whole server suite

**Interfaces:**
- Consumes: `Settings.metrics_token` (env `METRICS_TOKEN`) from Task 4; the `GET /metrics` and `GET /api/health/details` endpoints from Tasks 4 and 6; the job retry behaviour from Tasks 1–3.
- Produces: no code. This task is documentation, configuration and the final verification gate.

**Background you need (do not go looking for it):**

The README line to change is in the `## Features` list:

```markdown
- **Async jobs** — offload slow downstream calls without hitting client timeouts.
```

The README `## Architecture` section ends with this paragraph, after which the new paragraph goes:

```markdown
Indexing uses tree-sitter (Python, JS/TS, Go, Java), with scheduled re-indexing via APScheduler. Re-indexing is **incremental**: ...
```

`.env.example` sections are delimited like this:

```
# ─── Logging ──────────────────────────────────────
LOG_LEVEL=INFO
```

`docker-compose.yml` passes settings to the `context-forge` service as a list of `- NAME=${NAME:-}` entries; `- LOG_LEVEL=${LOG_LEVEL:-INFO}` is the last one.

- [ ] **Step 1: Update the README features bullet**

Replace

```markdown
- **Async jobs** — offload slow downstream calls without hitting client timeouts.
```

with

```markdown
- **Async jobs** — offload slow downstream calls without hitting client timeouts. Jobs are executed by the scheduler, so they survive a restart; transient failures are retried with exponential backoff and exhausted jobs land in a `dead` state.
```

- [ ] **Step 2: Add the README operations paragraph**

In `## Architecture`, immediately after the "Indexing uses tree-sitter ..." paragraph, add:

```markdown
Operations are observable from two endpoints. `GET /metrics` serves Prometheus text exposition on the API port with the queue depths (jobs by status, pending index requests and the age of the oldest one, pending knowledge-base documents and web pages), the scheduler heartbeat, the applied schema version, and MCP tool-call counters; set `METRICS_TOKEN` to require `Authorization: Bearer <token>`, or leave it empty on a private scraping network. `GET /api/health/details` returns the same picture as JSON for organization admins and owners, plus the database latency, and reports `degraded` when the database check fails, the scheduler has not ticked for more than 60 seconds, or the oldest index request is more than an hour old. `GET /api/health` stays public and minimal. No metric is labelled by organization.
```

- [ ] **Step 3: Update `.env.example`**

Insert this block immediately before the `# ─── Logging ───` section:

```
# ─── Metrics ──────────────────────────────────────
# Bearer token required by GET /metrics (Prometheus text exposition).
# Leave empty to expose the endpoint without authentication — only do that
# when the port is reachable from a private scraping network.
METRICS_TOKEN=
```

- [ ] **Step 4: Update `docker-compose.yml`**

In the `context-forge` service `environment:` list, add immediately before `- LOG_LEVEL=${LOG_LEVEL:-INFO}`:

```yaml
      - METRICS_TOKEN=${METRICS_TOKEN:-}
```

- [ ] **Step 5: Sanity-check the compose file parses**

Run from the repository root:

```bash
docker compose config --quiet
```

Expected: no output, exit code 0. If the Docker CLI is unavailable in this environment, run instead from `services/server`:

```bash
.venv/Scripts/python.exe -c "import yaml,io; d=yaml.safe_load(open('../../docker-compose.yml',encoding='utf-8')); env=d['services']['context-forge']['environment']; assert any(e.startswith('METRICS_TOKEN=') for e in env), env; print('ok')"
```

Expected: `ok`.

- [ ] **Step 6: Run the full server suite**

Run from `services/server`:

```bash
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider
```

Expected: PASS, no failures and no errors. If anything fails, fix it before committing — do not skip, xfail, or delete a test to get green. Confirm in particular that `tests/test_jobs_project_scope.py`, `tests/test_oauth_bridge_reaper.py`, `tests/test_telegram_webhook.py` and `tests/test_oidc_login_flow.py` still pass, since they import `src.api.app` and `src.scheduler`, both of which this feature changed.

- [ ] **Step 7: Confirm no stale reference to the removed executor remains**

Run from the repository root:

```bash
grep -rn "_execute_http_job" services/server README.md || echo "clean"
```

Expected: `clean`.

- [ ] **Step 8: Commit**

```bash
git add README.md .env.example docker-compose.yml
git commit -m "document metrics health and job retries"
```

---

## Self-Review

**Spec coverage**

| Spec requirement | Task |
|---|---|
| Migration `0002_jobs_retry` adds `attempts`, `max_attempts`, `next_attempt_at`, `last_error` | 1 |
| Status values `pending / running / done / error / dead` | 1 (`JOB_STATUSES`), 3 (tools), 5 (gauge labels) |
| `_check_jobs` every 5 s, claim up to 5 with `FOR UPDATE SKIP LOCKED`, run concurrently | 2 |
| `job_submit` only inserts; response unchanged | 3 |
| Jobs stuck in `running` over 10 minutes reset to `pending` by the same check | 2 |
| 2xx → done; 4xx except 408/429 → error; 5xx/408/429/timeout/connection → retry | 1 (classification), 2 (application) |
| Backoff `5s * 4^(attempts-1)` capped at 5 min, else `dead` | 1 (formula), 2 (`finalize_job`) |
| `job_status` reports `attempts`, `max_attempts`, `next_attempt_at`, `dead` | 3 |
| `job_submit` accepts optional `max_attempts` (1–10) | 3 |
| Dependency `prometheus-client>=0.20` | 4 |
| `GET /metrics`, text exposition, optional `METRICS_TOKEN` bearer | 4 |
| Registry in `src/metrics.py`; labels never contain org ids | 4 (declaration + test), 5 (population) |
| `contextforge_jobs{status}` refreshed every 30 s | 5 |
| `contextforge_index_requests_pending`, `..._oldest_age_seconds` | 5 |
| `contextforge_kb_documents_pending`, `contextforge_web_pages_pending` | 5 |
| `contextforge_scheduler_tick_timestamp_seconds` heartbeat | 5 |
| `contextforge_schema_version` (from `current_version`) | 5 |
| `contextforge_mcp_tool_calls_total{tool,outcome}` declared now, incremented later | 4 |
| `GET /api/health` unchanged, public, minimal | 6 |
| `GET /api/health/details` for org admins/owners, exact JSON shape | 6 |
| `degraded` on db failure, tick older than 60 s, index age > 3600 | 6 |
| Tests: backoff 1..5 and cap | 1 |
| Tests: outcome classification for status codes and exceptions | 1 |
| Tests: `_check_jobs` claim/run/finalise with a fake pool, attempts increment, dead letter | 2 |
| Tests: stuck-running reset | 2 |
| Tests: `/metrics` metric names, token enforcement | 4 |
| Tests: `/api/health/details` shape and degraded rules with fake readings | 6 |
| `.env.example` + compose `METRICS_TOKEN=` | 7 |
| README: metrics and health paragraph under Architecture; jobs bullet mentions retries and dead letter | 7 |

No spec requirement is unassigned.

**Type and name consistency**

`JOB_STATUSES`, `next_backoff_seconds`, `classify_http_status`, `classify_exception`, `finalize_job`, `run_claimed_job`, `collect_queue_stats`, `refresh_metrics`, `record_scheduler_tick`, `last_scheduler_tick`, `is_scheduler_running`, `render_metrics`, `registry`, `jobs_by_status`, `index_requests_pending`, `index_requests_oldest_age_seconds`, `kb_documents_pending`, `web_pages_pending`, `scheduler_tick_timestamp_seconds`, `schema_version`, `mcp_tool_calls_total` are spelled identically in every task that defines or consumes them. `collect_queue_stats` returns `float` values everywhere and the health route is the only place that narrows them to `int`.
