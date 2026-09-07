# Versioned Schema Migrations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the "run one giant idempotent DDL string at every boot" scheme with a numbered, recorded, lock-protected migration runner, so every later feature ships its schema change as a new version module.

**Architecture:** A new package `services/server/src/migrations/` owns `runner.py` (advisory lock, `schema_migrations` bookkeeping, file-based discovery of `versions/NNNN_<name>.py` modules, ordered application) and `versions/0001_baseline.py` (executes the now-frozen `src.db.DDL` string). `src.db.init_db()` stops executing the DDL itself and calls `run_migrations(pool)`; the Python-side tenant/project migrations in `src.tenancy.ensure_tenant_storage()` keep running after it, unchanged. `src/cli.py` gains `migrate` and `migrate --status`.

**Tech Stack:** Python 3.11 (local dev 3.14), asyncpg (raw SQL), importlib file-based module loading, argparse, pytest (no pytest-asyncio — tests drive coroutines with `asyncio.run`).

**Spec:** `docs/superpowers/specs/2026-09-07-versioned-migrations-design.md`

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

- **This is feature 1 of the roadmap. Nothing else has landed yet.** Do not assume any file from another feature exists.
- **The spec calls the DDL string `SCHEMA_SQL`. In this codebase it is actually named `DDL`** (`services/server/src/db.py:16`) and it is a `str.format` template applied as `DDL.format(dims=settings.embeddings_dims)`. Keep the name `DDL` and keep the `.format(dims=...)` call — three existing tests import `from src.db import DDL` (`tests/test_db_schema.py`, `tests/test_projects_schema.py`, `tests/test_oauth_bridge_schema.py`) and one of them asserts the string still formats. Do **not** rename it and do **not** un-double the `{{}}` braces inside it.
- **No new Python dependency.** Everything used here is in the standard library or already installed.
- **No UI change.** No file under `services/ui` is touched by this plan.
- **No new MCP tool and no new MCP permission.**
- Tests never need a live database. Every command below runs from `services/server`. Per-file test command: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/<file>`.
- The test suite has **no `conftest.py`** and **no `pytest-asyncio`**. Async code is exercised with `asyncio.run(...)` and hand-written fake pool/connection classes, following `tests/test_role_permissions.py` and `tests/test_repo_registry.py`. Task 1 factors those fakes into `tests/fake_db.py`; later tasks import them (`tests/` is importable — `tests/test_project_migration.py` already does `from tests.test_projects_schema import CONTENT_TABLES`, and `pyproject.toml` sets `pythonpath = ["."]`).

### The public interface this feature defines (verbatim — later features depend on these exact names)

```python
# src/migrations/runner.py
MIGRATIONS_LOCK_KEY = 0x43464D47

async def run_migrations(pool) -> list[int]: ...
async def current_version(pool) -> int: ...
def discover_versions(versions_dir=None) -> list: ...
```

```python
# every src/migrations/versions/NNNN_<name>.py module
VERSION: int
NAME: str
TRANSACTIONAL: bool = True

async def upgrade(conn) -> None: ...
```

---

### Task 1: Migration package, module discovery, `current_version`

**Files:**
- Create: `services/server/src/migrations/__init__.py`
- Create: `services/server/src/migrations/versions/__init__.py`
- Create: `services/server/src/migrations/runner.py`
- Create: `services/server/tests/fake_db.py`
- Test: `services/server/tests/test_migrations_discovery.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `src/migrations/runner.py`: `MIGRATIONS_LOCK_KEY = 0x43464D47`, `VERSIONS_DIR` (a `pathlib.Path`), `SCHEMA_MIGRATIONS_DDL` (str), `def discover_versions(versions_dir=None) -> list` returning loaded modules sorted by `VERSION`, and `async def current_version(pool) -> int`.
  - `tests/fake_db.py`: `FakeConn` and `FakePool`, reused by Tasks 2–4.
  - Version module contract, honoured by every module under `versions/`:
    ```python
    VERSION: int
    NAME: str
    TRANSACTIONAL: bool = True

    async def upgrade(conn) -> None: ...
    ```

**Context you need (do not go looking for it):**
- `services/server` already has `.venv/`. Run everything from `services/server`.
- There is no `conftest.py` and no `pytest-asyncio`. Drive coroutines with `asyncio.run(...)`.
- `pyproject.toml` has `[tool.pytest.ini_options] pythonpath = ["."]`, so `import src.*` and `import tests.*` both work.

- [ ] **Step 1: Create the two package `__init__.py` files**

`services/server/src/migrations/__init__.py`:

```python
"""Versioned schema migrations: numbered modules applied once and recorded."""
```

`services/server/src/migrations/versions/__init__.py`:

```python
"""Migration modules, one per schema version, named NNNN_<name>.py."""
```

- [ ] **Step 2: Write the shared fake pool/connection helper**

Create `services/server/tests/fake_db.py`:

```python
"""Fake asyncpg pool/connection so tests never need a live database."""
from __future__ import annotations


class FakeConn:
    """Records every statement; answers fetchval from a scripted queue."""

    def __init__(self, fetchval_results=None, fetch_rows=None):
        self.executed: list[tuple[str, tuple]] = []
        self.fetchval_results = list(fetchval_results or [])
        self.fetch_rows = list(fetch_rows or [])
        self.transactions = 0
        self.open_transactions = 0

    @property
    def sql(self) -> list[str]:
        return [query for query, _ in self.executed]

    async def execute(self, query, *args):
        self.executed.append((query, args))
        return "OK"

    async def fetchval(self, query, *args):
        self.executed.append((query, args))
        if self.fetchval_results:
            return self.fetchval_results.pop(0)
        return None

    async def fetch(self, query, *args):
        self.executed.append((query, args))
        return self.fetch_rows

    def transaction(self):
        conn = self

        class _Tx:
            async def __aenter__(self_inner):
                conn.transactions += 1
                conn.open_transactions += 1
                return self_inner

            async def __aexit__(self_inner, *exc):
                conn.open_transactions -= 1
                return False

        return _Tx()


class FakePool:
    """Hands the same FakeConn to every acquire()."""

    def __init__(self, conn: FakeConn):
        self.conn = conn
        self.acquired = 0

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self_inner):
                pool.acquired += 1
                return pool.conn

            async def __aexit__(self_inner, *exc):
                return False

        return _Ctx()
```

- [ ] **Step 3: Write the failing test**

Create `services/server/tests/test_migrations_discovery.py`:

```python
"""Discovery orders modules by number; current_version reads the table."""
import asyncio

from src.migrations import runner
from tests.fake_db import FakeConn, FakePool

MODULE = '''
VERSION = {version}
NAME = "{name}"
TRANSACTIONAL = {transactional}


async def upgrade(conn) -> None:
    await conn.execute("-- migration {version}")
'''


def write_versions(directory, specs):
    for version, name, transactional in specs:
        path = directory / f"{version:04d}_{name}.py"
        path.write_text(
            MODULE.format(version=version, name=name, transactional=transactional),
            encoding="utf-8",
        )
    (directory / "__init__.py").write_text("", encoding="utf-8")
    (directory / "notes.txt").write_text("not a migration", encoding="utf-8")


def test_lock_key_is_the_agreed_constant():
    assert runner.MIGRATIONS_LOCK_KEY == 0x43464D47


def test_discovery_orders_by_version_and_skips_non_modules(tmp_path):
    write_versions(
        tmp_path,
        [(3, "c", True), (1, "a", True), (2, "b", False)],
    )
    modules = runner.discover_versions(tmp_path)
    assert [m.VERSION for m in modules] == [1, 2, 3]
    assert [m.NAME for m in modules] == ["a", "b", "c"]
    assert [m.TRANSACTIONAL for m in modules] == [True, False, True]


def test_discovery_defaults_to_the_packaged_versions_dir(tmp_path, monkeypatch):
    write_versions(tmp_path, [(1, "a", True)])
    monkeypatch.setattr(runner, "VERSIONS_DIR", tmp_path)
    assert [m.VERSION for m in runner.discover_versions()] == [1]


def test_current_version_is_zero_when_the_table_is_absent():
    conn = FakeConn(fetchval_results=[False])
    assert asyncio.run(runner.current_version(FakePool(conn))) == 0


def test_current_version_reads_the_max_applied():
    conn = FakeConn(fetchval_results=[True, 4])
    assert asyncio.run(runner.current_version(FakePool(conn))) == 4
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_migrations_discovery.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.migrations.runner'`.

- [ ] **Step 5: Write the runner skeleton**

Create `services/server/src/migrations/runner.py`:

```python
"""Discovers, orders and applies the numbered migration modules."""
from __future__ import annotations

import importlib.util
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

MIGRATIONS_LOCK_KEY = 0x43464D47

VERSIONS_DIR = Path(__file__).parent / "versions"

_FILENAME_RE = re.compile(r"^(\d{4})_([A-Za-z0-9_]+)\.py$")

SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INT PRIMARY KEY,
    name       TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""


def discover_versions(versions_dir=None) -> list:
    """Load every versions/NNNN_<name>.py module, sorted by VERSION."""
    directory = Path(versions_dir) if versions_dir is not None else VERSIONS_DIR
    modules = []
    for path in sorted(directory.glob("*.py")):
        if not _FILENAME_RE.match(path.name):
            continue
        spec = importlib.util.spec_from_file_location(
            f"src.migrations.versions.{path.stem}", path
        )
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    modules.sort(key=lambda module: module.VERSION)
    return modules


async def current_version(pool) -> int:
    """Highest recorded version, 0 when schema_migrations does not exist."""
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT to_regclass('public.schema_migrations') IS NOT NULL"
        )
        if not exists:
            return 0
        return await conn.fetchval(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        ) or 0
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_migrations_discovery.py`
Expected: PASS (5 passed).

- [ ] **Step 7: Commit**

```bash
git add services/server/src/migrations services/server/tests/fake_db.py services/server/tests/test_migrations_discovery.py
git commit -m "add migration module discovery and version query"
```

---

### Task 2: `run_migrations` — advisory lock, bookkeeping, ordered application

**Files:**
- Modify: `services/server/src/migrations/runner.py` (append `run_migrations` and its `_record` helper)
- Test: `services/server/tests/test_migrations_runner.py`

**Interfaces:**
- Consumes, already present in `src/migrations/runner.py` from Task 1 — do not redefine them:
  ```python
  MIGRATIONS_LOCK_KEY = 0x43464D47
  VERSIONS_DIR = Path(__file__).parent / "versions"
  SCHEMA_MIGRATIONS_DDL = """
  CREATE TABLE IF NOT EXISTS schema_migrations (
      version    INT PRIMARY KEY,
      name       TEXT NOT NULL,
      applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
  )
  """

  def discover_versions(versions_dir=None) -> list: ...
  async def current_version(pool) -> int: ...
  ```
  Also present: `services/server/tests/fake_db.py` exposing `FakeConn` (attributes `executed` — a list of `(sql, args)` — plus the `sql` property, `fetchval_results`, `transactions`, `open_transactions`) and `FakePool(conn)`.
- Produces: `async def run_migrations(pool) -> list[int]` — the single entry point every caller (boot and CLI) uses. It takes `pg_advisory_lock(MIGRATIONS_LOCK_KEY)` on one acquired connection, creates `schema_migrations` if missing, applies every discovered module whose `VERSION` is greater than the highest recorded version, records one row per module, releases the lock even on failure, and returns the list of versions applied in this run.

**Behaviour required by the spec:**
- Transactional modules (`TRANSACTIONAL` true or absent) run inside `async with conn.transaction()`, with the bookkeeping INSERT in the same transaction.
- Non-transactional modules run in autocommit (no `conn.transaction()`), then get their row recorded.
- A failing module propagates its exception; no later module runs; the lock is still released.

- [ ] **Step 1: Write the failing test**

Create `services/server/tests/test_migrations_runner.py`:

```python
"""run_migrations: lock, ordering, transaction dispatch, failure behaviour."""
import asyncio

import pytest

from src.migrations import runner
from tests.fake_db import FakeConn, FakePool

MODULE = '''
VERSION = {version}
NAME = "{name}"
TRANSACTIONAL = {transactional}


async def upgrade(conn) -> None:
    await conn.execute("-- migration {version}")
'''

FAILING = '''
VERSION = {version}
NAME = "{name}"
TRANSACTIONAL = True


async def upgrade(conn) -> None:
    raise RuntimeError("boom {version}")
'''


def build_versions(tmp_path, specs, failing=()):
    for version, name, transactional in specs:
        (tmp_path / f"{version:04d}_{name}.py").write_text(
            MODULE.format(version=version, name=name, transactional=transactional),
            encoding="utf-8",
        )
    for version, name in failing:
        (tmp_path / f"{version:04d}_{name}.py").write_text(
            FAILING.format(version=version, name=name), encoding="utf-8"
        )
    return tmp_path


def run(pool):
    return asyncio.run(runner.run_migrations(pool))


def test_applies_every_pending_module_in_order(tmp_path, monkeypatch):
    build_versions(tmp_path, [(1, "a", True), (2, "b", True), (3, "c", True)])
    monkeypatch.setattr(runner, "VERSIONS_DIR", tmp_path)
    conn = FakeConn(fetchval_results=[0])

    assert run(FakePool(conn)) == [1, 2, 3]
    assert "-- migration 1" in conn.sql
    assert "-- migration 3" in conn.sql
    assert any("CREATE TABLE IF NOT EXISTS schema_migrations" in q for q in conn.sql)
    recorded = [args for query, args in conn.executed if "INSERT INTO schema_migrations" in query]
    assert recorded == [(1, "a"), (2, "b"), (3, "c")]


def test_skips_modules_at_or_below_the_recorded_version(tmp_path, monkeypatch):
    build_versions(tmp_path, [(1, "a", True), (2, "b", True), (3, "c", True)])
    monkeypatch.setattr(runner, "VERSIONS_DIR", tmp_path)
    conn = FakeConn(fetchval_results=[2])

    assert run(FakePool(conn)) == [3]
    assert "-- migration 1" not in conn.sql
    assert "-- migration 2" not in conn.sql
    assert "-- migration 3" in conn.sql


def test_nothing_to_do_returns_an_empty_list(tmp_path, monkeypatch):
    build_versions(tmp_path, [(1, "a", True)])
    monkeypatch.setattr(runner, "VERSIONS_DIR", tmp_path)
    conn = FakeConn(fetchval_results=[1])

    assert run(FakePool(conn)) == []


def test_transactional_module_runs_inside_a_transaction(tmp_path, monkeypatch):
    build_versions(tmp_path, [(1, "a", True)])
    monkeypatch.setattr(runner, "VERSIONS_DIR", tmp_path)
    conn = FakeConn(fetchval_results=[0])

    run(FakePool(conn))
    assert conn.transactions == 1
    assert conn.open_transactions == 0


def test_non_transactional_module_runs_in_autocommit(tmp_path, monkeypatch):
    build_versions(tmp_path, [(1, "a", False)])
    monkeypatch.setattr(runner, "VERSIONS_DIR", tmp_path)
    conn = FakeConn(fetchval_results=[0])

    assert run(FakePool(conn)) == [1]
    assert conn.transactions == 0


def test_advisory_lock_is_taken_first_and_released_last(tmp_path, monkeypatch):
    build_versions(tmp_path, [(1, "a", True)])
    monkeypatch.setattr(runner, "VERSIONS_DIR", tmp_path)
    conn = FakeConn(fetchval_results=[0])

    run(FakePool(conn))
    assert "pg_advisory_lock" in conn.executed[0][0]
    assert conn.executed[0][1] == (runner.MIGRATIONS_LOCK_KEY,)
    assert "pg_advisory_unlock" in conn.executed[-1][0]
    assert conn.executed[-1][1] == (runner.MIGRATIONS_LOCK_KEY,)


def test_first_failure_stops_the_run_and_still_releases_the_lock(tmp_path, monkeypatch):
    build_versions(tmp_path, [(1, "a", True), (3, "c", True)], failing=[(2, "b")])
    monkeypatch.setattr(runner, "VERSIONS_DIR", tmp_path)
    conn = FakeConn(fetchval_results=[0])

    with pytest.raises(RuntimeError, match="boom 2"):
        run(FakePool(conn))

    assert "-- migration 1" in conn.sql
    assert "-- migration 3" not in conn.sql
    recorded = [args for query, args in conn.executed if "INSERT INTO schema_migrations" in query]
    assert recorded == [(1, "a")]
    assert "pg_advisory_unlock" in conn.executed[-1][0]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_migrations_runner.py`
Expected: FAIL — `AttributeError: module 'src.migrations.runner' has no attribute 'run_migrations'`.

- [ ] **Step 3: Append the implementation to `src/migrations/runner.py`**

Add at the end of the file (keep everything already there):

```python
async def _record(conn, module) -> None:
    await conn.execute(
        "INSERT INTO schema_migrations (version, name) VALUES ($1, $2)"
        " ON CONFLICT (version) DO NOTHING",
        module.VERSION,
        module.NAME,
    )


async def run_migrations(pool) -> list[int]:
    """Apply every pending version module under the advisory lock."""
    applied: list[int] = []
    async with pool.acquire() as conn:
        await conn.execute("SELECT pg_advisory_lock($1::bigint)", MIGRATIONS_LOCK_KEY)
        try:
            await conn.execute(SCHEMA_MIGRATIONS_DDL)
            version = await conn.fetchval(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            ) or 0
            for module in discover_versions():
                if module.VERSION <= version:
                    continue
                if getattr(module, "TRANSACTIONAL", True):
                    async with conn.transaction():
                        await module.upgrade(conn)
                        await _record(conn, module)
                else:
                    await module.upgrade(conn)
                    await _record(conn, module)
                applied.append(module.VERSION)
                logger.info("Applied migration %04d_%s", module.VERSION, module.NAME)
        finally:
            await conn.execute(
                "SELECT pg_advisory_unlock($1::bigint)", MIGRATIONS_LOCK_KEY
            )
    return applied
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_migrations_runner.py`
Expected: PASS (7 passed).

- [ ] **Step 5: Run the discovery test too, to confirm nothing regressed**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_migrations_discovery.py tests/test_migrations_runner.py`
Expected: PASS (12 passed).

- [ ] **Step 6: Commit**

```bash
git add services/server/src/migrations/runner.py services/server/tests/test_migrations_runner.py
git commit -m "apply pending migrations under an advisory lock"
```

---

### Task 3: `0001_baseline` module and boot integration in `init_db`

**Files:**
- Create: `services/server/src/migrations/versions/0001_baseline.py`
- Modify: `services/server/src/db.py` (add the runner import, add a freeze comment above `DDL = """` at line 16, rewrite `init_db` at lines 929–934)
- Test: `services/server/tests/test_migrations_baseline.py`

**Interfaces:**
- Consumes, already present in `src/migrations/runner.py` — do not redefine:
  ```python
  async def run_migrations(pool) -> list[int]: ...
  def discover_versions(versions_dir=None) -> list: ...
  ```
  And `services/server/tests/fake_db.py` exposing `FakeConn` (attributes `executed` — a list of `(sql, args)` — plus the `sql` property) and `FakePool(conn)`.
- Produces: `src/migrations/versions/0001_baseline.py` with the version module contract, verbatim:
  ```python
  VERSION = 1
  NAME = "baseline"
  TRANSACTIONAL = True

  async def upgrade(conn) -> None: ...
  ```
  And `src.db.init_db()` whose only schema work is `await run_migrations(pool)`.

**Context you need (do not go looking for it):**
- `services/server/src/db.py:16` defines `DDL = """ ... """`, ~700 lines of `CREATE TABLE IF NOT EXISTS` / `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` / `DROP TABLE IF EXISTS` / `DO $$ ... $$` blocks. Literal braces inside it are doubled (`'{{}}'::jsonb`) because it is passed through `str.format`.
- `init_db` today is exactly:
  ```python
  async def init_db() -> None:
      settings = get_settings()
      pool = await get_pool()
      async with pool.acquire() as conn:
          await conn.execute(DDL.format(dims=settings.embeddings_dims))
      logger.info("Database initialized")
  ```
- `db.py` already imports `get_settings` at the top (`from .config import get_settings`) and still needs it for `get_pool`. Leave that import alone.
- **Do not rename `DDL`, do not edit its contents, do not un-double its braces.** `tests/test_db_schema.py::test_ddl_still_formats` calls `DDL.format(dims=1536)` and must keep passing.
- `src.migrations.runner` does not import `src.db`, so a top-level `from .migrations.runner import run_migrations` in `db.py` creates no import cycle. The baseline module imports `src.db` lazily — it is only exec'd by `discover_versions()` at run time, long after `src.db` is loaded.
- Absolute `from src.db import DDL` inside the version module is correct in every context: Docker runs `python -m src.main` from `/app`, and pytest sets `pythonpath = ["."]` from `services/server`.
- `src/main.py` calls `await init_db()` and then `await ensure_runtime_state()` and `await ensure_tenant_storage()`. **Do not touch `src/main.py` and do not touch `src/tenancy.py`** — the Python-side tenant/project migrations keep running exactly where they run today.

- [ ] **Step 1: Write the failing test**

Create `services/server/tests/test_migrations_baseline.py`:

```python
"""0001_baseline replays the frozen DDL; init_db delegates to the runner."""
import asyncio
import inspect

from src import db
from src.migrations import runner
from tests.fake_db import FakeConn, FakePool


def baseline():
    modules = runner.discover_versions()
    assert modules, "no migration modules discovered"
    return modules[0]


def test_baseline_declares_the_module_contract():
    module = baseline()
    assert module.VERSION == 1
    assert module.NAME == "baseline"
    assert module.TRANSACTIONAL is True
    assert inspect.iscoroutinefunction(module.upgrade)


def test_baseline_docstring_freezes_the_ddl():
    doc = baseline().__doc__ or ""
    assert "new version" in doc.lower()


def test_baseline_executes_the_ddl_with_dims_substituted():
    conn = FakeConn()
    asyncio.run(baseline().upgrade(conn))
    assert len(conn.executed) == 1
    sql = conn.executed[0][0]
    assert "CREATE TABLE IF NOT EXISTS repos" in sql
    assert "CREATE TABLE IF NOT EXISTS organizations" in sql
    # str.format has run: the doubled braces are collapsed.
    assert "'{}'::jsonb" in sql
    assert "{dims}" not in sql


def test_init_db_calls_the_runner_and_executes_no_ddl(monkeypatch):
    conn = FakeConn()
    pool = FakePool(conn)
    seen = []

    async def fake_get_pool():
        return pool

    async def fake_run_migrations(arg):
        seen.append(arg)
        return [1]

    monkeypatch.setattr(db, "get_pool", fake_get_pool)
    monkeypatch.setattr(db, "run_migrations", fake_run_migrations)

    asyncio.run(db.init_db())

    assert seen == [pool]
    assert conn.executed == []
    assert "DDL.format" not in inspect.getsource(db.init_db)


def test_ddl_is_marked_frozen_in_db_py():
    source = inspect.getsource(db)
    header = source.split('DDL = """')[0]
    assert "0001_baseline" in header
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_migrations_baseline.py`
Expected: FAIL — `assert modules, "no migration modules discovered"` (the `versions/` directory holds only `__init__.py`).

- [ ] **Step 3: Create the baseline migration module**

Create `services/server/src/migrations/versions/0001_baseline.py`:

```python
"""Baseline schema, frozen: replays src.db.DDL as it stood at version 1.

Never edit this module or src.db.DDL. Every schema change from here on is a
new version module, src/migrations/versions/NNNN_<name>.py.
"""
from __future__ import annotations

from src.config import get_settings
from src.db import DDL

VERSION = 1
NAME = "baseline"
TRANSACTIONAL = True


async def upgrade(conn) -> None:
    await conn.execute(DDL.format(dims=get_settings().embeddings_dims))
```

- [ ] **Step 4: Freeze the DDL string and rewire `init_db` in `src/db.py`**

Add the runner import next to the existing imports at the top of `services/server/src/db.py`:

```python
from .config import get_settings
from .migrations.runner import run_migrations
```

Add one comment line immediately above `DDL = """` (line 16):

```python
# Frozen baseline schema replayed by migrations/versions/0001_baseline.py: new changes go in a new version module.
DDL = """
```

Replace the whole body of `init_db` (lines 929–934) with:

```python
async def init_db() -> None:
    pool = await get_pool()
    applied = await run_migrations(pool)
    logger.info("Database initialized (migrations applied: %s)", applied or "none")
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_migrations_baseline.py`
Expected: PASS (5 passed).

- [ ] **Step 6: Run the three existing DDL tests to prove the string is untouched**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_db_schema.py tests/test_projects_schema.py tests/test_oauth_bridge_schema.py tests/test_project_migration.py`
Expected: PASS. If `test_ddl_still_formats` fails you edited `DDL` — revert that edit.

- [ ] **Step 7: Commit**

```bash
git add services/server/src/migrations/versions/0001_baseline.py services/server/src/db.py services/server/tests/test_migrations_baseline.py
git commit -m "run the baseline schema through the migration runner"
```

---

### Task 4: `forge-cli migrate` and `forge-cli migrate --status`

**Files:**
- Modify: `services/server/src/cli.py` (add `cmd_migrate`, register the `migrate` subparser inside `main()`)
- Test: `services/server/tests/test_cli_migrate.py`

**Interfaces:**
- Consumes, already present — do not redefine:
  ```python
  # src/migrations/runner.py
  async def run_migrations(pool) -> list[int]: ...
  async def current_version(pool) -> int: ...
  def discover_versions(versions_dir=None) -> list: ...
  ```
  Each discovered module exposes `VERSION: int` and `NAME: str`.
  ```python
  # src/db.py
  async def get_pool(): ...    # asyncpg pool, built from settings.database_url
  async def close_db() -> None: ...
  ```
- Produces: `src.cli.cmd_migrate(args)`, reached by `forge-cli migrate` and `forge-cli migrate --status`.

**Context you need (do not go looking for it):**
- `services/server/src/cli.py` is a plain argparse CLI. Existing commands are `configure` and `test`; both are httpx calls to a running server, and neither touches the database. Its structure:
  ```python
  def cmd_configure(args: argparse.Namespace) -> None: ...
  def cmd_test(args: argparse.Namespace) -> None: ...

  def main() -> None:
      parser = argparse.ArgumentParser(...)
      parser.add_argument("--version", action="version", version="%(prog)s 1.0.0")
      subparsers = parser.add_subparsers(dest="command", help="Available commands")
      configure_parser = subparsers.add_parser("configure", help="Configure CLI settings")
      ...
      configure_parser.set_defaults(func=cmd_configure)
      test_parser = subparsers.add_parser("test", help="Test connection to server")
      ...
      test_parser.set_defaults(func=cmd_test)
      args = parser.parse_args()
      if not args.command:
          parser.print_help()
          sys.exit(1)
      args.func(args)
  ```
- The connection string comes from `DATABASE_URL` via `src.config.get_settings().database_url`, which `src.db.get_pool()` already reads. `cmd_migrate` must not build its own connection.
- `cli.py` is `forge-cli = "src.cli:main"` in `pyproject.toml`, and also runs as `python -m src.cli`. In both cases `__package__ == "src"`, so relative imports (`from .db import ...`) work. Import inside `cmd_migrate`, not at module top: the CLI must keep starting without asyncpg-time settings for `configure`/`test`.
- `asyncio` is not currently imported in `cli.py`; import it inside `cmd_migrate`.

- [ ] **Step 1: Write the failing test**

Create `services/server/tests/test_cli_migrate.py`:

```python
"""forge-cli migrate applies pending versions; --status only reports."""
import argparse

from src import cli, db
from src.migrations import runner


class FakeModule:
    def __init__(self, version, name):
        self.VERSION = version
        self.NAME = name


def _patch(monkeypatch, *, applied=None, version=0, modules=()):
    calls = {"run": 0, "closed": 0}
    pool = object()

    async def fake_get_pool():
        return pool

    async def fake_close_db():
        calls["closed"] += 1

    async def fake_run_migrations(arg):
        calls["run"] += 1
        assert arg is pool
        return list(applied or [])

    async def fake_current_version(arg):
        assert arg is pool
        return version

    monkeypatch.setattr(db, "get_pool", fake_get_pool)
    monkeypatch.setattr(db, "close_db", fake_close_db)
    monkeypatch.setattr(runner, "run_migrations", fake_run_migrations)
    monkeypatch.setattr(runner, "current_version", fake_current_version)
    monkeypatch.setattr(runner, "discover_versions", lambda *a, **k: list(modules))
    return calls


def test_migrate_prints_the_versions_it_applied(monkeypatch, capsys):
    calls = _patch(monkeypatch, applied=[1, 2])
    cli.cmd_migrate(argparse.Namespace(status=False))
    out = capsys.readouterr().out
    assert "Applied: 1, 2" in out
    assert calls["run"] == 1
    assert calls["closed"] == 1


def test_migrate_says_so_when_there_is_nothing_to_do(monkeypatch, capsys):
    _patch(monkeypatch, applied=[])
    cli.cmd_migrate(argparse.Namespace(status=False))
    assert "Already up to date" in capsys.readouterr().out


def test_status_prints_current_version_and_pending_modules(monkeypatch, capsys):
    calls = _patch(
        monkeypatch,
        version=1,
        modules=[FakeModule(1, "baseline"), FakeModule(2, "jobs_retry")],
    )
    cli.cmd_migrate(argparse.Namespace(status=True))
    out = capsys.readouterr().out
    assert "Current schema version: 1" in out
    assert "0002_jobs_retry" in out
    assert "0001_baseline" not in out
    assert calls["run"] == 0


def test_status_reports_an_up_to_date_database(monkeypatch, capsys):
    _patch(monkeypatch, version=1, modules=[FakeModule(1, "baseline")])
    cli.cmd_migrate(argparse.Namespace(status=True))
    assert "no pending migrations" in capsys.readouterr().out


def test_parser_exposes_migrate_and_its_status_flag(monkeypatch):
    parsed = {}

    def fake_migrate(args):
        parsed["status"] = args.status

    monkeypatch.setattr(cli, "cmd_migrate", fake_migrate)
    monkeypatch.setattr("sys.argv", ["forge-cli", "migrate", "--status"])
    cli.main()
    assert parsed["status"] is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_cli_migrate.py`
Expected: FAIL — `AttributeError: module 'src.cli' has no attribute 'cmd_migrate'`.

- [ ] **Step 3: Add `cmd_migrate` to `src/cli.py`**

Insert after `cmd_test` and before `def main() -> None:`:

```python
def cmd_migrate(args: argparse.Namespace) -> None:
    """Apply pending schema migrations, or report the current version."""
    import asyncio

    from .db import close_db, get_pool
    from .migrations.runner import current_version, discover_versions, run_migrations

    async def _run() -> None:
        pool = await get_pool()
        try:
            if args.status:
                version = await current_version(pool)
                print(f"Current schema version: {version}")
                pending = [m for m in discover_versions() if m.VERSION > version]
                for module in pending:
                    print(f"  pending: {module.VERSION:04d}_{module.NAME}")
                if not pending:
                    print("  no pending migrations")
            else:
                applied = await run_migrations(pool)
                if applied:
                    print("Applied: " + ", ".join(str(v) for v in applied))
                else:
                    print("Already up to date")
        finally:
            await close_db()

    asyncio.run(_run())
```

Note: `from .db import close_db, get_pool` resolves the names on the module object at call time, which is why the test's `monkeypatch.setattr(db, "get_pool", ...)` takes effect.

- [ ] **Step 4: Register the subparser inside `main()`**

Immediately after the `test_parser.set_defaults(func=cmd_test)` line and before `args = parser.parse_args()`:

```python
    # Migrate command
    migrate_parser = subparsers.add_parser(
        "migrate", help="Apply pending database schema migrations"
    )
    migrate_parser.add_argument(
        "--status",
        action="store_true",
        help="Print the current schema version and the pending migrations",
    )
    migrate_parser.set_defaults(func=cmd_migrate)
```

Leave the rest of `main()` alone — the existing `args.func(args)` dispatch already handles it. `main()` builds the parser on every call, so the `cmd_migrate` global is resolved at call time and `monkeypatch.setattr(cli, "cmd_migrate", ...)` in the test is honoured. Do not add a lambda and do not change the dispatch line.

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_cli_migrate.py`
Expected: PASS (5 passed).

- [ ] **Step 6: Check the CLI help still renders**

Run: `.venv/Scripts/python.exe -m src.cli --help`
Expected: the command list includes `migrate`. Then run `.venv/Scripts/python.exe -m src.cli migrate --help` and confirm `--status` is listed. Neither command touches the database.

- [ ] **Step 7: Commit**

```bash
git add services/server/src/cli.py services/server/tests/test_cli_migrate.py
git commit -m "add forge-cli migrate and migrate --status"
```

---

### Task 5: README "Development" section and full-suite verification

**Files:**
- Modify: `README.md` (insert a `## Development` section after `### Upgrading an existing installation` and before `## Projects and the MCP endpoint`)
- Test: the whole server suite

**Interfaces:**
- Consumes, all already present — the README documents them and must match them exactly:
  ```python
  # src/migrations/runner.py
  MIGRATIONS_LOCK_KEY = 0x43464D47
  async def run_migrations(pool) -> list[int]: ...
  async def current_version(pool) -> int: ...
  def discover_versions(versions_dir=None) -> list: ...
  ```
  ```python
  # every src/migrations/versions/NNNN_<name>.py
  VERSION: int
  NAME: str
  TRANSACTIONAL: bool = True
  async def upgrade(conn) -> None: ...
  ```
- Produces: nothing consumed by later tasks in this plan. Later features' plans point at this README section.

**Context you need (do not go looking for it):**
- `README.md` is 147 lines. Its headings in order are: `# context-forge`, `## Features`, `## Architecture`, `## Quick start`, `### Upgrading an existing installation`, `## Projects and the MCP endpoint`, `## MCP tools`, `## Agent setup`, `### Claude Code with OIDC (browser login)`, `## Security`, `### MCP permissions`, `## License`.
- README copy is English. The product is `context-forge` / `ContextForge`, never `askme`.
- The `### Upgrading an existing installation` section ends with the bullet about embedding columns losing their fixed dimension. Insert the new section right after that bullet list, before the `## Projects and the MCP endpoint` heading.

- [ ] **Step 1: Insert the `## Development` section into `README.md`**

Paste exactly this, between the end of `### Upgrading an existing installation` and the `## Projects and the MCP endpoint` heading:

````markdown
## Development

### Schema migrations

The schema is versioned. Every change is a new module under `services/server/src/migrations/versions/`, named `NNNN_<name>.py`:

```python
"""Add the retry bookkeeping columns to jobs."""
from __future__ import annotations

VERSION = 2
NAME = "jobs_retry"
TRANSACTIONAL = True


async def upgrade(conn) -> None:
    await conn.execute(
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS attempts INT NOT NULL DEFAULT 0"
    )
```

- `VERSION` is the file number and `NAME` the file suffix; both are recorded in the `schema_migrations` table.
- Modules run once, in `VERSION` order, at server startup, on a single connection holding a Postgres advisory lock, so concurrent replicas cannot race. A module that raises aborts the boot; nothing after it runs.
- `TRANSACTIONAL = True` wraps the module in a transaction. Set it to `False` only when the statement cannot run inside one (`CREATE INDEX CONCURRENTLY`); such a module must be idempotent, because a failure leaves it half-applied and unrecorded.
- **Never edit `services/server/src/db.py:DDL`.** It is frozen as `versions/0001_baseline.py`, and every existing installation has already applied it.

From `services/server`:

```bash
.venv/Scripts/python.exe -m src.cli migrate --status   # current version and pending modules
.venv/Scripts/python.exe -m src.cli migrate            # apply pending modules
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider
```

Both `migrate` forms connect with `DATABASE_URL`, the same setting the server uses.
````

- [ ] **Step 2: Cross-check the README against the code**

Confirm by reading the files that the section is accurate:
- `services/server/src/migrations/versions/0001_baseline.py` exists and declares `VERSION = 1`, `NAME = "baseline"`, `TRANSACTIONAL = True`, `async def upgrade(conn) -> None`.
- `services/server/src/migrations/runner.py` creates `schema_migrations` and takes `pg_advisory_lock`.
- `services/server/src/cli.py` has the `migrate` subcommand with `--status`.

Fix the README, not the code, if anything disagrees.

- [ ] **Step 3: Run the full server suite**

Run from `services/server`:

```bash
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider
```

Expected: PASS, **552 passed** (530 before this plan, plus 5 + 7 + 5 + 5 new tests). No failures, no errors. If the count differs but everything passes, that is fine — a failure is not.

If `tests/test_db_schema.py`, `tests/test_projects_schema.py` or `tests/test_oauth_bridge_schema.py` fails, the frozen `DDL` string was edited: revert that edit.

- [ ] **Step 4: Confirm nothing outside the plan's file list changed**

Run: `git status --short`
Expected: only `README.md`, `services/server/src/cli.py`, `services/server/src/db.py`, `services/server/src/migrations/**`, and the five new/changed files under `services/server/tests/`. Nothing under `services/ui`, nothing under `services/server/src/api/routes/`.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "document versioned schema migrations"
```

---

## Verification summary

After Task 5 the following must all be true:

- `services/server/src/migrations/runner.py` exposes `MIGRATIONS_LOCK_KEY = 0x43464D47`, `async def run_migrations(pool) -> list[int]`, `async def current_version(pool) -> int`, `def discover_versions(versions_dir=None) -> list`.
- `services/server/src/migrations/versions/0001_baseline.py` exposes `VERSION = 1`, `NAME = "baseline"`, `TRANSACTIONAL = True`, `async def upgrade(conn) -> None`.
- `src.db.init_db()` executes no DDL of its own; it calls `run_migrations(pool)`.
- `src.tenancy.ensure_tenant_storage()` and `src/main.py` are unchanged.
- `src.db.DDL` is unchanged, and is marked frozen by the comment above it.
- `forge-cli migrate` and `forge-cli migrate --status` work.
- `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider` passes from `services/server`.
