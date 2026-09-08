# HNSW vector indexes per organization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every organization a partial HNSW index on the typed embedding cast for its own dimension, and make every vector query order by exactly that expression so the planner uses it.

**Architecture:** A new module `src/vector_index.py` owns the index naming, the DDL text, and the lifecycle (`ensure_org_indexes`, `ensure_all_indexes`) using `CREATE INDEX CONCURRENTLY` on an autocommit pooled connection. `src/search.py` stops holding six module-level SQL constants and instead builds the same SQL per embedding dimension, ordering by `(embedding::vector(N)) <=> $1::vector(N)` and setting `hnsw.ef_search` inside the search transaction. Three call sites trigger the lifecycle: boot (background, after `ensure_tenant_storage`), the end of `reembed_org`, and a settings save that did not change the dimension.

**Tech Stack:** Python 3.11 (local dev 3.14), asyncpg raw SQL, PostgreSQL 16 + pgvector, FastAPI, pytest.

**Spec:** `docs/superpowers/specs/2026-09-07-hnsw-indexes-design.md`

## Global Constraints

Copied verbatim from `docs/superpowers/specs/2026-09-07-platform-improvements-roadmap.md`, section "Global constraints (bind every plan)". Every task's requirements implicitly include this section.

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

### Feature-specific constraints

- **No schema migration.** This feature adds no module under `src/migrations/versions/`
  and does not edit the DDL string in `src/db.py`. The indexes are runtime-managed.
  (Roadmap migration slot for this feature: "none (runtime-managed indexes)".)
- **This feature adds no new MCP tool, no new permission, no new env var, and no UI change.**
  Nothing under `services/ui` is touched.
- **Every command below runs from `services/server`** and uses the repo venv:
  `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/<file>`.
  There is no pytest-asyncio plugin: async code is driven with `asyncio.run(...)`,
  as every existing test does.
- **Fake-pool pattern.** The committed pattern this plan follows is the local
  `FakeConn` / `FakePool` / `_patch(monkeypatch, ...)` trio used by
  `services/server/tests/test_repo_registry.py:7-56` (and
  `tests/test_project_resource_counts.py:5-40`,
  `tests/test_code_graph_tools.py:11-56`): a `FakePool.acquire()` returning an
  async context manager over a recording connection, installed with
  `monkeypatch.setattr(<module under test>, "get_pool", fake_pool)`. `search.py`,
  `vector_index.py` and `reembed.py` all do `from .db import get_pool`, so the
  name must be patched **on the module under test**, never on `src.db`.
  Feature 1 may leave a shared `services/server/tests/fake_db.py` behind; this
  plan deliberately keeps its own fakes inline, because two tasks need
  behaviours that helper does not offer (a `fetch` that answers per table, and
  an `execute` that raises for one index name).
- **Baseline:** the full server suite is green at 530 passed before this plan starts.

---

### Task 1: `src/vector_index.py` — index names, cast expression, DDL text

**Files:**
- Create: `services/server/src/vector_index.py`
- Test: `services/server/tests/test_vector_index_sql.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces (Tasks 2, 3 and 4 rely on these exact names):
  - `HNSW_TABLES: tuple[str, ...] = ("repo_chunks", "kb_chunks", "web_chunks")`
  - `HNSW_M = 16`, `HNSW_EF_CONSTRUCTION = 64`, `HNSW_EF_SEARCH = 100`,
    `MAINTENANCE_WORK_MEM = "256MB"`
  - `index_name(table: str, org_id: int, dims: int) -> str`
  - `vector_expr(dims: int, column: str = "embedding") -> str`
  - `create_index_sql(table: str, org_id: int, dims: int) -> str`
  - `drop_index_sql(name: str) -> str`

**Background you need (do not go looking for it):** the `embedding` columns of
`repo_chunks`, `kb_chunks` and `web_chunks` are declared as bare `vector`
(no dimension), so pgvector cannot index them directly. The index is therefore
built on the *expression* `(embedding::vector(N))` and made partial on one
organization, so each org can carry a different embedding model.

- [ ] **Step 1: Write the failing test**

Create `services/server/tests/test_vector_index_sql.py`:

```python
"""Naming and DDL text of the per-organization HNSW indexes."""
import pytest

from src.vector_index import (
    HNSW_EF_CONSTRUCTION,
    HNSW_EF_SEARCH,
    HNSW_M,
    HNSW_TABLES,
    MAINTENANCE_WORK_MEM,
    create_index_sql,
    drop_index_sql,
    index_name,
    vector_expr,
)


def test_index_name_follows_the_table_org_dims_pattern():
    assert index_name("repo_chunks", 42, 1536) == "repo_chunks_emb_hnsw_org42_d1536"
    assert index_name("kb_chunks", 1, 768) == "kb_chunks_emb_hnsw_org1_d768"


def test_vector_expr_is_the_typed_cast_used_by_index_and_queries():
    assert vector_expr(1536) == "(embedding::vector(1536))"
    assert vector_expr(1024, "c.embedding") == "(c.embedding::vector(1024))"


def test_create_index_sql_is_concurrent_partial_and_typed():
    assert create_index_sql("repo_chunks", 42, 1536) == (
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS repo_chunks_emb_hnsw_org42_d1536 "
        "ON repo_chunks USING hnsw ((embedding::vector(1536)) vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64) "
        "WHERE org_id = 42"
    )


def test_create_index_sql_rejects_a_table_outside_the_allowlist():
    # The table name is interpolated into DDL: only the three known tables pass.
    with pytest.raises(ValueError):
        create_index_sql("organizations", 1, 1536)


def test_drop_index_sql_is_concurrent_and_idempotent():
    assert drop_index_sql("kb_chunks_emb_hnsw_org1_d768") == (
        "DROP INDEX CONCURRENTLY IF EXISTS kb_chunks_emb_hnsw_org1_d768"
    )


def test_constants():
    assert HNSW_TABLES == ("repo_chunks", "kb_chunks", "web_chunks")
    assert (HNSW_M, HNSW_EF_CONSTRUCTION, HNSW_EF_SEARCH) == (16, 64, 100)
    assert MAINTENANCE_WORK_MEM == "256MB"
```

- [ ] **Step 2: Run the test to verify it fails**

Run from `services/server`:

```
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_vector_index_sql.py
```

Expected: collection error — `ModuleNotFoundError: No module named 'src.vector_index'`.

- [ ] **Step 3: Write the module**

Create `services/server/src/vector_index.py`:

```python
"""HNSW vector indexes per organization.

The ``embedding`` columns are untyped ``vector`` so each organization can pick
its own model; pgvector cannot index them directly, so every organization gets
a partial index on the typed cast for its own dimension.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Tables whose ``embedding`` column is searched by cosine distance.
HNSW_TABLES: tuple[str, ...] = ("repo_chunks", "kb_chunks", "web_chunks")

HNSW_M = 16
HNSW_EF_CONSTRUCTION = 64
HNSW_EF_SEARCH = 100
MAINTENANCE_WORK_MEM = "256MB"


def index_name(table: str, org_id: int, dims: int) -> str:
    return f"{table}_emb_hnsw_org{int(org_id)}_d{int(dims)}"


def vector_expr(dims: int, column: str = "embedding") -> str:
    """Typed cast shared by the index definition and every ORDER BY."""
    return f"({column}::vector({int(dims)}))"


def create_index_sql(table: str, org_id: int, dims: int) -> str:
    if table not in HNSW_TABLES:
        raise ValueError(f"unknown vector table: {table}")
    return (
        f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {index_name(table, org_id, dims)} "
        f"ON {table} USING hnsw ({vector_expr(dims)} vector_cosine_ops) "
        f"WITH (m = {HNSW_M}, ef_construction = {HNSW_EF_CONSTRUCTION}) "
        f"WHERE org_id = {int(org_id)}"
    )


def drop_index_sql(name: str) -> str:
    return f"DROP INDEX CONCURRENTLY IF EXISTS {name}"
```

- [ ] **Step 4: Run the test to verify it passes**

```
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_vector_index_sql.py
```

Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add services/server/src/vector_index.py services/server/tests/test_vector_index_sql.py
git commit -m "add HNSW index naming and DDL builders"
```

---

### Task 2: index lifecycle — `ensure_org_indexes` and `ensure_all_indexes`

**Files:**
- Modify: `services/server/src/vector_index.py` (append; keep everything Task 1 wrote)
- Test: `services/server/tests/test_vector_index_lifecycle.py`

**Interfaces:**
- Consumes (already in `src/vector_index.py` from Task 1, do not redefine):
  - `HNSW_TABLES = ("repo_chunks", "kb_chunks", "web_chunks")`
  - `MAINTENANCE_WORK_MEM = "256MB"`
  - `index_name(table, org_id, dims) -> str` → `"repo_chunks_emb_hnsw_org42_d1536"`
  - `create_index_sql(table, org_id, dims) -> str` → the full
    `CREATE INDEX CONCURRENTLY IF NOT EXISTS ... WHERE org_id = 42` statement
  - `drop_index_sql(name) -> str` → `"DROP INDEX CONCURRENTLY IF EXISTS <name>"`
- Produces (Tasks 3 and 4 rely on these):
  - `async def ensure_org_indexes(org_id: int, dims: int) -> list[str]` — returns the
    index names it created; never raises.
  - `async def ensure_all_indexes() -> None` — never raises.

**Background you need:**
- `src.db.get_pool()` returns an `asyncpg` pool. `pool.acquire()` is an async
  context manager yielding a connection. asyncpg runs statements in autocommit
  unless you open `conn.transaction()`, and a statement passed to `conn.execute`
  **without arguments** uses the simple query protocol — both are required for
  `CREATE INDEX CONCURRENTLY` / `DROP INDEX CONCURRENTLY`, which cannot run
  inside a transaction block. So: never wrap these in `conn.transaction()`, and
  never pass bind parameters to them.
- asyncpg resets the session (`RESET ALL`) when a connection returns to the pool,
  so `SET maintenance_work_mem` does not leak to other users of the pool.
- `src.org_config.all_org_ids()` is an existing coroutine returning
  `list[int]` (`SELECT id FROM organizations ORDER BY id`).
- `src.org_settings.get_org_settings(org_id)` is an existing coroutine returning an
  `OrgSettings` pydantic model whose `embeddings_dims: int = 1536` field is the
  organization's effective embedding dimension.

- [ ] **Step 1: Write the failing test**

Create `services/server/tests/test_vector_index_lifecycle.py`:

```python
"""ensure_org_indexes / ensure_all_indexes against a fake pool (no live DB)."""
import asyncio

from src import vector_index
from src.org_settings import OrgSettings


class FakeConn:
    """Records every statement; ``fetch`` answers the pg_indexes lookup."""

    def __init__(self, stale=None, fail_on=()):
        self.executed = []
        self.stale = stale or {}
        self.fail_on = fail_on

    async def execute(self, sql, *args):
        self.executed.append(sql)
        if any(token in sql for token in self.fail_on):
            raise RuntimeError("boom")

    async def fetch(self, sql, *args):
        table = args[0]
        return [{"indexname": name} for name in self.stale.get(table, [])]


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


def _patch_pool(monkeypatch, conn):
    async def fake_pool():
        return FakePool(conn)

    monkeypatch.setattr(vector_index, "get_pool", fake_pool)


def test_creates_one_index_per_table_after_raising_work_mem(monkeypatch):
    conn = FakeConn()
    _patch_pool(monkeypatch, conn)

    created = asyncio.run(vector_index.ensure_org_indexes(42, 1536))

    assert created == [
        "repo_chunks_emb_hnsw_org42_d1536",
        "kb_chunks_emb_hnsw_org42_d1536",
        "web_chunks_emb_hnsw_org42_d1536",
    ]
    assert conn.executed[0] == "SET maintenance_work_mem = '256MB'"
    creates = [s for s in conn.executed if s.startswith("CREATE INDEX CONCURRENTLY")]
    assert len(creates) == 3
    assert "(embedding::vector(1536)) vector_cosine_ops" in creates[0]
    assert creates[0].endswith("WHERE org_id = 42")


def test_drops_only_the_stale_dimension_of_the_same_org(monkeypatch):
    conn = FakeConn(stale={"repo_chunks": [
        "repo_chunks_emb_hnsw_org42_d1536",
        "repo_chunks_emb_hnsw_org42_d768",
    ]})
    _patch_pool(monkeypatch, conn)

    asyncio.run(vector_index.ensure_org_indexes(42, 1536))

    drops = [s for s in conn.executed if s.startswith("DROP INDEX CONCURRENTLY")]
    assert drops == [
        "DROP INDEX CONCURRENTLY IF EXISTS repo_chunks_emb_hnsw_org42_d768"
    ]


def test_a_failing_build_is_swallowed_and_the_others_still_run(monkeypatch):
    conn = FakeConn(fail_on=("kb_chunks_emb_hnsw_org7_d1536",))
    _patch_pool(monkeypatch, conn)

    created = asyncio.run(vector_index.ensure_org_indexes(7, 1536))

    assert created == [
        "repo_chunks_emb_hnsw_org7_d1536",
        "web_chunks_emb_hnsw_org7_d1536",
    ]


def test_a_broken_pool_never_reaches_the_caller(monkeypatch):
    async def boom():
        raise RuntimeError("no database")

    monkeypatch.setattr(vector_index, "get_pool", boom)

    assert asyncio.run(vector_index.ensure_org_indexes(1, 1536)) == []


def test_ensure_all_indexes_uses_each_organization_dimension(monkeypatch):
    calls = []

    async def fake_all_org_ids():
        return [1, 2]

    async def fake_get_org_settings(org_id):
        return OrgSettings(embeddings_dims=1536 if org_id == 1 else 768)

    async def fake_ensure(org_id, dims):
        calls.append((org_id, dims))
        return []

    monkeypatch.setattr(vector_index, "all_org_ids", fake_all_org_ids)
    monkeypatch.setattr(vector_index, "get_org_settings", fake_get_org_settings)
    monkeypatch.setattr(vector_index, "ensure_org_indexes", fake_ensure)

    asyncio.run(vector_index.ensure_all_indexes())

    assert calls == [(1, 1536), (2, 768)]
```

- [ ] **Step 2: Run the test to verify it fails**

```
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_vector_index_lifecycle.py
```

Expected: FAIL — `AttributeError: module 'src.vector_index' has no attribute 'get_pool'`
(and `ensure_org_indexes` does not exist).

- [ ] **Step 3: Append the lifecycle to `src/vector_index.py`**

Add these imports directly under `import logging` at the top of the module:

```python
from .db import get_pool
from .org_config import all_org_ids
from .org_settings import get_org_settings
```

Append at the end of the file:

```python
_STALE_INDEX_SQL = (
    "SELECT indexname FROM pg_indexes WHERE tablename = $1 AND indexname LIKE $2"
)


async def _stale_index_names(conn, table: str, org_id: int, keep: str) -> list[str]:
    rows = await conn.fetch(
        _STALE_INDEX_SQL, table, f"{table}_emb_hnsw_org{int(org_id)}_d%"
    )
    return [r["indexname"] for r in rows if r["indexname"] != keep]


async def ensure_org_indexes(org_id: int, dims: int) -> list[str]:
    """Create the org's HNSW indexes and drop those of another dimension."""
    created: list[str] = []
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            # CONCURRENTLY needs autocommit: never open a transaction here.
            await conn.execute(f"SET maintenance_work_mem = '{MAINTENANCE_WORK_MEM}'")
            for table in HNSW_TABLES:
                name = index_name(table, org_id, dims)
                try:
                    await conn.execute(create_index_sql(table, org_id, dims))
                    created.append(name)
                except Exception:
                    logger.exception("HNSW index build failed: %s", name)
                try:
                    for stale in await _stale_index_names(conn, table, org_id, name):
                        await conn.execute(drop_index_sql(stale))
                except Exception:
                    logger.exception("HNSW stale index cleanup failed: %s", name)
    except Exception:
        logger.exception("HNSW index maintenance failed (org=%s)", org_id)
    return created


async def ensure_all_indexes() -> None:
    """Refresh the HNSW indexes of every organization."""
    try:
        org_ids = await all_org_ids()
    except Exception:
        logger.exception("HNSW index maintenance: cannot list organizations")
        return
    for org_id in org_ids:
        try:
            dims = int((await get_org_settings(org_id)).embeddings_dims)
        except Exception:
            logger.exception("HNSW index maintenance: no settings (org=%s)", org_id)
            continue
        await ensure_org_indexes(org_id, dims)
```

- [ ] **Step 4: Run the test to verify it passes**

```
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_vector_index_lifecycle.py tests/test_vector_index_sql.py
```

Expected: `11 passed`.

- [ ] **Step 5: Commit**

```bash
git add services/server/src/vector_index.py services/server/tests/test_vector_index_lifecycle.py
git commit -m "create and prune HNSW indexes per organization"
```

---

### Task 3: type every vector query and set `hnsw.ef_search`

**Files:**
- Modify: `services/server/src/search.py` (imports; the six SQL constants become
  builder functions; the three search coroutines)
- Modify: `services/server/tests/test_repo_project_scope.py:7`
- Modify: `services/server/tests/test_kb_project_scope.py:14-15`
- Modify: `services/server/tests/test_web_project_scope.py:22-23`
- Test: `services/server/tests/test_search_hnsw_sql.py`

**Interfaces:**
- Consumes (already in `src/vector_index.py`, do not redefine):
  - `HNSW_EF_SEARCH = 100`
  - `vector_expr(dims: int, column: str = "embedding") -> str`, e.g.
    `vector_expr(1536) == "(embedding::vector(1536))"` and
    `vector_expr(1536, "c.embedding") == "(c.embedding::vector(1536))"`
- Produces: module-private SQL builders in `src/search.py` —
  `_repo_hybrid_sql(dims)`, `_repo_vector_sql(dims)`, `_web_hybrid_sql(dims)`,
  `_web_vector_sql(dims)`, `_kb_hybrid_sql(dims)`, `_kb_vector_sql(dims)`,
  each `(int) -> str`. The six module-level constants `_REPO_HYBRID_SQL`,
  `_REPO_VECTOR_SQL`, `_WEB_HYBRID_SQL`, `_WEB_VECTOR_SQL`, `_KB_HYBRID_SQL`,
  `_KB_VECTOR_SQL` **disappear**. `_SYMBOL_SEARCH_SQL` stays a constant (no vector).

**Why this shape:** the planner only uses an expression index when the query
orders by the *same* expression, and only uses a partial index when it can prove
the query's `WHERE` implies the index predicate. Hence `dims` is interpolated
into the SQL as an integer (never a bind parameter) and `org_id` stays `$2`:
PostgreSQL plans a parameterised statement with a custom plan whenever the
generic plan is more expensive, which it always is here (a sequential scan).

**Existing code you are changing (current `src/search.py`):** it defines
`RRF_K = 60`, `CANDIDATE_POOL = 50`, `TSQUERY_CONFIG = "english"`,
`_vector_to_pg(embedding: list[float]) -> str`, `hybrid_enabled() -> bool`,
`_parse_metadata`, `_normalize_scores`, and the coroutines
`search_repo_chunks`, `search_repo_symbols`, `search_web_chunks`,
`search_kb_chunks`. Keep all of them; only the pieces shown below change.

- [ ] **Step 1: Write the failing test**

Create `services/server/tests/test_search_hnsw_sql.py`:

```python
"""Vector SQL must match the HNSW index expression and raise ef_search."""
import asyncio

import pytest

from src import search
from src.org_settings import OrgSettings


class FakeConn:
    def __init__(self):
        self.executed = []
        self.fetched = []

    async def execute(self, sql, *args):
        self.executed.append(sql)

    async def fetch(self, sql, *args):
        self.fetched.append(sql)
        return []

    def transaction(self):
        class _Tx:
            async def __aenter__(self):
                return None

            async def __aexit__(self, *exc):
                return False

        return _Tx()


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


def _patch(monkeypatch, dims=1536, hybrid=True):
    conn = FakeConn()

    async def fake_pool():
        return FakePool(conn)

    async def fake_embed_text(text, org_id):
        return [0.1] * dims

    async def fake_org_settings(org_id):
        return OrgSettings(embeddings_dims=dims)

    monkeypatch.setattr(search, "get_pool", fake_pool)
    monkeypatch.setattr(search, "embed_text", fake_embed_text)
    monkeypatch.setattr(search, "get_org_settings", fake_org_settings)
    monkeypatch.setattr(search, "hybrid_enabled", lambda: hybrid)
    return conn


@pytest.mark.parametrize("hybrid", [True, False])
def test_repo_search_orders_by_the_indexed_expression(monkeypatch, hybrid):
    conn = _patch(monkeypatch, hybrid=hybrid)

    asyncio.run(search.search_repo_chunks(42, "q", project_id=7))

    sql = conn.fetched[0]
    assert "ORDER BY (embedding::vector(1536)) <=> $1::vector(1536)" in sql
    assert "WHERE org_id = $2" in sql
    assert "embedding <=> $1::vector)" not in sql


@pytest.mark.parametrize("hybrid", [True, False])
def test_kb_and_web_search_use_the_aliased_typed_cast(monkeypatch, hybrid):
    conn = _patch(monkeypatch, dims=768, hybrid=hybrid)

    asyncio.run(search.search_kb_chunks(42, "q", project_id=7))
    asyncio.run(search.search_web_chunks(42, "q", project_id=7))

    for sql in conn.fetched:
        assert "ORDER BY (c.embedding::vector(768)) <=> $1::vector(768)" in sql
        assert "WHERE c.org_id = $2" in sql


def test_every_vector_search_sets_ef_search_in_a_transaction(monkeypatch):
    conn = _patch(monkeypatch)

    asyncio.run(search.search_repo_chunks(42, "q", project_id=7))
    asyncio.run(search.search_kb_chunks(42, "q", project_id=7))
    asyncio.run(search.search_web_chunks(42, "q", project_id=7))

    assert conn.executed == ["SET LOCAL hnsw.ef_search = 100"] * 3


def test_hybrid_sql_keeps_the_lexical_half_and_the_parameter_layout(monkeypatch):
    sql = search._repo_hybrid_sql(1536)
    assert "websearch_to_tsquery('english', $4)" in sql
    assert "ts_rank_cd(c.content_tsv, tsq.query)" in sql
    assert "LIMIT $6" in sql
    assert "($7::bigint IS NULL OR project_id = $7)" in sql
```

- [ ] **Step 2: Run the test to verify it fails**

```
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_search_hnsw_sql.py
```

Expected: FAIL — `AttributeError: module 'src.search' has no attribute 'get_org_settings'`.

- [ ] **Step 3: Replace the imports at the top of `src/search.py`**

Replace the existing import block (currently `import json`, `import logging`,
`from typing import ...`, `from .config import get_settings`,
`from .db import get_pool`, `from .indexer.embedder import embed_text`) with:

```python
import json
import logging
from functools import lru_cache
from typing import Any, Optional

from .config import get_settings
from .db import get_pool
from .indexer.embedder import embed_text
from .org_settings import get_org_settings
from .vector_index import HNSW_EF_SEARCH, vector_expr
```

- [ ] **Step 4: Replace the repo SQL constants with builders**

Delete `_REPO_HYBRID_SQL = f"""..."""` and `_REPO_VECTOR_SQL = """..."""` and put
in their place:

```python
# Both branches keep a stable parameter layout so the optional repo filter never
# needs dynamic placeholder renumbering:
#   $1 embedding $2 org_id $3 candidate pool $4 query $5 repos (text[] or NULL) $6 limit $7 project (bigint or NULL)
@lru_cache(maxsize=8)
def _repo_hybrid_sql(dims: int) -> str:
    expr = vector_expr(dims)
    return f"""
WITH tsq AS (
    SELECT websearch_to_tsquery('{TSQUERY_CONFIG}', $4) AS query
),
vec AS (
    SELECT id, ROW_NUMBER() OVER (ORDER BY {expr} <=> $1::vector({dims})) AS rank
    FROM repo_chunks
    WHERE org_id = $2 AND ($7::bigint IS NULL OR project_id = $7)
      AND ($5::text[] IS NULL OR repo_name = ANY($5))
    ORDER BY {expr} <=> $1::vector({dims})
    LIMIT $3
),
kw AS (
    SELECT c.id, ROW_NUMBER() OVER (ORDER BY ts_rank_cd(c.content_tsv, tsq.query) DESC) AS rank
    FROM repo_chunks c, tsq
    WHERE c.org_id = $2 AND ($7::bigint IS NULL OR c.project_id = $7)
      AND ($5::text[] IS NULL OR c.repo_name = ANY($5))
      AND c.content_tsv @@ tsq.query
    ORDER BY ts_rank_cd(c.content_tsv, tsq.query) DESC
    LIMIT $3
),
fused AS (
    SELECT COALESCE(v.id, k.id) AS id,
           COALESCE(1.0 / ({RRF_K} + v.rank), 0)
         + COALESCE(1.0 / ({RRF_K} + k.rank), 0) AS score
    FROM vec v
    FULL OUTER JOIN kw k ON v.id = k.id
)
SELECT c.repo_name, c.file_path, c.chunk_type, c.content, c.metadata, f.score
FROM fused f
JOIN repo_chunks c ON c.id = f.id
ORDER BY f.score DESC
LIMIT $6
"""


@lru_cache(maxsize=8)
def _repo_vector_sql(dims: int) -> str:
    expr = vector_expr(dims)
    return f"""
SELECT repo_name, file_path, chunk_type, content, metadata,
       1 - ({expr} <=> $1::vector({dims})) AS score
FROM repo_chunks
WHERE org_id = $2 AND ($5::bigint IS NULL OR project_id = $5)
  AND ($3::text[] IS NULL OR repo_name = ANY($3))
ORDER BY {expr} <=> $1::vector({dims})
LIMIT $4
"""
```

For `dims = 1536` these render exactly (this is the shape the index must match):

```
    SELECT id, ROW_NUMBER() OVER (ORDER BY (embedding::vector(1536)) <=> $1::vector(1536)) AS rank
    FROM repo_chunks
    WHERE org_id = $2 AND ($7::bigint IS NULL OR project_id = $7)
      AND ($5::text[] IS NULL OR repo_name = ANY($5))
    ORDER BY (embedding::vector(1536)) <=> $1::vector(1536)
    LIMIT $3
```

```
SELECT repo_name, file_path, chunk_type, content, metadata,
       1 - ((embedding::vector(1536)) <=> $1::vector(1536)) AS score
FROM repo_chunks
WHERE org_id = $2 AND ($5::bigint IS NULL OR project_id = $5)
  AND ($3::text[] IS NULL OR repo_name = ANY($3))
ORDER BY (embedding::vector(1536)) <=> $1::vector(1536)
LIMIT $4
```

- [ ] **Step 5: Replace the web SQL constants with builders**

Delete `_WEB_HYBRID_SQL` and `_WEB_VECTOR_SQL` and put in their place:

```python
#   $1 embedding  $2 org_id  $3 candidate pool  $4 query text
#   $5 page_ids (bigint[] or NULL)  $6 limit  $7 project (bigint or NULL)
@lru_cache(maxsize=8)
def _web_hybrid_sql(dims: int) -> str:
    expr = vector_expr(dims, "c.embedding")
    return f"""
WITH tsq AS (
    SELECT websearch_to_tsquery('{TSQUERY_CONFIG}', $4) AS query
),
vec AS (
    SELECT c.id, ROW_NUMBER() OVER (ORDER BY {expr} <=> $1::vector({dims})) AS rank
    FROM web_chunks c
    WHERE c.org_id = $2 AND ($7::bigint IS NULL OR c.project_id = $7)
      AND ($5::bigint[] IS NULL OR c.page_id = ANY($5))
    ORDER BY {expr} <=> $1::vector({dims})
    LIMIT $3
),
kw AS (
    SELECT c.id, ROW_NUMBER() OVER (ORDER BY ts_rank_cd(c.content_tsv, tsq.query) DESC) AS rank
    FROM web_chunks c, tsq
    WHERE c.org_id = $2 AND ($7::bigint IS NULL OR c.project_id = $7)
      AND ($5::bigint[] IS NULL OR c.page_id = ANY($5))
      AND c.content_tsv @@ tsq.query
    ORDER BY ts_rank_cd(c.content_tsv, tsq.query) DESC
    LIMIT $3
),
fused AS (
    SELECT COALESCE(v.id, k.id) AS id,
           COALESCE(1.0 / ({RRF_K} + v.rank), 0)
         + COALESCE(1.0 / ({RRF_K} + k.rank), 0) AS score
    FROM vec v
    FULL OUTER JOIN kw k ON v.id = k.id
)
SELECT c.page_id, c.chunk_index, c.content, c.metadata,
       p.title, p.url, f.score
FROM fused f
JOIN web_chunks c ON c.id = f.id
JOIN web_pages p ON p.id = c.page_id
ORDER BY f.score DESC
LIMIT $6
"""


@lru_cache(maxsize=8)
def _web_vector_sql(dims: int) -> str:
    expr = vector_expr(dims, "c.embedding")
    return f"""
SELECT c.page_id, c.chunk_index, c.content, c.metadata,
       p.title, p.url,
       1 - ({expr} <=> $1::vector({dims})) AS score
FROM web_chunks c
JOIN web_pages p ON p.id = c.page_id
WHERE c.org_id = $2 AND ($5::bigint IS NULL OR c.project_id = $5)
  AND ($3::bigint[] IS NULL OR c.page_id = ANY($3))
ORDER BY {expr} <=> $1::vector({dims})
LIMIT $4
"""
```

For `dims = 768` the ordering line renders as
`ORDER BY (c.embedding::vector(768)) <=> $1::vector(768)`.

- [ ] **Step 6: Replace the kb SQL constants with builders**

Delete `_KB_HYBRID_SQL` and `_KB_VECTOR_SQL` and put in their place:

```python
#   $1 embedding  $2 org_id  $3 candidate pool  $4 query text
#   $5 document_ids (bigint[] or NULL)  $6 limit  $7 project (bigint or NULL)
@lru_cache(maxsize=8)
def _kb_hybrid_sql(dims: int) -> str:
    expr = vector_expr(dims, "c.embedding")
    return f"""
WITH tsq AS (
    SELECT websearch_to_tsquery('{TSQUERY_CONFIG}', $4) AS query
),
vec AS (
    SELECT c.id, ROW_NUMBER() OVER (ORDER BY {expr} <=> $1::vector({dims})) AS rank
    FROM kb_chunks c
    WHERE c.org_id = $2 AND ($7::bigint IS NULL OR c.project_id = $7)
      AND ($5::bigint[] IS NULL OR c.document_id = ANY($5))
    ORDER BY {expr} <=> $1::vector({dims})
    LIMIT $3
),
kw AS (
    SELECT c.id, ROW_NUMBER() OVER (ORDER BY ts_rank_cd(c.content_tsv, tsq.query) DESC) AS rank
    FROM kb_chunks c, tsq
    WHERE c.org_id = $2 AND ($7::bigint IS NULL OR c.project_id = $7)
      AND ($5::bigint[] IS NULL OR c.document_id = ANY($5))
      AND c.content_tsv @@ tsq.query
    ORDER BY ts_rank_cd(c.content_tsv, tsq.query) DESC
    LIMIT $3
),
fused AS (
    SELECT COALESCE(v.id, k.id) AS id,
           COALESCE(1.0 / ({RRF_K} + v.rank), 0)
         + COALESCE(1.0 / ({RRF_K} + k.rank), 0) AS score
    FROM vec v
    FULL OUTER JOIN kw k ON v.id = k.id
)
SELECT c.document_id, c.chunk_index, c.content, c.metadata,
       d.title, d.filename, d.extension, f.score
FROM fused f
JOIN kb_chunks c ON c.id = f.id
JOIN kb_documents d ON d.id = c.document_id
ORDER BY f.score DESC
LIMIT $6
"""


@lru_cache(maxsize=8)
def _kb_vector_sql(dims: int) -> str:
    expr = vector_expr(dims, "c.embedding")
    return f"""
SELECT c.document_id, c.chunk_index, c.content, c.metadata,
       d.title, d.filename, d.extension,
       1 - ({expr} <=> $1::vector({dims})) AS score
FROM kb_chunks c
JOIN kb_documents d ON d.id = c.document_id
WHERE c.org_id = $2 AND ($5::bigint IS NULL OR c.project_id = $5)
  AND ($3::bigint[] IS NULL OR c.document_id = ANY($3))
ORDER BY {expr} <=> $1::vector({dims})
LIMIT $4
"""
```

- [ ] **Step 7: Route the three coroutines through the builders and set `ef_search`**

In `search_repo_chunks`, replace the block from `embedding_str = ...` down to the
end of the `async with pool.acquire() as conn:` body with:

```python
    dims = int((await get_org_settings(org_id)).embeddings_dims)
    embedding_str = _vector_to_pg(await embed_text(query, org_id))
    pool = await get_pool()
    hybrid = hybrid_enabled()

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(f"SET LOCAL hnsw.ef_search = {HNSW_EF_SEARCH}")
            if hybrid:
                rows = await conn.fetch(
                    _repo_hybrid_sql(dims), embedding_str, org_id, CANDIDATE_POOL,
                    query, repos, limit, project_id,
                )
            else:
                rows = await conn.fetch(
                    _repo_vector_sql(dims), embedding_str, org_id, repos, limit, project_id
                )
```

In `search_web_chunks`, the same shape:

```python
    dims = int((await get_org_settings(org_id)).embeddings_dims)
    embedding_str = _vector_to_pg(await embed_text(query, org_id))
    pool = await get_pool()
    hybrid = hybrid_enabled()

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(f"SET LOCAL hnsw.ef_search = {HNSW_EF_SEARCH}")
            if hybrid:
                rows = await conn.fetch(
                    _web_hybrid_sql(dims), embedding_str, org_id, CANDIDATE_POOL,
                    query, page_ids, limit, project_id,
                )
            else:
                rows = await conn.fetch(
                    _web_vector_sql(dims), embedding_str, org_id, page_ids, limit, project_id
                )
```

In `search_kb_chunks`:

```python
    dims = int((await get_org_settings(org_id)).embeddings_dims)
    embedding_str = _vector_to_pg(await embed_text(query, org_id))
    pool = await get_pool()
    hybrid = hybrid_enabled()

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(f"SET LOCAL hnsw.ef_search = {HNSW_EF_SEARCH}")
            if hybrid:
                rows = await conn.fetch(
                    _kb_hybrid_sql(dims), embedding_str, org_id, CANDIDATE_POOL,
                    query, document_ids, limit, project_id,
                )
            else:
                rows = await conn.fetch(
                    _kb_vector_sql(dims), embedding_str, org_id, document_ids, limit, project_id
                )
```

Leave the `if project_id is None: raise ValueError(...)` guard as the first
statement of each coroutine (an existing test asserts it fires before any pool
access), and leave `search_repo_symbols` untouched — it does no vector work.

- [ ] **Step 8: Update the three existing tests that read the deleted constants**

`services/server/tests/test_repo_project_scope.py`, line 7 — replace:

```python
    for sql in (search._REPO_HYBRID_SQL, search._REPO_VECTOR_SQL, search._SYMBOL_SEARCH_SQL):
```

with:

```python
    for sql in (search._repo_hybrid_sql(1536), search._repo_vector_sql(1536), search._SYMBOL_SEARCH_SQL):
```

`services/server/tests/test_kb_project_scope.py`, lines 14-15 — replace:

```python
    assert "project_id" in search._KB_HYBRID_SQL
    assert "project_id" in search._KB_VECTOR_SQL
```

with:

```python
    assert "project_id" in search._kb_hybrid_sql(1536)
    assert "project_id" in search._kb_vector_sql(1536)
```

`services/server/tests/test_web_project_scope.py`, lines 22-23 — replace:

```python
    assert "project_id" in search._WEB_HYBRID_SQL
    assert "project_id" in search._WEB_VECTOR_SQL
```

with:

```python
    assert "project_id" in search._web_hybrid_sql(1536)
    assert "project_id" in search._web_vector_sql(1536)
```

- [ ] **Step 9: Run the tests to verify they pass**

```
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_search_hnsw_sql.py tests/test_repo_project_scope.py tests/test_kb_project_scope.py tests/test_web_project_scope.py tests/test_search_requires_project.py
```

Expected: all pass (6 new + the 4 existing files).

- [ ] **Step 10: Commit**

```bash
git add services/server/src/search.py services/server/tests
git commit -m "order vector searches by the indexed expression"
```

---

### Task 4: trigger the index maintenance at boot, after re-embed, and on settings save

**Files:**
- Modify: `services/server/src/main.py` (inside `main()`, after `await ensure_tenant_storage()`)
- Modify: `services/server/src/reembed.py`
- Modify: `services/server/src/api/routes/settings.py`
- Modify: `services/server/tests/test_reembed_job.py` (keep it hermetic)
- Modify: `services/server/tests/test_settings_org_scope.py` (keep it hermetic)
- Test: `services/server/tests/test_vector_index_triggers.py`

**Interfaces:**
- Consumes (already in `src/vector_index.py`, do not redefine):
  - `async def ensure_org_indexes(org_id: int, dims: int) -> list[str]` — idempotent,
    never raises.
  - `async def ensure_all_indexes() -> None` — iterates organizations, never raises.
- Produces: nothing new for later tasks.

**Existing code you are changing:**
- `src/main.py` — inside `async def main()`:
  `await init_db()`, `await ensure_runtime_state()`, `await ensure_tenant_storage()`,
  then the stale-processing resets, then `asyncio.create_task(initial_index())`.
  `import asyncio` is already at the top of the module.
- `src/reembed.py` — `async def reembed_org(org_id: int, job_id: str) -> dict` loops
  `for table in _TABLES:` filling `counts[table] = done`, then calls
  `await _set_job_status(job_id, "completed", result=counts)` inside a `try`.
  It currently imports `from .db import get_pool` and
  `from .indexer.embedder import embed_batch`.
- `src/api/routes/settings.py` — `update_runtime_settings` computes
  `overrides_changed`, `embeddings_dims_changed`, `requires_reembed`, and in the
  `if overrides_changed:` branch calls `await persist_org_settings_overrides(...)`,
  `reset_embedder_clients()`, `reset_memory_client()`. The module already holds
  `_background_tasks: set = set()`, and `start_reembed` does a function-local
  `import asyncio`.

- [ ] **Step 1: Write the failing test**

Create `services/server/tests/test_vector_index_triggers.py`:

```python
"""Boot, re-embed and settings-save all refresh the HNSW indexes."""
import asyncio
import inspect

from src import main as main_module
from src import reembed
from src.api.deps import ActiveOrg
from src.api.routes import settings as settings_routes
from src.config import ForgeConfig
from src.org_settings import OrgSettings


def test_boot_schedules_index_maintenance_after_tenant_storage():
    src = inspect.getsource(main_module.main)
    assert "ensure_all_indexes" in src
    assert src.index("ensure_tenant_storage()") < src.index("ensure_all_indexes()")
    # It must not block boot: the ensure runs as a background task.
    assert "asyncio.create_task(ensure_all_indexes())" in src


def test_reembed_ensures_the_indexes_once_with_the_org_dimension(monkeypatch):
    calls = []

    async def fake_iter_chunks(table, org_id, batch_size):
        yield [(10, "hello")]

    async def fake_embed_batch(texts, org_id):
        return [[0.1] * 4 for _ in texts]

    async def fake_update(table, pairs, org_id):
        pass

    async def fake_job_update(job_id, status, result=None, error=None):
        pass

    async def fake_org_settings(org_id):
        return OrgSettings(embeddings_dims=3072)

    async def fake_ensure(org_id, dims):
        calls.append((org_id, dims))
        return []

    monkeypatch.setattr(reembed, "_iter_chunks", fake_iter_chunks)
    monkeypatch.setattr(reembed, "embed_batch", fake_embed_batch)
    monkeypatch.setattr(reembed, "_update_embeddings", fake_update)
    monkeypatch.setattr(reembed, "_set_job_status", fake_job_update)
    monkeypatch.setattr(reembed, "get_org_settings", fake_org_settings)
    monkeypatch.setattr(reembed, "ensure_org_indexes", fake_ensure)

    asyncio.run(reembed.reembed_org(5, "job-1"))

    assert calls == [(5, 3072)]


def _patch_settings(monkeypatch, current, calls):
    async def mock_get_org_settings(org_id):
        return current

    async def mock_persist_overrides(org_id, overrides):
        for k, v in overrides.items():
            if hasattr(current, k):
                setattr(current, k, v)

    async def mock_persist_org_config(org_id, config):
        pass

    async def mock_get_org_config(org_id):
        return ForgeConfig()

    async def mock_sync_repos_config(org_id):
        pass

    async def fake_ensure(org_id, dims):
        calls.append((org_id, dims))
        return []

    monkeypatch.setattr(settings_routes, "get_org_settings", mock_get_org_settings)
    monkeypatch.setattr(settings_routes, "persist_org_settings_overrides", mock_persist_overrides)
    monkeypatch.setattr(settings_routes, "persist_org_config", mock_persist_org_config)
    monkeypatch.setattr(settings_routes, "get_org_config", mock_get_org_config)
    monkeypatch.setattr(settings_routes, "sync_repos_config", mock_sync_repos_config)
    monkeypatch.setattr(settings_routes, "reset_embedder_clients", lambda: None)
    monkeypatch.setattr(settings_routes, "reset_memory_client", lambda: None)
    monkeypatch.setattr(settings_routes, "ensure_org_indexes", fake_ensure)


def _org():
    return ActiveOrg(org_id=1, role="admin", namespace="ns", name="Org")


def test_settings_save_without_a_dimension_change_ensures_the_indexes(monkeypatch):
    calls = []
    current = OrgSettings(embeddings_dims=1536)
    _patch_settings(monkeypatch, current, calls)

    req = settings_routes.SettingsUpdateRequest(
        forge_config={}, settings_overrides={"embeddings_model": "text-embedding-3-large"}
    )

    async def run():
        await settings_routes.update_runtime_settings(req=req, org=_org())
        await asyncio.sleep(0)  # let the background task run

    asyncio.run(run())
    assert calls == [(1, 1536)]


def test_settings_save_with_a_dimension_change_leaves_it_to_the_reembed(monkeypatch):
    calls = []
    current = OrgSettings(embeddings_dims=1536)
    _patch_settings(monkeypatch, current, calls)

    req = settings_routes.SettingsUpdateRequest(
        forge_config={}, settings_overrides={"embeddings_dims": 1024}
    )

    async def run():
        out = await settings_routes.update_runtime_settings(req=req, org=_org())
        await asyncio.sleep(0)
        assert out["requires_vector_reset"] is True

    asyncio.run(run())
    assert calls == []
```

- [ ] **Step 2: Run the test to verify it fails**

```
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_vector_index_triggers.py
```

Expected: FAIL — `AttributeError: module 'src.reembed' has no attribute 'ensure_org_indexes'`
and the boot assertion fails on the missing `ensure_all_indexes`.

- [ ] **Step 3: Schedule the ensure at boot (`src/main.py`)**

Replace:

```python
    await ensure_tenant_storage()
```

with:

```python
    await ensure_tenant_storage()

    # Rebuilt in the background: a large HNSW build must not delay boot.
    from .vector_index import ensure_all_indexes

    asyncio.create_task(ensure_all_indexes())
```

- [ ] **Step 4: Ensure the indexes at the end of `reembed_org` (`src/reembed.py`)**

Add these imports under `from .indexer.embedder import embed_batch`:

```python
from .org_settings import get_org_settings
from .vector_index import ensure_org_indexes
```

Then, inside `reembed_org`, replace:

```python
            counts[table] = done
        await _set_job_status(job_id, "completed", result=counts)
```

with:

```python
            counts[table] = done
        dims = int((await get_org_settings(org_id)).embeddings_dims)
        await ensure_org_indexes(org_id, dims)
        await _set_job_status(job_id, "completed", result=counts)
```

- [ ] **Step 5: Ensure the indexes on a settings save (`src/api/routes/settings.py`)**

Add `import asyncio` as the first import line of the module (directly above
`from typing import Any`), and delete the now-redundant function-local
`import asyncio` inside `start_reembed`.

Add to the import block:

```python
from ...vector_index import ensure_org_indexes
```

Add below `_background_tasks: set = set()`:

```python
def _schedule(coro) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
```

Then, in `update_runtime_settings`, replace:

```python
        await persist_org_settings_overrides(org.org_id, next_overrides)
        reset_embedder_clients()
        reset_memory_client()
```

with:

```python
        await persist_org_settings_overrides(org.org_id, next_overrides)
        reset_embedder_clients()
        reset_memory_client()
        # A changed dimension is handled by the re-embed, which ensures too.
        if not embeddings_dims_changed:
            _schedule(
                ensure_org_indexes(org.org_id, int(next_overrides["embeddings_dims"]))
            )
```

Finally, rewrite the tail of `start_reembed` to use the helper:

```python
    task = asyncio.create_task(reembed_org(org.org_id, str(job_id)))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
```

becomes:

```python
    _schedule(reembed_org(org.org_id, str(job_id)))
```

- [ ] **Step 6: Keep the two existing test files hermetic**

Both files drive code paths that now reach `ensure_org_indexes`. Left unpatched
they would call the real coroutine, which calls `get_pool()` and would try to
open a real connection — forbidden by the Global Constraints. Patch it in both.

In `services/server/tests/test_reembed_job.py`, both tests build their fakes with
`monkeypatch.setattr(reembed, ...)`. In **each** of the two test functions, add
these two fakes and the two extra `setattr` lines next to the existing ones:

```python
    async def fake_org_settings(org_id):
        from src.org_settings import OrgSettings

        return OrgSettings(embeddings_dims=1536)

    async def fake_ensure(org_id, dims):
        return []

    monkeypatch.setattr(reembed, "get_org_settings", fake_org_settings)
    monkeypatch.setattr(reembed, "ensure_org_indexes", fake_ensure)
```

In `services/server/tests/test_settings_org_scope.py`, inside the existing
`_patch(monkeypatch, current=None)` helper, add before `return current`:

```python
    async def mock_ensure_org_indexes(org_id: int, dims: int):
        return []

    monkeypatch.setattr(settings_routes, "ensure_org_indexes", mock_ensure_org_indexes)
```

- [ ] **Step 7: Run the tests to verify they pass**

```
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_vector_index_triggers.py tests/test_reembed_job.py tests/test_settings_org_scope.py
```

Expected: all pass (4 new + 2 + 7 existing). No `RuntimeWarning: coroutine ...
was never awaited` and no "Task was destroyed but it is pending" output.

- [ ] **Step 8: Commit**

```bash
git add services/server/src/main.py services/server/src/reembed.py services/server/src/api/routes/settings.py services/server/tests
git commit -m "ensure HNSW indexes at boot, reembed and save"
```

---

### Task 5: README and full-suite verification

**Files:**
- Modify: `README.md` (Architecture section, ~line 31; "Upgrading an existing installation", line 62)
- Test: the whole server suite

**Interfaces:**
- Consumes: the finished behaviour of Tasks 1–4. Nothing new is produced.

**Scope note:** this feature adds no MCP tool, no permission and no env var, so
the "MCP tools", "Security / MCP permissions" and env-var sections of the README
are not touched. Only the two places that describe vector search change.

- [ ] **Step 1: Update the Architecture section**

In `README.md`, find the paragraph that begins
"Indexing uses tree-sitter (Python, JS/TS, Go, Java), with scheduled re-indexing
via APScheduler." and append a new paragraph immediately after it:

```markdown
Vector search is served by one HNSW index per organization and embedding dimension, built on the typed cast `embedding::vector(N)` and partial on `org_id`, so organizations on different embedding models stay independently indexed. The indexes are created and pruned in the background at startup, at the end of a re-embed, and when embedding settings are saved.
```

- [ ] **Step 2: Replace the "sequential scan" upgrade caveat**

In `README.md`, under "### Upgrading an existing installation", replace this
bullet:

```markdown
- embedding columns lose their fixed dimension so each organization can pick its own model. The ivfflat indexes are dropped in the process and are not recreated yet, so vector search runs as a sequential scan on large tables (tracked as a follow-up).
```

with:

```markdown
- embedding columns lose their fixed dimension so each organization can pick its own model. The old ivfflat indexes are dropped in the process and replaced by per-organization HNSW indexes, which the server builds in the background on the first startup after the upgrade; vector search stays indexed while a build is in flight, it is simply slower until it completes.
```

- [ ] **Step 3: Check no stale wording is left**

Run from the repository root:

```bash
grep -n "ivfflat\|sequential scan" README.md
```

Expected: exactly one hit, on the rewritten bullet ("The old ivfflat indexes are
dropped..."), and no "sequential scan" hit at all.

- [ ] **Step 4: Run the full server suite**

Run from `services/server`:

```
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider
```

Expected: every test passes — at least `530 passed` (the pre-plan baseline) plus
the tests added by Tasks 1–4, with zero failures and zero errors. Paste the
summary line as evidence. Do not claim completion without it.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "document per-organization HNSW vector indexes"
```

---

## Self-review

**Spec coverage**

| Spec requirement | Task |
|---|---|
| Index shape: `CREATE INDEX CONCURRENTLY IF NOT EXISTS {table}_emb_hnsw_org{org}_d{dims}`, `USING hnsw ((embedding::vector(N)) vector_cosine_ops)`, `WITH (m = 16, ef_construction = 64)`, `WHERE org_id = N` | 1 |
| `vector_expr(dims) -> str` in a new `src/vector_index.py`, one implementation for every query | 1, 3 |
| `index_name(table, org_id, dims) -> str` | 1 |
| `ensure_org_indexes(org_id, dims) -> list[str]`, autocommit, `SET maintenance_work_mem = '256MB'` first, drops other-dimension indexes of the same org via `pg_indexes`, returns created names | 2 |
| `ensure_all_indexes()` iterating organizations | 2 |
| Build failures logged with the index name, never failing the caller | 2 |
| Query shape `ORDER BY (embedding::vector($dims)) <=> $1::vector($dims)` with `WHERE org_id = $2` already present, in repo hybrid/vector and kb/web search | 3 |
| `$dims` interpolated as an integer from `org_settings` → `embeddings_dims`, never from user input | 3 |
| `SET LOCAL hnsw.ef_search = 100` in the search transaction; `HNSW_EF_SEARCH = 100` in `src/vector_index.py` | 1 (constant), 3 (usage) |
| Trigger at startup after `ensure_tenant_storage`, as a background task | 4 |
| Trigger at the end of `reembed_org` | 4 |
| Trigger on settings save only when the dimension is unchanged | 4 |
| Tests: `index_name` and DDL text | 1 |
| Tests: three CREATEs + stale drop from a fake `pg_indexes` | 2 |
| Tests: search SQL carries the typed cast on both sides of `<=>` and sets `ef_search` | 3 |
| Tests: `reembed_org` calls `ensure_org_indexes` once at the end | 4 |
| README: HNSW sentence in Architecture, upgrade note replaces the sequential-scan caveat | 5 |
| No schema migration (roadmap slot: none) | Global Constraints |

**Placeholder scan:** no TBD / TODO / "similar to Task N" / "add error handling"
anywhere; every code step carries the literal code, every SQL step the literal
SQL, every command its expected output.

**Type consistency:** `index_name`, `vector_expr`, `create_index_sql`,
`drop_index_sql`, `ensure_org_indexes`, `ensure_all_indexes`, `HNSW_TABLES`,
`HNSW_M`, `HNSW_EF_CONSTRUCTION`, `HNSW_EF_SEARCH`, `MAINTENANCE_WORK_MEM` are
spelled identically in Tasks 1, 2, 3 and 4. The six SQL builders
(`_repo_hybrid_sql`, `_repo_vector_sql`, `_web_hybrid_sql`, `_web_vector_sql`,
`_kb_hybrid_sql`, `_kb_vector_sql`) are spelled identically in the Task 3
implementation, the Task 3 tests, and the three existing tests Task 3 updates.
