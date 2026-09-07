# Personal Memory and Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give memory a per-user personal scope alongside the project scope, track which memories are actually used, and run a daily consolidation job that de-duplicates and fades stale memories.

**Architecture:** A new `memory_stats` table (migration `0005_memory_stats`) records one row per Mem0 memory id with a normalised content hash, hit counters and a `stale_at` marker. `src/mcp/context.py` learns to resolve a personal namespace (`f"{project_namespace}::u{user_id}"`) from the MCP principal, and a single scope resolver feeds the MCP tools, the REST routes and (later) `context_pack`. `src/memory_consolidation.py` holds the pure consolidation logic (exact hash dedup, token-Jaccard near-dedup, decay) and is driven both by a daily scheduler job and by an admin-only REST endpoint surfaced as a button on the Memory page.

**Tech Stack:** Python 3.11 (dev 3.14), FastMCP, FastAPI, asyncpg (raw SQL), Mem0 + pgvector, APScheduler, React 18 + TypeScript + Vite.

**Spec:** `docs/superpowers/specs/2026-09-07-memory-personal-consolidation-design.md`

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

### Test helpers to reuse

- The fake-pool pattern in this repo is a `FakeConn` (records
  `(query, args)` in `self.executed`, answers `execute` / `fetch` / `fetchval`),
  a `FakeAcquire` async context manager and a `FakePool` with
  `acquire()` — see `services/server/tests/test_role_permissions.py` and
  `services/server/tests/test_oauth_bridge_reaper.py`. Every task below spells
  its fakes out in full so no task depends on another's file. The migrations
  feature also ships a shared `services/server/tests/fake_db.py` exporting
  `FakeConn` (with `.sql`, `.executed`, scripted `fetchval_results`,
  `fetch_rows`, and a counting `transaction()`) and `FakePool`; if that module
  is present you may import from it instead of pasting the fakes, but then
  adapt the assertions to its `fetchval_results` queue.
- Patching the pool is always `monkeypatch.setattr(<module>, "get_pool", fake_pool)`
  where `async def fake_pool(): return FakePool(conn)` — this works because
  `src/memory_stats.py` and `src/mcp/context.py` import `get_pool` at module
  level.
- The fake Mem0 client pattern is in
  `services/server/tests/test_memory_project_scope.py` and
  `services/server/tests/test_memory_routes_org.py`: a plain class with
  synchronous `add` / `search` / `get_all` / `get` / `delete`, installed with
  `monkeypatch.setattr(memory, "_get_memory", _fake_get_memory(fake))` where
  `_fake_get_memory` returns `async def _get(org_id): return fake`.
- MCP tools are `FunctionTool` objects; the existing tests unwrap them with
  `def _underlying(tool): return getattr(tool, "fn", tool)`. Permissions are
  granted in tests with
  `from src.mcp.permissions import set_current_permissions; set_current_permissions(frozenset({"*"}))`
  and reset to `None` afterwards.

### Feature-local constraints

- This feature adds **no new Python dependency** and **no new MCP permission**.
  The memory tools keep `context-read` / `context-write`.
- `mcp_api_keys.created_by` **already exists** (`src/db.py`, `admin_users(id)`
  FK) and is already populated by `create_mcp_api_key` in
  `src/api/security.py`. Do not add it in the migration.
- Baseline before this feature: `530 passed` for the server suite. Any test you
  change must be changed deliberately; the plan says exactly which ones.

---

## File Structure

| File | Responsibility |
|---|---|
| `services/server/src/migrations/versions/0005_memory_stats.py` (create) | DDL for `memory_stats` + its index |
| `services/server/src/memory_stats.py` (create) | Text normalisation, content hash, and every `memory_stats` SQL statement |
| `services/server/src/mcp/context.py` (modify) | Personal user id, personal namespace, scope → namespaces resolver |
| `services/server/src/mcp/memory.py` (modify) | Tool signatures with `scope` / `include_stale`, plus the reusable `search_memories` / `list_memories` / `search_in_namespaces` / `list_in_namespaces` |
| `services/server/src/api/routes/memory.py` (modify) | REST scope + stale params, restore, consolidate endpoint |
| `services/server/src/memory_consolidation.py` (create) | Dedup, near-dedup, decay, backfill; `consolidate_namespace` / `consolidate_project` / `consolidate_all` |
| `services/server/src/config.py` (modify) | `memory_decay_days`, `memory_decay_idle_days` |
| `services/server/src/scheduler.py` (modify) | Daily `memory_consolidation` job |
| `services/ui/src/lib/api.ts` (modify) | Scope-aware memory client |
| `services/ui/src/pages/Memory.tsx` (modify) | Scope selector, stale badge, restore, consolidate button |
| `README.md`, `.env.example`, `docker-compose.yml` (modify) | Docs and configuration |

---

### Task 1: Migration `0005_memory_stats` and the stats helpers

**Files:**
- Create: `services/server/src/migrations/versions/0005_memory_stats.py`
- Create: `services/server/src/memory_stats.py`
- Test: `services/server/tests/test_memory_stats.py`

**Interfaces:**
- Consumes: the migration runner from feature 1
  (`docs/superpowers/specs/2026-09-07-versioned-migrations-design.md`). Every
  version module in `src/migrations/versions/` exposes
  `VERSION: int`, `NAME: str`, `TRANSACTIONAL: bool = True` and
  `async def upgrade(conn) -> None`. Transactional modules run inside
  `async with conn.transaction()`; the runner records the version afterwards.
  It also consumes `src.db.get_pool()` (asyncpg pool; `pool.acquire()` is an
  async context manager yielding a connection with
  `execute/fetch/fetchrow/fetchval`).
- Produces, all in `src/memory_stats.py`:
  - `def normalize_text(text: str) -> str`
  - `def content_hash(text: str) -> str`
  - `async def record_memories(namespace: str, org_id: int, items: list[tuple[str, str]]) -> None`
  - `async def bump_hits(memory_ids: list[str]) -> None`
  - `async def stale_ids(memory_ids: list[str]) -> set[str]`
  - `async def delete_stats(memory_ids: list[str]) -> None`
  - `async def restore_memory(memory_id: str) -> None`
  - `async def fetch_namespace_stats(namespace: str) -> list[dict]`
  - `async def insert_stats_rows(rows: list[dict]) -> None`
  - `async def merge_hits(keeper_id: str, extra_hits: int) -> None`
  - `async def mark_stale(namespace: str, decay_days: int, idle_days: int) -> int`
  - `async def list_tracked_namespaces() -> list[tuple[int, str]]`
  - `async def list_project_namespaces(project_namespace: str) -> list[str]`

- [ ] **Step 1: Write the failing test**

Create `services/server/tests/test_memory_stats.py`:

```python
"""memory_stats: schema module + the SQL helpers that track memory usage.

No live database: a fake asyncpg pool records the statements issued.
"""
from __future__ import annotations

import asyncio

import pytest

from src import memory_stats


class FakeConn:
    def __init__(self, fetch_rows=None, fetchval_result=None):
        self.fetch_rows = fetch_rows if fetch_rows is not None else []
        self.fetchval_result = fetchval_result
        self.executed = []

    async def execute(self, query, *args):
        self.executed.append((query, args))
        return "UPDATE 1"

    async def fetch(self, query, *args):
        self.executed.append((query, args))
        return self.fetch_rows

    async def fetchval(self, query, *args):
        self.executed.append((query, args))
        return self.fetchval_result

    async def executemany(self, query, args_list):
        self.executed.append((query, tuple(args_list)))


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

    monkeypatch.setattr(memory_stats, "get_pool", fake_pool)


# --- migration module ------------------------------------------------------

def _migration():
    """The version module, loaded the way the runner loads it."""
    from src.migrations.runner import discover_versions

    found = [m for m in discover_versions() if getattr(m, "VERSION", None) == 5]
    assert found, "0005_memory_stats was not discovered by the migration runner"
    return found[0]


def test_migration_declares_version_five():
    mod = _migration()
    assert mod.VERSION == 5
    assert mod.NAME == "memory_stats"
    assert mod.TRANSACTIONAL is True


def test_migration_creates_table_and_index():
    conn = FakeConn()
    asyncio.run(_migration().upgrade(conn))
    sql = "\n".join(q for q, _ in conn.executed)
    assert "CREATE TABLE IF NOT EXISTS memory_stats" in sql
    assert "memory_id   TEXT PRIMARY KEY" in sql
    assert "content_hash TEXT NOT NULL" in sql
    assert "memory_stats_ns_idx" in sql


# --- normalisation and hashing --------------------------------------------

def test_normalize_text_lowercases_strips_punctuation_collapses_space():
    assert memory_stats.normalize_text("  The  Billing, Service!  ") == "the billing service"


def test_content_hash_is_stable_across_formatting_noise():
    assert memory_stats.content_hash("Use RRF!") == memory_stats.content_hash("  use rrf  ")


def test_content_hash_differs_for_different_text():
    assert memory_stats.content_hash("alpha") != memory_stats.content_hash("beta")


# --- SQL helpers -----------------------------------------------------------

def test_record_memories_inserts_one_row_per_item(monkeypatch):
    conn = FakeConn()
    _patch_pool(monkeypatch, conn)

    asyncio.run(memory_stats.record_memories("ns", 7, [("m1", "Alpha!"), ("m2", "Beta")]))

    query, args = conn.executed[0]
    assert "INSERT INTO memory_stats" in query
    assert "ON CONFLICT (memory_id) DO NOTHING" in query
    assert args == (
        ["m1", "m2"],
        ["ns", "ns"],
        [7, 7],
        [memory_stats.content_hash("Alpha!"), memory_stats.content_hash("Beta")],
    )


def test_record_memories_with_no_items_issues_no_sql(monkeypatch):
    conn = FakeConn()
    _patch_pool(monkeypatch, conn)
    asyncio.run(memory_stats.record_memories("ns", 7, []))
    assert conn.executed == []


def test_bump_hits_updates_every_returned_id_in_one_statement(monkeypatch):
    conn = FakeConn()
    _patch_pool(monkeypatch, conn)

    asyncio.run(memory_stats.bump_hits(["a", "b"]))

    query, args = conn.executed[0]
    assert "UPDATE memory_stats" in query
    assert "hits = hits + 1" in query
    assert "memory_id = ANY($1)" in query
    assert args == (["a", "b"],)


def test_bump_hits_with_no_ids_issues_no_sql(monkeypatch):
    conn = FakeConn()
    _patch_pool(monkeypatch, conn)
    asyncio.run(memory_stats.bump_hits([]))
    assert conn.executed == []


def test_stale_ids_returns_only_the_marked_ones(monkeypatch):
    conn = FakeConn(fetch_rows=[{"memory_id": "b"}])
    _patch_pool(monkeypatch, conn)

    result = asyncio.run(memory_stats.stale_ids(["a", "b"]))

    assert result == {"b"}
    query, args = conn.executed[0]
    assert "stale_at IS NOT NULL" in query
    assert args == (["a", "b"],)


def test_helpers_never_raise_when_the_database_fails(monkeypatch):
    """Usage tracking is best-effort: a DB outage must not break a memory tool."""
    async def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(memory_stats, "get_pool", boom)

    asyncio.run(memory_stats.bump_hits(["a"]))
    asyncio.run(memory_stats.record_memories("ns", 1, [("a", "x")]))
    assert asyncio.run(memory_stats.stale_ids(["a"])) == set()


def test_mark_stale_filters_on_age_and_idleness(monkeypatch):
    conn = FakeConn(fetchval_result=3)
    _patch_pool(monkeypatch, conn)

    marked = asyncio.run(memory_stats.mark_stale("ns", 180, 90))

    assert marked == 3
    query, args = conn.executed[0]
    assert "SET stale_at = NOW()" in query
    assert "stale_at IS NULL" in query
    assert "last_hit_at IS NULL" in query
    assert args == ("ns", 180, 90)


def test_list_tracked_namespaces_returns_org_namespace_pairs(monkeypatch):
    conn = FakeConn(fetch_rows=[{"org_id": 1, "namespace": "ns"},
                                {"org_id": 1, "namespace": "ns::u4"}])
    _patch_pool(monkeypatch, conn)

    assert asyncio.run(memory_stats.list_tracked_namespaces()) == [(1, "ns"), (1, "ns::u4")]


def test_list_project_namespaces_matches_the_project_and_its_personal_ones(monkeypatch):
    conn = FakeConn(fetch_rows=[{"namespace": "ns"}, {"namespace": "ns::u4"}])
    _patch_pool(monkeypatch, conn)

    assert asyncio.run(memory_stats.list_project_namespaces("ns")) == ["ns", "ns::u4"]
    query, args = conn.executed[0]
    assert "LIKE" in query
    assert args == ("ns",)
```

- [ ] **Step 2: Run test to verify it fails**

Run from `services/server`:
`.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_memory_stats.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.memory_stats'`.

- [ ] **Step 3: Write the migration module**

Create `services/server/src/migrations/versions/0005_memory_stats.py`:

```python
"""Usage counters for Mem0 memories. New schema changes go in a new version."""
from __future__ import annotations

VERSION = 5
NAME = "memory_stats"
TRANSACTIONAL = True


async def upgrade(conn) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_stats (
            memory_id   TEXT PRIMARY KEY,
            namespace   TEXT NOT NULL,
            org_id      BIGINT NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_hit_at TIMESTAMPTZ,
            hits        INT NOT NULL DEFAULT 0,
            stale_at    TIMESTAMPTZ,
            content_hash TEXT NOT NULL
        )
        """
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS memory_stats_ns_idx ON memory_stats (namespace)"
    )
```

- [ ] **Step 4: Write the stats helpers**

Create `services/server/src/memory_stats.py`:

```python
"""Usage tracking for Mem0 memories (the memory_stats table).

Best-effort by design: every helper swallows and logs database errors so a
memory tool never fails because its counters could not be written.
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Optional

from .db import get_pool

logger = logging.getLogger(__name__)

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Lowercase, replace punctuation with a space, collapse whitespace."""
    return _WS.sub(" ", _PUNCT.sub(" ", (text or "").lower())).strip()


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


async def record_memories(namespace: str, org_id: int, items: list[tuple[str, str]]) -> None:
    """Insert one row per (memory_id, content) pair created in this namespace."""
    if not items:
        return
    ids = [str(mid) for mid, _ in items]
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO memory_stats (memory_id, namespace, org_id, content_hash)
                SELECT * FROM unnest($1::text[], $2::text[], $3::bigint[], $4::text[])
                ON CONFLICT (memory_id) DO NOTHING
                """,
                ids,
                [namespace] * len(ids),
                [int(org_id)] * len(ids),
                [content_hash(text) for _, text in items],
            )
    except Exception as e:
        logger.warning("memory_stats.record_memories failed: %s", e)


async def bump_hits(memory_ids: list[str]) -> None:
    if not memory_ids:
        return
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE memory_stats SET hits = hits + 1, last_hit_at = NOW() "
                "WHERE memory_id = ANY($1)",
                list(memory_ids),
            )
    except Exception as e:
        logger.warning("memory_stats.bump_hits failed: %s", e)


async def stale_ids(memory_ids: list[str]) -> set[str]:
    if not memory_ids:
        return set()
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT memory_id FROM memory_stats "
                "WHERE memory_id = ANY($1) AND stale_at IS NOT NULL",
                list(memory_ids),
            )
        return {r["memory_id"] for r in rows}
    except Exception as e:
        logger.warning("memory_stats.stale_ids failed: %s", e)
        return set()


async def delete_stats(memory_ids: list[str]) -> None:
    if not memory_ids:
        return
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM memory_stats WHERE memory_id = ANY($1)", list(memory_ids)
            )
    except Exception as e:
        logger.warning("memory_stats.delete_stats failed: %s", e)


async def restore_memory(memory_id: str) -> None:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE memory_stats SET stale_at = NULL WHERE memory_id = $1", memory_id
            )
    except Exception as e:
        logger.warning("memory_stats.restore_memory failed: %s", e)


async def fetch_namespace_stats(namespace: str) -> list[dict]:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT memory_id, hits, created_at, content_hash, stale_at "
                "FROM memory_stats WHERE namespace = $1",
                namespace,
            )
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("memory_stats.fetch_namespace_stats failed: %s", e)
        return []


async def insert_stats_rows(rows: list[dict]) -> None:
    """Backfill rows for memories created before this feature.

    Each row: memory_id, namespace, org_id, created_at (or None), content_hash.
    """
    if not rows:
        return
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO memory_stats (memory_id, namespace, org_id, created_at, content_hash)
                VALUES ($1, $2, $3, COALESCE($4, NOW()), $5)
                ON CONFLICT (memory_id) DO NOTHING
                """,
                [
                    (r["memory_id"], r["namespace"], r["org_id"],
                     r.get("created_at"), r["content_hash"])
                    for r in rows
                ],
            )
    except Exception as e:
        logger.warning("memory_stats.insert_stats_rows failed: %s", e)


async def merge_hits(keeper_id: str, extra_hits: int) -> None:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE memory_stats SET hits = hits + $2 WHERE memory_id = $1",
                keeper_id,
                int(extra_hits),
            )
    except Exception as e:
        logger.warning("memory_stats.merge_hits failed: %s", e)


async def mark_stale(namespace: str, decay_days: int, idle_days: int) -> int:
    """Mark old-and-idle memories stale. Returns how many rows were marked."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            return int(
                await conn.fetchval(
                    """
                    WITH updated AS (
                        UPDATE memory_stats SET stale_at = NOW()
                        WHERE namespace = $1
                          AND stale_at IS NULL
                          AND created_at < NOW() - ($2 * INTERVAL '1 day')
                          AND (last_hit_at IS NULL
                               OR last_hit_at < NOW() - ($3 * INTERVAL '1 day'))
                        RETURNING 1
                    )
                    SELECT count(*) FROM updated
                    """,
                    namespace,
                    int(decay_days),
                    int(idle_days),
                )
                or 0
            )
    except Exception as e:
        logger.warning("memory_stats.mark_stale failed: %s", e)
        return 0


async def list_tracked_namespaces() -> list[tuple[int, str]]:
    """(org_id, namespace) pairs known to memory_stats, plus every project namespace."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT org_id, namespace FROM memory_stats
                UNION
                SELECT org_id, memory_namespace AS namespace FROM projects
                """
            )
        return [(int(r["org_id"]), r["namespace"]) for r in rows]
    except Exception as e:
        logger.warning("memory_stats.list_tracked_namespaces failed: %s", e)
        return []


async def list_project_namespaces(project_namespace: str) -> list[str]:
    """The project namespace and every personal namespace derived from it."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT namespace FROM memory_stats "
                "WHERE namespace = $1 OR namespace LIKE $1 || '::u%' ORDER BY namespace",
                project_namespace,
            )
        return [r["namespace"] for r in rows]
    except Exception as e:
        logger.warning("memory_stats.list_project_namespaces failed: %s", e)
        return []


def extract_created_at(item: dict[str, Any]) -> Optional[Any]:
    """Mem0's created_at for an item, when it exposes one."""
    return item.get("created_at") or item.get("updated_at")
```

Note on `record_memories`: it must issue exactly one statement for the whole
batch (four parallel arrays fed to `unnest`), not one `INSERT` per memory — the
test asserts the four arrays as positional args in that order.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_memory_stats.py`
Expected: PASS.

- [ ] **Step 6: Run the whole suite to check nothing regressed**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`
Expected: PASS (baseline 530 + the new tests).

- [ ] **Step 7: Commit**

```bash
git add services/server/src/migrations/versions/0005_memory_stats.py services/server/src/memory_stats.py services/server/tests/test_memory_stats.py
git commit -m "add memory_stats table and helpers"
```

---

### Task 2: Personal namespace and scope resolution

**Files:**
- Modify: `services/server/src/mcp/context.py`
- Test: `services/server/tests/test_memory_scope_namespace.py`

**Interfaces:**
- Consumes:
  - `resolve_memory_namespace() -> Optional[str]` (already in
    `src/mcp/context.py`): the current project's Mem0 namespace, falling back
    to the org namespace in the context.
  - `get_current_user_id() -> Optional[int]` (already in the same module):
    the OIDC user id, `None` for API keys.
  - `Principal` and `get_current_principal()` — added to this same module by
    the MCP audit feature
    (`docs/superpowers/specs/2026-09-07-mcp-audit-ratelimit-design.md`). Shape:
    `Principal(kind: "user" | "api_key" | "anonymous", id: int | None, label: str)`.
    For `kind == "api_key"` the `id` is the `mcp_api_keys.id`. If these names
    are missing from `src/mcp/context.py`, stop: the audit feature has not
    landed and this task cannot be implemented as written.
  - `get_forge_config().memory.user_id` from `src/config.py` — the legacy
    single-tenant fallback namespace (default `"default"`).
  - `mcp_api_keys.created_by` (BIGINT, FK to `admin_users(id)`), already
    present and already populated on key creation.
- Produces, all in `src/mcp/context.py`:
  - `PERSONAL_SCOPE_ERROR: str = "Personal memory requires an authenticated caller"`
  - `class PersonalScopeUnavailable(RuntimeError)`
  - `MEMORY_SCOPES = ("project", "personal", "all")`
  - `async def resolve_personal_user_id() -> Optional[int]`
  - `async def resolve_personal_namespace() -> Optional[str]`
  - `async def resolve_scope_namespaces(scope: str) -> list[tuple[str, str]]`
    returning `[(scope_label, namespace), ...]`, e.g.
    `[("project", "acme--web"), ("personal", "acme--web::u4")]`.

- [ ] **Step 1: Write the failing test**

Create `services/server/tests/test_memory_scope_namespace.py`:

```python
"""Personal memory namespace derivation and scope resolution.

The personal namespace is f"{project_namespace}::u{user_id}". The user is the
OIDC user for user principals and the API key's creator (mcp_api_keys.created_by)
for API keys. Anonymous callers have no personal scope.
"""
from __future__ import annotations

import asyncio

import pytest

from src.mcp import context


class FakeConn:
    def __init__(self, fetchval_result=None):
        self.fetchval_result = fetchval_result
        self.executed = []

    async def fetchval(self, query, *args):
        self.executed.append((query, args))
        return self.fetchval_result


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

    monkeypatch.setattr(context, "get_pool", fake_pool)


@pytest.fixture(autouse=True)
def _clean_context():
    context.set_current_namespace(None)
    context.set_current_project_id(None)
    context.set_current_user_id(None)
    context.set_current_principal(None)
    yield
    context.set_current_namespace(None)
    context.set_current_project_id(None)
    context.set_current_user_id(None)
    context.set_current_principal(None)


def _project_ns(monkeypatch, namespace="acme--web"):
    async def _resolve():
        return namespace

    monkeypatch.setattr(context, "resolve_memory_namespace", _resolve)


def test_personal_user_id_from_a_user_principal(monkeypatch):
    context.set_current_principal(context.Principal("user", 4, "alice"))
    assert asyncio.run(context.resolve_personal_user_id()) == 4


def test_personal_user_id_from_the_api_key_creator(monkeypatch):
    conn = FakeConn(fetchval_result=9)
    _patch_pool(monkeypatch, conn)
    context.set_current_principal(context.Principal("api_key", 33, "ci-key"))

    assert asyncio.run(context.resolve_personal_user_id()) == 9
    query, args = conn.executed[0]
    assert "created_by" in query and "mcp_api_keys" in query
    assert args == (33,)


def test_personal_user_id_is_none_for_an_anonymous_principal():
    context.set_current_principal(context.Principal("anonymous", None, "anonymous"))
    assert asyncio.run(context.resolve_personal_user_id()) is None


def test_personal_namespace_appends_the_user_suffix(monkeypatch):
    _project_ns(monkeypatch)
    context.set_current_principal(context.Principal("user", 4, "alice"))
    assert asyncio.run(context.resolve_personal_namespace()) == "acme--web::u4"


def test_personal_namespace_is_none_without_a_user(monkeypatch):
    _project_ns(monkeypatch)
    context.set_current_principal(context.Principal("anonymous", None, "anonymous"))
    assert asyncio.run(context.resolve_personal_namespace()) is None


def test_scope_project_returns_only_the_project_namespace(monkeypatch):
    _project_ns(monkeypatch)
    context.set_current_principal(context.Principal("user", 4, "alice"))
    assert asyncio.run(context.resolve_scope_namespaces("project")) == [
        ("project", "acme--web")
    ]


def test_scope_all_returns_project_then_personal(monkeypatch):
    _project_ns(monkeypatch)
    context.set_current_principal(context.Principal("user", 4, "alice"))
    assert asyncio.run(context.resolve_scope_namespaces("all")) == [
        ("project", "acme--web"),
        ("personal", "acme--web::u4"),
    ]


def test_scope_all_drops_personal_for_an_anonymous_caller(monkeypatch):
    _project_ns(monkeypatch)
    assert asyncio.run(context.resolve_scope_namespaces("all")) == [
        ("project", "acme--web")
    ]


def test_scope_personal_rejects_an_anonymous_caller(monkeypatch):
    _project_ns(monkeypatch)
    with pytest.raises(context.PersonalScopeUnavailable) as excinfo:
        asyncio.run(context.resolve_scope_namespaces("personal"))
    assert str(excinfo.value) == "Personal memory requires an authenticated caller"


def test_unknown_scope_is_rejected(monkeypatch):
    _project_ns(monkeypatch)
    with pytest.raises(ValueError):
        asyncio.run(context.resolve_scope_namespaces("everything"))


def test_project_namespace_falls_back_to_the_forge_config_user_id(monkeypatch):
    async def _none():
        return None

    monkeypatch.setattr(context, "resolve_memory_namespace", _none)
    assert asyncio.run(context.resolve_scope_namespaces("project")) == [
        ("project", "default")
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_memory_scope_namespace.py`
Expected: FAIL — `AttributeError: module 'src.mcp.context' has no attribute 'resolve_personal_user_id'`.

- [ ] **Step 3: Implement the resolvers**

In `services/server/src/mcp/context.py`, add `get_pool` to the module-level
imports (safe: `src/db.py` imports only `src/config.py`, so there is no cycle):

```python
from ..db import get_pool
```

Then append to the module:

```python
PERSONAL_SCOPE_ERROR = "Personal memory requires an authenticated caller"
MEMORY_SCOPES = ("project", "personal", "all")


class PersonalScopeUnavailable(RuntimeError):
    """The personal memory scope was requested by a caller with no user."""


async def resolve_personal_user_id() -> Optional[int]:
    """User behind the current caller: the OIDC user, or the API key's creator."""
    principal = get_current_principal()
    if principal is not None and principal.kind == "user" and principal.id is not None:
        return int(principal.id)
    user_id = get_current_user_id()
    if user_id is not None:
        return int(user_id)
    if principal is not None and principal.kind == "api_key" and principal.id is not None:
        pool = await get_pool()
        async with pool.acquire() as conn:
            created_by = await conn.fetchval(
                "SELECT created_by FROM mcp_api_keys WHERE id = $1", principal.id
            )
        return int(created_by) if created_by is not None else None
    return None


async def _project_namespace_or_fallback() -> Optional[str]:
    from ..config import get_forge_config

    return await resolve_memory_namespace() or get_forge_config().memory.user_id or None


async def resolve_personal_namespace() -> Optional[str]:
    base = await _project_namespace_or_fallback()
    if not base:
        return None
    user_id = await resolve_personal_user_id()
    if user_id is None:
        return None
    return f"{base}::u{user_id}"


async def resolve_scope_namespaces(scope: str) -> list[tuple[str, str]]:
    """(scope label, namespace) pairs for 'project', 'personal' or 'all'."""
    if scope not in MEMORY_SCOPES:
        raise ValueError(f"Unknown memory scope '{scope}': use one of {MEMORY_SCOPES}")
    project_ns = await _project_namespace_or_fallback()
    if scope == "project":
        return [("project", project_ns)] if project_ns else []
    personal_ns = await resolve_personal_namespace()
    if scope == "personal":
        if not personal_ns:
            raise PersonalScopeUnavailable(PERSONAL_SCOPE_ERROR)
        return [("personal", personal_ns)]
    pairs: list[tuple[str, str]] = []
    if project_ns:
        pairs.append(("project", project_ns))
    if personal_ns:
        pairs.append(("personal", personal_ns))
    return pairs
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_memory_scope_namespace.py`
Expected: PASS.

- [ ] **Step 5: Run the whole suite**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/server/src/mcp/context.py services/server/tests/test_memory_scope_namespace.py
git commit -m "resolve personal memory namespace per user"
```

---

### Task 3: Scope-aware memory tools

**Files:**
- Modify: `services/server/src/mcp/memory.py`
- Modify: `services/server/tests/test_memory_project_scope.py` (one assertion)
- Test: `services/server/tests/test_memory_scope_tools.py`

**Interfaces:**
- Consumes from `src/mcp/context.py` (Task 2):
  `resolve_scope_namespaces(scope) -> list[tuple[str, str]]`,
  `PersonalScopeUnavailable`, `PERSONAL_SCOPE_ERROR`, and the pre-existing
  `resolve_org_id() -> Optional[int]`.
- Consumes from `src/memory_stats.py` (Task 1):
  `record_memories(namespace, org_id, items)`, `bump_hits(memory_ids)`,
  `stale_ids(memory_ids) -> set[str]`, `delete_stats(memory_ids)`.
- Consumes the existing `async def _get_memory(org_id: int)` in
  `src/mcp/memory.py`, which returns a Mem0 client with the synchronous methods
  `add(content, user_id=..., metadata=..., infer=...)`,
  `search(query, user_id=..., limit=...)`, `get_all(user_id=...)`,
  `get(memory_id)`, `delete(memory_id)`. Mem0 returns either a list or
  `{"results": [...]}`; each item is a dict with `id`, `memory`, optional
  `score`, `metadata`, `created_at`, `user_id`.
- Produces, in `src/mcp/memory.py`:
  - `async def search_memories(query: str, limit: int, scope: str, include_stale: bool) -> list[dict]`
    — the underlying search reused by the later `context_pack` feature with
    scope `"all"`. **This exact name and signature must not change.**
  - `async def list_memories(limit: int, scope: str, include_stale: bool) -> list[dict]`
  - `async def search_in_namespaces(client, query: str, namespaces: list[tuple[str, str]], limit: int, include_stale: bool) -> list[dict]`
  - `async def list_in_namespaces(client, namespaces: list[tuple[str, str]], limit: int, include_stale: bool) -> list[dict]`
    (the last two are also called by the REST routes in Task 4)
  - MCP tools, signatures verbatim:
    - `memory_add(content, metadata=None, infer=True, scope="project")`
    - `memory_search(query, limit=10, scope="all", include_stale=False)`
    - `memory_list(limit=20, scope="all", include_stale=False)`
    - `memory_delete(memory_id)` (unchanged signature)

- [ ] **Step 1: Write the failing test**

Create `services/server/tests/test_memory_scope_tools.py`:

```python
"""Scope-aware memory tools: personal namespace, merge, stale filter, hit bump."""
from __future__ import annotations

import asyncio
import inspect

import pytest

from src.mcp import context, memory
from src.mcp.permissions import ToolError, set_current_permissions


def _underlying(tool):
    return getattr(tool, "fn", tool)


class FakeMem:
    def __init__(self, search_results=None, all_results=None, get_result=None):
        self.search_results = search_results or {}
        self.all_results = all_results or {}
        self.get_result = get_result
        self.calls = []

    def add(self, content, user_id=None, metadata=None, infer=False):
        self.calls.append(("add", user_id, infer))
        return {"results": [{"id": "m-new", "memory": content, "event": "ADD"}]}

    def search(self, query, user_id=None, limit=10):
        self.calls.append(("search", user_id, limit))
        return {"results": list(self.search_results.get(user_id, []))}

    def get_all(self, user_id=None):
        self.calls.append(("get_all", user_id))
        return {"results": list(self.all_results.get(user_id, []))}

    def get(self, memory_id):
        return self.get_result

    def delete(self, memory_id):
        self.calls.append(("delete", memory_id))


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    set_current_permissions(frozenset({"*"}))

    async def _org():
        return 1

    monkeypatch.setattr(context, "resolve_org_id", _org)

    async def _ns():
        return "acme--web"

    monkeypatch.setattr(context, "resolve_memory_namespace", _ns)
    context.set_current_principal(context.Principal("user", 4, "alice"))
    yield
    context.set_current_principal(None)
    set_current_permissions(None)


def _patch_client(monkeypatch, fake):
    async def _get(org_id):
        return fake

    monkeypatch.setattr(memory, "_get_memory", _get)


def _patch_stats(monkeypatch, stale=frozenset(), recorded=None, bumped=None):
    import src.memory_stats as stats

    async def _stale(ids):
        return set(stale)

    async def _bump(ids):
        if bumped is not None:
            bumped.extend(ids)

    async def _record(namespace, org_id, items):
        if recorded is not None:
            recorded.append((namespace, org_id, items))

    async def _delete(ids):
        return None

    monkeypatch.setattr(stats, "stale_ids", _stale)
    monkeypatch.setattr(stats, "bump_hits", _bump)
    monkeypatch.setattr(stats, "record_memories", _record)
    monkeypatch.setattr(stats, "delete_stats", _delete)


# --- signatures ------------------------------------------------------------

def test_tool_signatures_are_the_agreed_ones():
    add = inspect.signature(_underlying(memory.memory_add)).parameters
    assert add["metadata"].default is None
    assert add["infer"].default is True
    assert add["scope"].default == "project"

    search = inspect.signature(_underlying(memory.memory_search)).parameters
    assert search["limit"].default == 10
    assert search["scope"].default == "all"
    assert search["include_stale"].default is False

    listing = inspect.signature(_underlying(memory.memory_list)).parameters
    assert listing["limit"].default == 20
    assert listing["scope"].default == "all"
    assert listing["include_stale"].default is False


def test_search_memories_is_exposed_for_reuse():
    params = list(inspect.signature(memory.search_memories).parameters)
    assert params == ["query", "limit", "scope", "include_stale"]


# --- add -------------------------------------------------------------------

def test_memory_add_personal_scope_uses_the_personal_namespace(monkeypatch):
    fake = FakeMem()
    _patch_client(monkeypatch, fake)
    recorded = []
    _patch_stats(monkeypatch, recorded=recorded)

    result = asyncio.run(_underlying(memory.memory_add)("note", scope="personal"))

    assert result["status"] == "ok"
    assert result["scope"] == "personal"
    assert ("add", "acme--web::u4", True) in fake.calls
    assert recorded == [("acme--web::u4", 1, [("m-new", "note")])]


def test_memory_add_rejects_personal_scope_for_anonymous_callers(monkeypatch):
    fake = FakeMem()
    _patch_client(monkeypatch, fake)
    _patch_stats(monkeypatch)
    context.set_current_principal(context.Principal("anonymous", None, "anonymous"))

    result = asyncio.run(_underlying(memory.memory_add)("note", scope="personal"))

    assert result == {
        "status": "error",
        "error": "Personal memory requires an authenticated caller",
    }
    assert fake.calls == []


# --- search ----------------------------------------------------------------

def test_memory_search_all_merges_and_labels_both_namespaces(monkeypatch):
    fake = FakeMem(search_results={
        "acme--web": [{"id": "p1", "memory": "proj", "score": 0.5}],
        "acme--web::u4": [{"id": "u1", "memory": "mine", "score": 0.9}],
    })
    _patch_client(monkeypatch, fake)
    bumped = []
    _patch_stats(monkeypatch, bumped=bumped)

    result = asyncio.run(_underlying(memory.memory_search)("q"))

    assert result["status"] == "ok"
    assert [(m["id"], m["scope"]) for m in result["memories"]] == [
        ("u1", "personal"), ("p1", "project")
    ]
    assert bumped == ["u1", "p1"]
    assert ("search", "acme--web", 20) in fake.calls
    assert ("search", "acme--web::u4", 20) in fake.calls


def test_memory_search_filters_stale_unless_requested(monkeypatch):
    fake = FakeMem(search_results={
        "acme--web": [{"id": "p1", "score": 0.5}, {"id": "p2", "score": 0.4}],
        "acme--web::u4": [],
    })
    _patch_client(monkeypatch, fake)
    _patch_stats(monkeypatch, stale={"p2"})

    hidden = asyncio.run(_underlying(memory.memory_search)("q"))
    assert [m["id"] for m in hidden["memories"]] == ["p1"]

    shown = asyncio.run(_underlying(memory.memory_search)("q", include_stale=True))
    assert [(m["id"], m["stale"]) for m in shown["memories"]] == [("p1", False), ("p2", True)]


def test_memory_search_respects_the_limit_after_merging(monkeypatch):
    fake = FakeMem(search_results={
        "acme--web": [{"id": "p1", "score": 0.5}, {"id": "p2", "score": 0.4}],
        "acme--web::u4": [{"id": "u1", "score": 0.9}],
    })
    _patch_client(monkeypatch, fake)
    _patch_stats(monkeypatch)

    result = asyncio.run(_underlying(memory.memory_search)("q", limit=2))
    assert [m["id"] for m in result["memories"]] == ["u1", "p1"]


def test_memory_search_project_scope_skips_the_personal_namespace(monkeypatch):
    fake = FakeMem(search_results={"acme--web": []})
    _patch_client(monkeypatch, fake)
    _patch_stats(monkeypatch)

    asyncio.run(_underlying(memory.memory_search)("q", scope="project"))
    assert [c for c in fake.calls if c[0] == "search"] == [("search", "acme--web", 20)]


# --- list ------------------------------------------------------------------

def test_memory_list_merges_both_namespaces_and_labels_scope(monkeypatch):
    fake = FakeMem(all_results={
        "acme--web": [{"id": "p1"}],
        "acme--web::u4": [{"id": "u1"}],
    })
    _patch_client(monkeypatch, fake)
    _patch_stats(monkeypatch)

    result = asyncio.run(_underlying(memory.memory_list)())

    assert {(m["id"], m["scope"]) for m in result["memories"]} == {
        ("p1", "project"), ("u1", "personal")
    }


# --- delete ----------------------------------------------------------------

def test_memory_delete_accepts_a_personal_memory(monkeypatch):
    fake = FakeMem(get_result={"id": "u1", "user_id": "acme--web::u4"})
    _patch_client(monkeypatch, fake)
    _patch_stats(monkeypatch)

    result = asyncio.run(_underlying(memory.memory_delete)("u1"))

    assert result == {"status": "ok", "deleted": "u1"}
    assert ("delete", "u1") in fake.calls


def test_memory_delete_still_rejects_a_foreign_namespace(monkeypatch):
    fake = FakeMem(get_result={"id": "x", "user_id": "other--proj"})
    _patch_client(monkeypatch, fake)
    _patch_stats(monkeypatch)

    with pytest.raises(ToolError):
        asyncio.run(_underlying(memory.memory_delete)("x"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_memory_scope_tools.py`
Expected: FAIL — `AttributeError: module 'src.mcp.memory' has no attribute 'search_memories'`.

- [ ] **Step 3: Rewrite the tool layer**

In `services/server/src/mcp/memory.py`, replace everything from the
`@mcp.tool()` above `memory_add` to the end of the file with:

```python
def _as_list(results: Any) -> list[dict]:
    """Mem0 returns either a list or {"results": [...]}."""
    if isinstance(results, dict):
        results = results.get("results", [])
    return [dict(item) for item in (results or [])]


async def _annotate_stale(items: list[dict], include_stale: bool) -> list[dict]:
    from ..memory_stats import stale_ids

    ids = [str(i["id"]) for i in items if i.get("id")]
    stale = await stale_ids(ids)
    for item in items:
        item["stale"] = str(item.get("id")) in stale
    return items if include_stale else [i for i in items if not i["stale"]]


async def search_in_namespaces(
    client,
    query: str,
    namespaces: list[tuple[str, str]],
    limit: int,
    include_stale: bool,
) -> list[dict]:
    """Search every (scope, namespace) pair, merge by score, bump hit counters."""
    from ..memory_stats import bump_hits

    merged: list[dict] = []
    for label, namespace in namespaces:
        # Fetch twice the limit: stale entries are filtered out afterwards.
        for item in _as_list(client.search(query, user_id=namespace, limit=limit * 2)):
            item["scope"] = label
            merged.append(item)
    merged.sort(key=lambda m: m.get("score") or 0, reverse=True)
    merged = (await _annotate_stale(merged, include_stale))[:limit]
    await bump_hits([str(m["id"]) for m in merged if m.get("id")])
    return merged


async def list_in_namespaces(
    client, namespaces: list[tuple[str, str]], limit: int, include_stale: bool
) -> list[dict]:
    """List every (scope, namespace) pair, newest namespaces first, stale filtered."""
    merged: list[dict] = []
    for label, namespace in namespaces:
        for item in _as_list(client.get_all(user_id=namespace)):
            item["scope"] = label
            merged.append(item)
    return (await _annotate_stale(merged, include_stale))[:limit]


async def search_memories(
    query: str, limit: int, scope: str, include_stale: bool
) -> list[dict]:
    """Underlying memory search shared by memory_search and context_pack."""
    from .context import resolve_org_id, resolve_scope_namespaces

    namespaces = await resolve_scope_namespaces(scope)
    if not namespaces:
        return []
    client = await _get_memory(await resolve_org_id())
    return await search_in_namespaces(client, query, namespaces, limit, include_stale)


async def list_memories(limit: int, scope: str, include_stale: bool) -> list[dict]:
    """Underlying memory listing shared by memory_list and the REST route."""
    from .context import resolve_org_id, resolve_scope_namespaces

    namespaces = await resolve_scope_namespaces(scope)
    if not namespaces:
        return []
    client = await _get_memory(await resolve_org_id())
    return await list_in_namespaces(client, namespaces, limit, include_stale)


@mcp.tool()
@requires_permission("context-write")
async def memory_add(
    content: str,
    metadata: Optional[dict[str, Any] | str] = None,
    infer: bool = True,
    scope: str = "project",
) -> dict:
    """Save a memory, fact, or note that should persist across sessions.

    Args:
        content: The text to remember (a fact, decision, note, etc.)
        metadata: Optional key-value metadata tags as a dict or JSON object string
            (e.g. {"project": "backend", "type": "decision"})
        infer: If True (default), runs LLM-based fact extraction that may split
            content into several smaller atomic memories. Pass False to store the
            text verbatim as a single memory — use that for a block of
            instructions or anything that should stay intact.
        scope: "project" (default, shared with everyone on the project) or
            "personal" (visible only to you).

    Returns:
        dict with the created memory id, content and scope
    """
    from ..memory_stats import record_memories
    from .context import PersonalScopeUnavailable, resolve_org_id, resolve_scope_namespaces

    if scope not in ("project", "personal"):
        return {"status": "error", "error": "scope must be 'project' or 'personal'"}
    try:
        namespaces = await resolve_scope_namespaces(scope)
    except PersonalScopeUnavailable as e:
        return {"status": "error", "error": str(e)}
    if not namespaces:
        return {"status": "error", "error": "No memory namespace for the current context"}

    label, namespace = namespaces[0]
    org_id = await resolve_org_id()
    try:
        client = await _get_memory(org_id)
        result = client.add(
            content, user_id=namespace, metadata=_normalize_metadata(metadata), infer=infer
        )
        created = [
            (str(i["id"]), i.get("memory") or content)
            for i in _as_list(result)
            if i.get("id")
        ]
        await record_memories(namespace, org_id, created)
        return {"status": "ok", "scope": label, "memory": result}
    except Exception as e:
        logger.error("memory_add failed: %s", e)
        return {"status": "error", "error": str(e)}


@mcp.tool()
@requires_permission("context-read")
async def memory_search(
    query: str, limit: int = 10, scope: str = "all", include_stale: bool = False
) -> dict:
    """Search the persistent cross-session memory semantically.

    Use this BEFORE answering questions about past decisions, conventions, or
    anything the user may have asked to remember in earlier sessions.

    Args:
        query: Natural language search query
        limit: Maximum number of results to return (default 10)
        scope: "all" (default: project + your personal memories), "project" or
            "personal"
        include_stale: Include memories the consolidation job marked stale

    Returns:
        dict with matching memories, each with id, content, score, metadata,
        scope and stale
    """
    from .context import PersonalScopeUnavailable

    try:
        memories = await search_memories(query, limit, scope, include_stale)
    except PersonalScopeUnavailable as e:
        return {"status": "error", "error": str(e)}
    except Exception as e:
        logger.error("memory_search failed: %s", e)
        return {"status": "error", "error": str(e)}
    return {"status": "ok", "memories": memories, "count": len(memories)}


@mcp.tool()
@requires_permission("context-read")
async def memory_list(
    limit: int = 20, scope: str = "all", include_stale: bool = False
) -> dict:
    """List recent memories.

    Args:
        limit: Maximum number of memories to return (default 20)
        scope: "all" (default), "project" or "personal"
        include_stale: Include memories the consolidation job marked stale

    Returns:
        dict with the memories, each labelled with its scope
    """
    from .context import PersonalScopeUnavailable

    try:
        memories = await list_memories(limit, scope, include_stale)
    except PersonalScopeUnavailable as e:
        return {"status": "error", "error": str(e)}
    except Exception as e:
        logger.error("memory_list failed: %s", e)
        return {"status": "error", "error": str(e)}
    return {"status": "ok", "memories": memories, "count": len(memories)}


@mcp.tool()
@requires_permission("context-write")
async def memory_delete(memory_id: str) -> dict:
    """Delete a specific memory by its ID.

    Only deletes the memory if it belongs to the caller's project or personal
    namespace — mem0's delete-by-id does not filter by user_id, so the ownership
    check has to happen here before the delete is issued.

    Args:
        memory_id: The ID of the memory (from memory_search or memory_list)

    Returns:
        dict with status
    """
    from ..memory_stats import delete_stats
    from .context import resolve_org_id, resolve_scope_namespaces
    from .permissions import ToolError

    namespaces = await resolve_scope_namespaces("all")
    owned = {ns for _, ns in namespaces}
    org_id = await resolve_org_id()

    try:
        client = await _get_memory(org_id)
        existing = client.get(memory_id)
    except Exception as e:
        logger.error("memory_delete lookup failed: %s", e)
        return {"status": "error", "error": str(e)}

    if not existing or existing.get("user_id") not in owned:
        raise ToolError(f"Memory '{memory_id}' not found in the current namespace")

    try:
        client.delete(memory_id)
        await delete_stats([memory_id])
        return {"status": "ok", "deleted": memory_id}
    except Exception as e:
        logger.error("memory_delete failed: %s", e)
        return {"status": "error", "error": str(e)}
```

Leave everything above (`_memory_collection_name`, `_build_memory_config`,
`_get_memory`, `reset_memory_client`, `_normalize_metadata`) untouched.

- [ ] **Step 4: Update the one assertion that counted the old namespace idiom**

`services/server/tests/test_memory_project_scope.py` currently asserts the old
`resolve_memory_namespace() or get_forge_config()...` idiom appears four times.
That fallback moved into `src/mcp/context.py` in Task 2. Replace the body of
`test_memory_module_resolves_uid_from_namespace_only` with:

```python
def test_memory_module_resolves_uid_from_namespace_only():
    source = inspect.getsource(memory)
    assert "user_id or" not in source
    # memory_add, search_memories, list_memories and memory_delete each resolve
    # their namespaces through the context, never from a caller-supplied value.
    assert source.count("await resolve_scope_namespaces(") == 4
```

- [ ] **Step 5: Run the memory tests**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_memory_scope_tools.py tests/test_memory_project_scope.py tests/test_memory_namespace_project.py`
Expected: PASS.

- [ ] **Step 6: Run the whole suite**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add services/server/src/mcp/memory.py services/server/tests/test_memory_scope_tools.py services/server/tests/test_memory_project_scope.py
git commit -m "add memory scopes and stale filtering to tools"
```

---

### Task 4: Scope-aware REST memory routes

**Files:**
- Modify: `services/server/src/api/routes/memory.py`
- Modify: `services/server/tests/test_memory_routes_org.py` (three call sites)
- Test: `services/server/tests/test_memory_routes_scope.py`

**Interfaces:**
- Consumes from `src/mcp/memory.py` (Task 3):
  - `async def search_in_namespaces(client, query, namespaces, limit, include_stale) -> list[dict]`
  - `async def list_in_namespaces(client, namespaces, limit, include_stale) -> list[dict]`
  - `async def _get_memory(org_id: int)` (the per-org Mem0 client)
  - `def _normalize_metadata(metadata) -> dict`
  - `def _as_list(results) -> list[dict]`
- Consumes from `src/memory_stats.py` (Task 1): `record_memories`,
  `delete_stats`, `restore_memory`.
- Consumes from `src/api/deps.py`: `ActiveProject`, `get_active_project`,
  `get_current_user_id`. `ActiveProject` is a dataclass with fields
  `org_id, project_id, role, namespace, name, org_name`, where `namespace` is
  the project's Mem0 namespace.
- Produces in `src/api/routes/memory.py`:
  - `def _scope_namespaces(project: ActiveProject, user_id: int, scope: str) -> list[tuple[str, str]]`
  - `MemoryAddRequest(content, metadata=None, infer=True, scope="project")`
  - `MemorySearchRequest(query, limit=20, scope="all", include_stale=False)`
  - `POST /api/memory`, `GET /api/memory?limit&scope&include_stale`,
    `POST /api/memory/search`, `DELETE /api/memory/{memory_id}`,
    `POST /api/memory/{memory_id}/restore`

- [ ] **Step 1: Write the failing test**

Create `services/server/tests/test_memory_routes_scope.py`:

```python
"""REST memory routes honour scope, include_stale, and the restore action.

The personal namespace for the REST API is f"{project.namespace}::u{user_id}",
where user_id is the authenticated session user.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

import src.api.routes.memory as routes
from src.api.deps import ActiveProject
from src.mcp import memory as memory_mod


class FakeMem:
    def __init__(self, search_results=None, all_results=None, get_result=None):
        self.search_results = search_results or {}
        self.all_results = all_results or {}
        self.get_result = get_result
        self.calls = []

    def add(self, content, user_id=None, metadata=None, infer=True):
        self.calls.append(("add", user_id))
        return {"results": [{"id": "m1", "memory": content}]}

    def search(self, query, user_id=None, limit=10):
        self.calls.append(("search", user_id, limit))
        return {"results": list(self.search_results.get(user_id, []))}

    def get_all(self, user_id=None):
        self.calls.append(("get_all", user_id))
        return {"results": list(self.all_results.get(user_id, []))}

    def get(self, memory_id):
        return self.get_result

    def delete(self, memory_id):
        self.calls.append(("delete", memory_id))


def _project(org_id=7, namespace="acme--web"):
    return ActiveProject(
        org_id=org_id, project_id=1, role="member", namespace=namespace,
        name="web", org_name="acme",
    )


@pytest.fixture
def patched(monkeypatch):
    state = {"stale": set(), "recorded": [], "deleted": [], "restored": []}

    def _install(fake):
        async def _get(org_id):
            state["org_id"] = org_id
            return fake

        monkeypatch.setattr(memory_mod, "_get_memory", _get)
        return fake

    import src.memory_stats as stats

    async def _stale(ids):
        return set(state["stale"])

    async def _bump(ids):
        state.setdefault("bumped", []).extend(ids)

    async def _record(namespace, org_id, items):
        state["recorded"].append((namespace, org_id, items))

    async def _delete(ids):
        state["deleted"].extend(ids)

    async def _restore(memory_id):
        state["restored"].append(memory_id)

    monkeypatch.setattr(stats, "stale_ids", _stale)
    monkeypatch.setattr(stats, "bump_hits", _bump)
    monkeypatch.setattr(stats, "record_memories", _record)
    monkeypatch.setattr(stats, "delete_stats", _delete)
    monkeypatch.setattr(stats, "restore_memory", _restore)

    state["install"] = _install
    return state


def test_scope_namespaces_builds_the_personal_suffix():
    assert routes._scope_namespaces(_project(), 4, "all") == [
        ("project", "acme--web"), ("personal", "acme--web::u4")
    ]
    assert routes._scope_namespaces(_project(), 4, "personal") == [
        ("personal", "acme--web::u4")
    ]
    assert routes._scope_namespaces(_project(), 4, "project") == [
        ("project", "acme--web")
    ]


def test_add_memory_personal_scope_writes_to_the_personal_namespace(patched):
    fake = patched["install"](FakeMem())

    result = asyncio.run(routes.add_memory(
        routes.MemoryAddRequest(content="note", scope="personal"),
        project=_project(), user_id=4,
    ))

    assert result["status"] == "ok"
    assert ("add", "acme--web::u4") in fake.calls
    assert patched["recorded"] == [("acme--web::u4", 7, [("m1", "note")])]


def test_add_memory_rejects_an_unknown_scope(patched):
    patched["install"](FakeMem())
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(routes.add_memory(
            routes.MemoryAddRequest(content="note", scope="everything"),
            project=_project(), user_id=4,
        ))
    assert excinfo.value.status_code == 400


def test_search_memories_merges_scopes_and_labels_results(patched):
    patched["install"](FakeMem(search_results={
        "acme--web": [{"id": "p1", "score": 0.4}],
        "acme--web::u4": [{"id": "u1", "score": 0.8}],
    }))

    result = asyncio.run(routes.search_memories(
        routes.MemorySearchRequest(query="q"), project=_project(), user_id=4
    ))

    assert [(m["id"], m["scope"]) for m in result["memories"]] == [
        ("u1", "personal"), ("p1", "project")
    ]


def test_list_memories_filters_stale_unless_requested(patched):
    patched["stale"] = {"p2"}
    patched["install"](FakeMem(all_results={
        "acme--web": [{"id": "p1"}, {"id": "p2"}],
        "acme--web::u4": [],
    }))

    hidden = asyncio.run(routes.list_memories(project=_project(), user_id=4))
    assert [m["id"] for m in hidden["memories"]] == ["p1"]

    shown = asyncio.run(routes.list_memories(
        include_stale=True, project=_project(), user_id=4
    ))
    assert [(m["id"], m["stale"]) for m in shown["memories"]] == [("p1", False), ("p2", True)]


def test_delete_memory_accepts_the_personal_namespace(patched):
    fake = patched["install"](FakeMem(get_result={"id": "u1", "user_id": "acme--web::u4"}))

    result = asyncio.run(routes.delete_memory("u1", project=_project(), user_id=4))

    assert result == {"status": "ok", "deleted": "u1"}
    assert ("delete", "u1") in fake.calls
    assert patched["deleted"] == ["u1"]


def test_delete_memory_refuses_a_foreign_namespace(patched):
    patched["install"](FakeMem(get_result={"id": "x", "user_id": "other--proj"}))

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(routes.delete_memory("x", project=_project(), user_id=4))
    assert excinfo.value.status_code == 404


def test_restore_memory_clears_the_stale_marker(patched):
    patched["install"](FakeMem(get_result={"id": "p1", "user_id": "acme--web"}))

    result = asyncio.run(routes.restore_memory("p1", project=_project(), user_id=4))

    assert result == {"status": "ok", "restored": "p1"}
    assert patched["restored"] == ["p1"]


def test_restore_memory_refuses_a_foreign_namespace(patched):
    patched["install"](FakeMem(get_result={"id": "x", "user_id": "other--proj"}))

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(routes.restore_memory("x", project=_project(), user_id=4))
    assert excinfo.value.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_memory_routes_scope.py`
Expected: FAIL — `AttributeError: module 'src.api.routes.memory' has no attribute '_scope_namespaces'`.

- [ ] **Step 3: Rewrite the routes**

Replace the whole content of `services/server/src/api/routes/memory.py` with:

```python
"""REST API routes for memory management."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..deps import ActiveProject, get_active_project, get_current_user_id

router = APIRouter(prefix="/memory", tags=["memory"])

SCOPES = ("project", "personal", "all")


async def _get_memory(org_id: int):
    from ...mcp.memory import _get_memory as _m
    return await _m(org_id)


def _scope_namespaces(
    project: ActiveProject, user_id: int, scope: str
) -> list[tuple[str, str]]:
    """(scope label, namespace) pairs for the active project and this user."""
    if scope not in SCOPES:
        raise HTTPException(status_code=400, detail=f"scope must be one of {SCOPES}")
    project_ns = project.namespace
    personal_ns = f"{project_ns}::u{user_id}"
    if scope == "project":
        return [("project", project_ns)]
    if scope == "personal":
        return [("personal", personal_ns)]
    return [("project", project_ns), ("personal", personal_ns)]


class MemoryAddRequest(BaseModel):
    content: str
    metadata: Optional[dict[str, Any] | str] = None
    infer: bool = True
    scope: str = "project"


class MemorySearchRequest(BaseModel):
    query: str
    limit: int = 20
    scope: str = "all"
    include_stale: bool = False


@router.post("")
async def add_memory(
    req: MemoryAddRequest,
    project: ActiveProject = Depends(get_active_project),
    user_id: int = Depends(get_current_user_id),
):
    """Add a memory in the project or in the caller's personal namespace.

    Set infer=false to store the text directly without LLM extraction."""
    if req.scope not in ("project", "personal"):
        raise HTTPException(status_code=400, detail="scope must be 'project' or 'personal'")
    label, namespace = _scope_namespaces(project, user_id, req.scope)[0]
    try:
        from ...mcp.memory import _as_list, _normalize_metadata
        from ...memory_stats import record_memories

        mem = await _get_memory(project.org_id)
        result = mem.add(
            req.content,
            user_id=namespace,
            metadata=_normalize_metadata(req.metadata),
            infer=req.infer,
        )
        created = [
            (str(i["id"]), i.get("memory") or req.content)
            for i in _as_list(result)
            if i.get("id")
        ]
        await record_memories(namespace, project.org_id, created)
        return {"status": "ok", "scope": label, "memory": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("")
async def list_memories(
    limit: int = 50,
    scope: str = "all",
    include_stale: bool = False,
    project: ActiveProject = Depends(get_active_project),
    user_id: int = Depends(get_current_user_id),
):
    """List recent memories in the requested scope."""
    namespaces = _scope_namespaces(project, user_id, scope)
    try:
        from ...mcp.memory import list_in_namespaces

        mem = await _get_memory(project.org_id)
        memories = await list_in_namespaces(mem, namespaces, limit, include_stale)
        return {"memories": memories, "count": len(memories)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/search")
async def search_memories(
    req: MemorySearchRequest,
    project: ActiveProject = Depends(get_active_project),
    user_id: int = Depends(get_current_user_id),
):
    """Search memories by semantic similarity in the requested scope."""
    namespaces = _scope_namespaces(project, user_id, req.scope)
    try:
        from ...mcp.memory import search_in_namespaces

        mem = await _get_memory(project.org_id)
        memories = await search_in_namespaces(
            mem, req.query, namespaces, req.limit, req.include_stale
        )
        return {"memories": memories, "count": len(memories)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def _owned_memory(memory_id: str, project: ActiveProject, user_id: int) -> dict:
    """Fetch a memory and refuse it (404) unless it is in one of the caller's namespaces.

    mem0's lookup does not filter by user_id, so the check happens here.
    """
    owned = {ns for _, ns in _scope_namespaces(project, user_id, "all")}
    mem = await _get_memory(project.org_id)
    existing = mem.get(memory_id)
    if not existing or existing.get("user_id") not in owned:
        raise HTTPException(status_code=404, detail=f"Memory '{memory_id}' not found")
    return existing


@router.delete("/{memory_id}")
async def delete_memory(
    memory_id: str,
    project: ActiveProject = Depends(get_active_project),
    user_id: int = Depends(get_current_user_id),
):
    """Delete a memory by ID, if it belongs to the project or to the caller."""
    try:
        await _owned_memory(memory_id, project, user_id)
        from ...memory_stats import delete_stats

        mem = await _get_memory(project.org_id)
        mem.delete(memory_id)
        await delete_stats([memory_id])
        return {"status": "ok", "deleted": memory_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{memory_id}/restore")
async def restore_memory(
    memory_id: str,
    project: ActiveProject = Depends(get_active_project),
    user_id: int = Depends(get_current_user_id),
):
    """Clear the stale marker set by the consolidation job."""
    try:
        await _owned_memory(memory_id, project, user_id)
        from ...memory_stats import restore_memory as clear_stale

        await clear_stale(memory_id)
        return {"status": "ok", "restored": memory_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

- [ ] **Step 4: Update the existing route calls that now need a user_id**

`services/server/tests/test_memory_routes_org.py::test_memory_routes_resolve_org_scoped_client`
calls the three routes without `user_id`. Update those three calls (and only
those) to pass one:

```python
    project = _project(org_id=9)
    asyncio.run(memory_routes.add_memory(
        memory_routes.MemoryAddRequest(content="note"), project=project, user_id=5
    ))
    asyncio.run(memory_routes.list_memories(project=project, user_id=5))
    asyncio.run(memory_routes.search_memories(
        memory_routes.MemorySearchRequest(query="x"), project=project, user_id=5
    ))
```

Its `_FakeMem.search` must accept the `limit` keyword — it already does via
`**kwargs`. The assertion `recorded == [9, 9, 9]` still holds because
`_get_memory` is called once per route. The assertion `("search", "ns")` still
holds because the default scope `"all"` searches the project namespace `"ns"`
first. Leave `test_rest_delete_memory_route_scoped_to_active_project` in
`tests/test_memory_project_scope.py` alone — `delete_memory`'s source still
mentions `get_active_project` and reaches `project.namespace` through
`_scope_namespaces`; if that assertion fails, change it to check
`_scope_namespaces` instead and say so in the commit body.

- [ ] **Step 5: Run the route tests**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_memory_routes_scope.py tests/test_memory_routes_org.py tests/test_memory_project_scope.py`
Expected: PASS.

- [ ] **Step 6: Run the whole suite**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add services/server/src/api/routes/memory.py services/server/tests/test_memory_routes_scope.py services/server/tests/test_memory_routes_org.py
git commit -m "add memory scope and restore to REST routes"
```

---

### Task 5: Consolidation module

**Files:**
- Create: `services/server/src/memory_consolidation.py`
- Test: `services/server/tests/test_memory_consolidation.py`

**Interfaces:**
- Consumes from `src/memory_stats.py` (Task 1):
  `normalize_text(text) -> str`, `content_hash(text) -> str`,
  `fetch_namespace_stats(namespace) -> list[dict]` (rows with
  `memory_id, hits, created_at, content_hash, stale_at`),
  `insert_stats_rows(rows)` (rows: dicts with
  `memory_id, namespace, org_id, created_at, content_hash`),
  `merge_hits(keeper_id, extra_hits)`, `delete_stats(memory_ids)`,
  `mark_stale(namespace, decay_days, idle_days) -> int`,
  `list_tracked_namespaces() -> list[tuple[int, str]]`,
  `list_project_namespaces(project_namespace) -> list[str]`,
  `extract_created_at(item) -> Any | None`.
- Consumes `src.mcp.memory._get_memory(org_id)` (Mem0 client with
  `get_all(user_id=...)` and `delete(memory_id)`) and `src.mcp.memory._as_list`.
- Consumes `src.config.get_settings()` — after Task 6 it exposes
  `memory_decay_days` (default 180) and `memory_decay_idle_days` (default 90).
  **This task must not depend on those fields existing**: read them with
  `getattr(settings, "memory_decay_days", 180)` so the module works before and
  after Task 6.
- Produces:
  - `NEAR_DUPLICATE_THRESHOLD = 0.9`
  - `MAX_NAMESPACE_MEMORIES = 2000`
  - `def jaccard(a: set[str], b: set[str]) -> float`
  - `async def consolidate_namespace(org_id: int, namespace: str) -> dict`
    returning `{"deduplicated": n, "marked_stale": m, "scanned": k}`
  - `async def consolidate_project(org_id: int, project_namespace: str) -> dict`
  - `async def consolidate_all() -> dict`

- [ ] **Step 1: Write the failing test**

Create `services/server/tests/test_memory_consolidation.py`:

```python
"""Consolidation: backfill, exact dedup, near-duplicate dedup, decay."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from src import memory_consolidation as consolidation
from src import memory_stats


def _dt(days_ago: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


class FakeMem:
    def __init__(self, items):
        self.items = items
        self.deleted = []

    def get_all(self, user_id=None):
        return {"results": list(self.items)}

    def delete(self, memory_id):
        self.deleted.append(memory_id)


@pytest.fixture
def stub(monkeypatch):
    state = {
        "stats": [],
        "inserted": [],
        "merged": [],
        "deleted_stats": [],
        "marked": 0,
        "mark_calls": [],
    }

    async def _fetch(namespace):
        return list(state["stats"])

    async def _insert(rows):
        state["inserted"].extend(rows)

    async def _merge(keeper_id, extra_hits):
        state["merged"].append((keeper_id, extra_hits))

    async def _delete(ids):
        state["deleted_stats"].extend(ids)

    async def _mark(namespace, decay_days, idle_days):
        state["mark_calls"].append((namespace, decay_days, idle_days))
        return state["marked"]

    monkeypatch.setattr(memory_stats, "fetch_namespace_stats", _fetch)
    monkeypatch.setattr(memory_stats, "insert_stats_rows", _insert)
    monkeypatch.setattr(memory_stats, "merge_hits", _merge)
    monkeypatch.setattr(memory_stats, "delete_stats", _delete)
    monkeypatch.setattr(memory_stats, "mark_stale", _mark)

    def _client(items):
        fake = FakeMem(items)

        async def _get(org_id):
            return fake

        monkeypatch.setattr(consolidation, "_get_memory", _get)
        return fake

    state["client"] = _client
    return state


# --- jaccard ---------------------------------------------------------------

def test_jaccard_of_identical_token_sets_is_one():
    assert consolidation.jaccard({"a", "b"}, {"a", "b"}) == 1.0


def test_jaccard_of_disjoint_token_sets_is_zero():
    assert consolidation.jaccard({"a"}, {"b"}) == 0.0


def test_jaccard_threshold_is_zero_point_nine():
    assert consolidation.NEAR_DUPLICATE_THRESHOLD == 0.9


# --- backfill --------------------------------------------------------------

def test_missing_stats_rows_are_backfilled_with_mem0_created_at(stub):
    created = _dt(10)
    stub["client"]([{"id": "m1", "memory": "alpha", "created_at": created}])

    result = asyncio.run(consolidation.consolidate_namespace(3, "ns"))

    assert stub["inserted"] == [{
        "memory_id": "m1",
        "namespace": "ns",
        "org_id": 3,
        "created_at": created,
        "content_hash": memory_stats.content_hash("alpha"),
    }]
    assert result["scanned"] == 1


# --- exact dedup -----------------------------------------------------------

def test_exact_duplicates_keep_the_oldest_and_merge_its_hits(stub):
    h = memory_stats.content_hash("same text")
    stub["stats"] = [
        {"memory_id": "old", "hits": 2, "created_at": _dt(30), "content_hash": h,
         "stale_at": None},
        {"memory_id": "new", "hits": 5, "created_at": _dt(1), "content_hash": h,
         "stale_at": None},
    ]
    fake = stub["client"]([
        {"id": "old", "memory": "same text"},
        {"id": "new", "memory": "Same  text!"},
    ])

    result = asyncio.run(consolidation.consolidate_namespace(3, "ns"))

    assert fake.deleted == ["new"]
    assert stub["deleted_stats"] == ["new"]
    assert stub["merged"] == [("old", 5)]
    assert result["deduplicated"] == 1


# --- near duplicates -------------------------------------------------------

def test_near_duplicates_above_the_threshold_are_merged(stub):
    a = "the billing service reads provider tokens from runtime settings today"
    b = "the billing service reads provider tokens from runtime settings"
    stub["stats"] = [
        {"memory_id": "a", "hits": 1, "created_at": _dt(30),
         "content_hash": memory_stats.content_hash(a), "stale_at": None},
        {"memory_id": "b", "hits": 3, "created_at": _dt(2),
         "content_hash": memory_stats.content_hash(b), "stale_at": None},
    ]
    fake = stub["client"]([{"id": "a", "memory": a}, {"id": "b", "memory": b}])

    result = asyncio.run(consolidation.consolidate_namespace(3, "ns"))

    assert fake.deleted == ["b"]
    assert stub["merged"] == [("a", 3)]
    assert result["deduplicated"] == 1


def test_distinct_memories_below_the_threshold_are_left_alone(stub):
    a = "use reciprocal rank fusion for hybrid search"
    b = "the deployment runs behind traefik on the staging cluster"
    stub["stats"] = [
        {"memory_id": "a", "hits": 0, "created_at": _dt(30),
         "content_hash": memory_stats.content_hash(a), "stale_at": None},
        {"memory_id": "b", "hits": 0, "created_at": _dt(2),
         "content_hash": memory_stats.content_hash(b), "stale_at": None},
    ]
    fake = stub["client"]([{"id": "a", "memory": a}, {"id": "b", "memory": b}])

    result = asyncio.run(consolidation.consolidate_namespace(3, "ns"))

    assert fake.deleted == []
    assert result["deduplicated"] == 0


def test_above_the_cap_only_exact_dedup_runs(stub, caplog):
    items = [{"id": f"m{i}", "memory": f"memory number {i}"}
             for i in range(consolidation.MAX_NAMESPACE_MEMORIES + 1)]
    items[0] = {"id": "d1", "memory": "dup"}
    items[1] = {"id": "d2", "memory": "dup"}
    stub["stats"] = [
        {"memory_id": it["id"], "hits": 0, "created_at": _dt(i + 1),
         "content_hash": memory_stats.content_hash(it["memory"]), "stale_at": None}
        for i, it in enumerate(items)
    ]
    fake = stub["client"](items)

    with caplog.at_level("WARNING"):
        result = asyncio.run(consolidation.consolidate_namespace(3, "ns"))

    assert fake.deleted == ["d2"]
    assert result["deduplicated"] == 1
    assert any("near-duplicate" in r.getMessage().lower() for r in caplog.records)


# --- decay -----------------------------------------------------------------

def test_decay_uses_the_configured_windows(stub, monkeypatch):
    from src import config

    settings = config.get_settings()
    monkeypatch.setattr(settings, "memory_decay_days", 200, raising=False)
    monkeypatch.setattr(settings, "memory_decay_idle_days", 30, raising=False)
    stub["marked"] = 4
    stub["client"]([])

    result = asyncio.run(consolidation.consolidate_namespace(3, "ns"))

    assert stub["mark_calls"] == [("ns", 200, 30)]
    assert result["marked_stale"] == 4


def test_consolidate_namespace_returns_the_three_counters(stub):
    stub["client"]([])
    assert asyncio.run(consolidation.consolidate_namespace(3, "ns")) == {
        "deduplicated": 0, "marked_stale": 0, "scanned": 0
    }


# --- fan-out ---------------------------------------------------------------

def test_consolidate_project_covers_the_project_and_personal_namespaces(monkeypatch):
    seen = []

    async def _namespaces(project_namespace):
        return ["acme--web", "acme--web::u4"]

    async def _one(org_id, namespace):
        seen.append((org_id, namespace))
        return {"deduplicated": 1, "marked_stale": 2, "scanned": 3}

    monkeypatch.setattr(memory_stats, "list_project_namespaces", _namespaces)
    monkeypatch.setattr(consolidation, "consolidate_namespace", _one)

    result = asyncio.run(consolidation.consolidate_project(7, "acme--web"))

    assert seen == [(7, "acme--web"), (7, "acme--web::u4")]
    assert result == {"deduplicated": 2, "marked_stale": 4, "scanned": 6, "namespaces": 2}


def test_consolidate_project_includes_an_untracked_project_namespace(monkeypatch):
    seen = []

    async def _namespaces(project_namespace):
        return []

    async def _one(org_id, namespace):
        seen.append((org_id, namespace))
        return {"deduplicated": 0, "marked_stale": 0, "scanned": 0}

    monkeypatch.setattr(memory_stats, "list_project_namespaces", _namespaces)
    monkeypatch.setattr(consolidation, "consolidate_namespace", _one)

    asyncio.run(consolidation.consolidate_project(7, "acme--web"))

    assert seen == [(7, "acme--web")]


def test_consolidate_all_visits_every_tracked_namespace(monkeypatch):
    seen = []

    async def _tracked():
        return [(1, "a"), (1, "a::u2"), (2, "b")]

    async def _one(org_id, namespace):
        seen.append((org_id, namespace))
        return {"deduplicated": 0, "marked_stale": 1, "scanned": 2}

    monkeypatch.setattr(memory_stats, "list_tracked_namespaces", _tracked)
    monkeypatch.setattr(consolidation, "consolidate_namespace", _one)

    result = asyncio.run(consolidation.consolidate_all())

    assert seen == [(1, "a"), (1, "a::u2"), (2, "b")]
    assert result["namespaces"] == 3
    assert result["marked_stale"] == 3


def test_consolidate_all_keeps_going_when_one_namespace_fails(monkeypatch):
    async def _tracked():
        return [(1, "bad"), (1, "good")]

    async def _one(org_id, namespace):
        if namespace == "bad":
            raise RuntimeError("mem0 down")
        return {"deduplicated": 0, "marked_stale": 1, "scanned": 1}

    monkeypatch.setattr(memory_stats, "list_tracked_namespaces", _tracked)
    monkeypatch.setattr(consolidation, "consolidate_namespace", _one)

    result = asyncio.run(consolidation.consolidate_all())

    assert result["namespaces"] == 1
    assert result["marked_stale"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_memory_consolidation.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.memory_consolidation'`.

- [ ] **Step 3: Write the consolidation module**

Create `services/server/src/memory_consolidation.py`:

```python
"""Memory consolidation: de-duplicate memories and let unused ones fade.

Exact duplicates are grouped by content hash; near-duplicates by token Jaccard
similarity within a namespace. Decay only marks memories stale — nothing is
deleted automatically.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from . import memory_stats
from .config import get_settings
from .mcp.memory import _as_list, _get_memory

logger = logging.getLogger(__name__)

NEAR_DUPLICATE_THRESHOLD = 0.9
MAX_NAMESPACE_MEMORIES = 2000

_EMPTY = {"deduplicated": 0, "marked_stale": 0, "scanned": 0}


def jaccard(a: set[str], b: set[str]) -> float:
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def _tokens(text: str) -> set[str]:
    return set(memory_stats.normalize_text(text).split())


def _text(item: dict) -> str:
    return item.get("memory") or item.get("content") or ""


def _sort_key(created_at: Optional[Any]) -> tuple[int, Any]:
    """Oldest first; rows with no timestamp sort last."""
    return (1, "") if created_at is None else (0, created_at)


async def _backfill(org_id: int, namespace: str, items: list[dict], stats: dict) -> None:
    missing = [
        {
            "memory_id": str(item["id"]),
            "namespace": namespace,
            "org_id": org_id,
            "created_at": memory_stats.extract_created_at(item),
            "content_hash": memory_stats.content_hash(_text(item)),
        }
        for item in items
        if item.get("id") and str(item["id"]) not in stats
    ]
    if missing:
        await memory_stats.insert_stats_rows(missing)
        for row in missing:
            stats[row["memory_id"]] = {
                "memory_id": row["memory_id"],
                "hits": 0,
                "created_at": row["created_at"],
                "content_hash": row["content_hash"],
                "stale_at": None,
            }


async def _drop(client, keeper: str, loser: dict) -> None:
    client.delete(loser["memory_id"])
    await memory_stats.delete_stats([loser["memory_id"]])
    if loser.get("hits"):
        await memory_stats.merge_hits(keeper, int(loser["hits"]))


async def consolidate_namespace(org_id: int, namespace: str) -> dict:
    """De-duplicate and decay one namespace. Returns the three counters."""
    client = await _get_memory(org_id)
    items = [i for i in _as_list(client.get_all(user_id=namespace)) if i.get("id")]
    scanned = len(items)

    stats = {row["memory_id"]: row for row in await memory_stats.fetch_namespace_stats(namespace)}
    await _backfill(org_id, namespace, items, stats)

    by_id = {str(i["id"]): i for i in items}
    rows = [stats[mid] for mid in by_id if mid in stats]
    rows.sort(key=lambda r: _sort_key(r.get("created_at")))

    deduplicated = 0
    survivors: list[dict] = []
    keepers_by_hash: dict[str, dict] = {}
    for row in rows:
        keeper = keepers_by_hash.get(row["content_hash"])
        if keeper is not None:
            await _drop(client, keeper["memory_id"], row)
            deduplicated += 1
            continue
        keepers_by_hash[row["content_hash"]] = row
        survivors.append(row)

    if scanned > MAX_NAMESPACE_MEMORIES:
        logger.warning(
            "Namespace %s has %d memories (cap %d): skipping near-duplicate pass",
            namespace, scanned, MAX_NAMESPACE_MEMORIES,
        )
    else:
        kept: list[tuple[dict, set[str]]] = []
        for row in survivors:
            tokens = _tokens(_text(by_id[row["memory_id"]]))
            match = next(
                (k for k, kt in kept if jaccard(kt, tokens) >= NEAR_DUPLICATE_THRESHOLD),
                None,
            )
            if match is not None:
                await _drop(client, match["memory_id"], row)
                deduplicated += 1
                continue
            kept.append((row, tokens))

    settings = get_settings()
    marked = await memory_stats.mark_stale(
        namespace,
        getattr(settings, "memory_decay_days", 180),
        getattr(settings, "memory_decay_idle_days", 90),
    )
    return {"deduplicated": deduplicated, "marked_stale": marked, "scanned": scanned}


def _accumulate(total: dict, counts: dict) -> None:
    for key in ("deduplicated", "marked_stale", "scanned"):
        total[key] += counts.get(key, 0)
    total["namespaces"] += 1


async def consolidate_project(org_id: int, project_namespace: str) -> dict:
    """Consolidate a project's namespace and every personal namespace under it."""
    namespaces = await memory_stats.list_project_namespaces(project_namespace)
    if project_namespace not in namespaces:
        namespaces = [project_namespace, *namespaces]
    total = {"deduplicated": 0, "marked_stale": 0, "scanned": 0, "namespaces": 0}
    for namespace in namespaces:
        _accumulate(total, await consolidate_namespace(org_id, namespace))
    return total


async def consolidate_all() -> dict:
    """Consolidate every namespace known to memory_stats and every project namespace."""
    total = {"deduplicated": 0, "marked_stale": 0, "scanned": 0, "namespaces": 0}
    for org_id, namespace in await memory_stats.list_tracked_namespaces():
        try:
            _accumulate(total, await consolidate_namespace(org_id, namespace))
        except Exception as e:
            logger.warning("Consolidation failed for namespace %s: %s", namespace, e)
    return total
```

- [ ] **Step 4: Run the consolidation tests**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_memory_consolidation.py`
Expected: PASS.

- [ ] **Step 5: Run the whole suite**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/server/src/memory_consolidation.py services/server/tests/test_memory_consolidation.py
git commit -m "add memory consolidation and decay module"
```

---

### Task 6: Scheduler job, settings, and the manual consolidate endpoint

**Files:**
- Modify: `services/server/src/config.py`
- Modify: `services/server/src/scheduler.py`
- Modify: `services/server/src/api/routes/memory.py`
- Test: `services/server/tests/test_memory_consolidation_job.py`

**Interfaces:**
- Consumes from `src/memory_consolidation.py` (Task 5):
  `async def consolidate_all() -> dict` and
  `async def consolidate_project(org_id: int, project_namespace: str) -> dict`,
  both returning `{"deduplicated", "marked_stale", "scanned", "namespaces"}`.
- Consumes from `src/scheduler.py`: `start_scheduler()` registers jobs on a
  module-level `AsyncIOScheduler` with `_scheduler.add_job(...)`; `CronTrigger`
  is already imported there.
- Consumes from `src/api/deps.py`: `require_project_role(minimum)` — a
  dependency factory returning an `ActiveProject`; `role_at_least` treats
  `admin` and `owner` as satisfying `"admin"`, so `require_project_role("admin")`
  is the org admin/owner gate the spec asks for.
- Produces:
  - `Settings.memory_decay_days: int = 180` and
    `Settings.memory_decay_idle_days: int = 90` in `src/config.py`
    (env `MEMORY_DECAY_DAYS`, `MEMORY_DECAY_IDLE_DAYS`; pydantic-settings maps
    the field name to the upper-case env var automatically)
  - `async def _consolidate_memories() -> None` and the `memory_consolidation`
    job in `src/scheduler.py`
  - `POST /api/memory/consolidate` in `src/api/routes/memory.py`

- [ ] **Step 1: Write the failing test**

Create `services/server/tests/test_memory_consolidation_job.py`:

```python
"""Daily consolidation job and the admin-only manual trigger."""
from __future__ import annotations

import asyncio
import inspect

import pytest
from fastapi import HTTPException

from src import scheduler
from src.api.deps import ActiveProject


def test_settings_expose_the_decay_windows():
    from src.config import Settings

    s = Settings()
    assert s.memory_decay_days == 180
    assert s.memory_decay_idle_days == 90


def test_scheduler_registers_a_daily_consolidation_job():
    source = inspect.getsource(scheduler)
    assert "consolidate_all" in source
    assert "memory_consolidation" in source
    assert "CronTrigger" in source


def test_scheduler_job_delegates_to_consolidate_all(monkeypatch):
    called = []

    async def _all():
        called.append(True)
        return {"deduplicated": 1, "marked_stale": 2, "scanned": 3, "namespaces": 1}

    import src.memory_consolidation as consolidation

    monkeypatch.setattr(consolidation, "consolidate_all", _all)
    asyncio.run(scheduler._consolidate_memories())
    assert called == [True]


def test_scheduler_job_swallows_failures(monkeypatch):
    async def _boom():
        raise RuntimeError("mem0 down")

    import src.memory_consolidation as consolidation

    monkeypatch.setattr(consolidation, "consolidate_all", _boom)
    asyncio.run(scheduler._consolidate_memories())


def test_consolidate_endpoint_runs_the_active_project(monkeypatch):
    import src.api.routes.memory as routes
    import src.memory_consolidation as consolidation

    seen = []

    async def _project(org_id, namespace):
        seen.append((org_id, namespace))
        return {"deduplicated": 1, "marked_stale": 0, "scanned": 5, "namespaces": 2}

    monkeypatch.setattr(consolidation, "consolidate_project", _project)

    project = ActiveProject(
        org_id=7, project_id=1, role="admin", namespace="acme--web",
        name="web", org_name="acme",
    )
    result = asyncio.run(routes.consolidate_memories(project=project))

    assert seen == [(7, "acme--web")]
    assert result == {"deduplicated": 1, "marked_stale": 0, "scanned": 5, "namespaces": 2}


def test_consolidate_endpoint_requires_admin():
    import src.api.routes.memory as routes

    source = inspect.getsource(routes.consolidate_memories)
    assert 'require_project_role("admin")' in source


def test_consolidate_endpoint_rejects_a_member():
    """The dependency factory is the gate: a member must not get through it."""
    from src.api.deps import require_project_role

    checker = require_project_role("admin")
    member = ActiveProject(
        org_id=7, project_id=1, role="member", namespace="acme--web",
        name="web", org_name="acme",
    )
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(checker(project=member))
    assert excinfo.value.status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_memory_consolidation_job.py`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'memory_decay_days'`.

- [ ] **Step 3: Add the settings**

In `services/server/src/config.py`, inside `class Settings(BaseSettings)`, add
right after `search_hybrid`:

```python
    # Memory consolidation: memories older than MEMORY_DECAY_DAYS with no hit in
    # the last MEMORY_DECAY_IDLE_DAYS are marked stale (never deleted).
    memory_decay_days: int = 180
    memory_decay_idle_days: int = 90
```

Do **not** add them to `RUNTIME_OVERRIDE_FIELDS` / `ORG_OVERRIDE_FIELDS`: they
are deployment-wide knobs, not per-organization provider settings, so they need
no Settings-page field.

- [ ] **Step 4: Register the scheduler job**

In `services/server/src/scheduler.py`, add next to the other job callbacks:

```python
async def _consolidate_memories() -> None:
    """Daily: de-duplicate memories and mark old, unused ones stale."""
    from .memory_consolidation import consolidate_all

    try:
        counts = await consolidate_all()
    except Exception as e:
        logger.warning("Memory consolidation failed: %s", e)
        return
    if counts.get("deduplicated") or counts.get("marked_stale"):
        logger.info(
            "Memory consolidation: %d deduplicated, %d marked stale across %d namespace(s)",
            counts["deduplicated"], counts["marked_stale"], counts["namespaces"],
        )
```

and inside `start_scheduler`, before `_scheduler.start()`:

```python
    # Daily: memory dedup + decay. Global job (not per-organization).
    _scheduler.add_job(
        _consolidate_memories,
        CronTrigger(minute="30", hour="3"),
        id="memory_consolidation",
        replace_existing=True,
    )
```

`from .memory_consolidation import consolidate_all` stays inside the function so
the test can monkeypatch `src.memory_consolidation.consolidate_all`.

- [ ] **Step 5: Add the manual endpoint**

In `services/server/src/api/routes/memory.py`, change the deps import to:

```python
from ..deps import (
    ActiveProject,
    get_active_project,
    get_current_user_id,
    require_project_role,
)
```

and add this route **above** `@router.delete("/{memory_id}")`:

```python
@router.post("/consolidate")
async def consolidate_memories(
    project: ActiveProject = Depends(require_project_role("admin")),
):
    """Run de-duplication and decay for this project's namespaces (admin/owner)."""
    from ...memory_consolidation import consolidate_project

    try:
        return await consolidate_project(project.org_id, project.namespace)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

- [ ] **Step 6: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_memory_consolidation_job.py tests/test_memory_routes_scope.py`
Expected: PASS.

- [ ] **Step 7: Run the whole suite**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add services/server/src/config.py services/server/src/scheduler.py services/server/src/api/routes/memory.py services/server/tests/test_memory_consolidation_job.py
git commit -m "schedule memory consolidation and expose manual run"
```

---

### Task 7: Memory page scope selector, stale badge, consolidate button

**Files:**
- Modify: `services/ui/src/lib/api.ts`
- Modify: `services/ui/src/pages/Memory.tsx`

**Interfaces:**
- Consumes the REST API from Tasks 4 and 6:
  - `POST /api/memory` body `{content, metadata?, infer?, scope?}` → `{status, scope, memory}`
  - `GET /api/memory?limit=50&scope=all&include_stale=false` → `{memories, count}`
  - `POST /api/memory/search` body `{query, limit, scope, include_stale}` → `{memories, count}`
  - `DELETE /api/memory/{id}` → `{status, deleted}`
  - `POST /api/memory/{id}/restore` → `{status, restored}`
  - `POST /api/memory/consolidate` → `{deduplicated, marked_stale, scanned, namespaces}`
  - Every memory item now carries `scope: "project" | "personal"` and
    `stale: boolean`.
- Consumes the existing UI kit from `../components/ui`:
  `Badge` (variants `default | success | warning | danger | accent | muted`),
  `Banner`, `Button` (`variant`, `loading`, `disabled`), `Card`, `Input`,
  `Select` (props `value`, `onValueChange`, `options: {value,label}[]`,
  `label?`, `className?`), `Textarea`, `useConfirm`, `useToast`.
- Consumes the role pattern from `pages/Organization.tsx`:

```tsx
const organizations = useAppStore((s) => s.organizations)
const activeOrgId = useAppStore((s) => s.activeOrgId)
const myRole: OrgRole = organizations.find((o) => o.id === activeOrgId)?.role ?? 'viewer'
```

with `const ROLE_RANK: Record<OrgRole, number> = { viewer: 0, member: 1, admin: 2, owner: 3 }`.
- Produces: `MemoryScope` type and the extended `api.memory` client.

- [ ] **Step 1: Extend the API client**

In `services/ui/src/lib/api.ts`, replace the `Memory` and
`MemoryCreateRequest` interfaces with:

```ts
export type MemoryScope = 'project' | 'personal' | 'all'

export interface Memory {
  id: string
  memory: string
  metadata?: Record<string, unknown>
  score?: number
  created_at?: string
  scope?: 'project' | 'personal'
  stale?: boolean
}

export interface MemoryCreateRequest {
  content: string
  metadata?: Record<string, unknown>
  user_id?: string
  infer?: boolean
  scope?: 'project' | 'personal'
}

export interface MemoryConsolidateResult {
  deduplicated: number
  marked_stale: number
  scanned: number
  namespaces: number
}
```

and replace the `memory: { ... }` block of the `api` object with:

```ts
  memory: {
    create: (req: MemoryCreateRequest) =>
      request<{ status: string; scope: string; memory: unknown }>('/api/memory', {
        method: 'POST',
        body: JSON.stringify(req),
      }),
    list: (limit = 50, scope: MemoryScope = 'all', includeStale = false) =>
      request<{ memories: Memory[]; count: number }>(
        `/api/memory?limit=${limit}&scope=${scope}&include_stale=${includeStale}`,
      ),
    search: (query: string, limit = 20, scope: MemoryScope = 'all', includeStale = false) =>
      request<{ memories: Memory[]; count: number }>('/api/memory/search', {
        method: 'POST',
        body: JSON.stringify({ query, limit, scope, include_stale: includeStale }),
      }),
    delete: (id: string) => request(`/api/memory/${encodeURIComponent(id)}`, { method: 'DELETE' }),
    restore: (id: string) =>
      request<{ status: string; restored: string }>(
        `/api/memory/${encodeURIComponent(id)}/restore`,
        { method: 'POST' },
      ),
    consolidate: () =>
      request<MemoryConsolidateResult>('/api/memory/consolidate', { method: 'POST' }),
  },
```

- [ ] **Step 2: Verify the client compiles**

Run from `services/ui`: `npx tsc --noEmit`
Expected: errors only in `src/pages/Memory.tsx` (its `api.memory.list(...)`
call site is still fine, but the page does not yet use the new fields). If
there are no errors at all, that is fine too — continue.

- [ ] **Step 3: Update the Memory page**

In `services/ui/src/pages/Memory.tsx`:

1. Extend the imports:

```tsx
import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { Plus, RotateCcw, Sparkles, Trash2, X } from 'lucide-react'
import { api, type Memory as MemoryItem, type MemoryScope, type OrgRole } from '../lib/api'
import { Badge, Banner, Button, Card, Input, Select, Textarea, useConfirm, useToast } from '../components/ui'
import { useAppStore } from '../store'
```

2. Add these module-level constants next to `FETCH_LIMIT`:

```tsx
const ROLE_RANK: Record<OrgRole, number> = { viewer: 0, member: 1, admin: 2, owner: 3 }

const SCOPE_FILTER_OPTIONS = [
  { value: 'all', label: 'All memories' },
  { value: 'project', label: 'Project' },
  { value: 'personal', label: 'Mine' },
]

const SCOPE_FORM_OPTIONS = [
  { value: 'project', label: 'Project (shared)' },
  { value: 'personal', label: 'Mine (private)' },
]
```

3. Give `MemoryRow` a restore handler and a stale badge. Replace the component
   signature and its content cell with:

```tsx
function MemoryRow({
  memory,
  onDelete,
  onRestore,
}: {
  memory: MemoryItem
  onDelete: (id: string) => Promise<void>
  onRestore: (id: string) => Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  const type = getType(memory)
  const tags = getTags(memory)
  const extra = getExtraMetadata(memory)

  const handleDelete = async () => {
    setBusy(true)
    try {
      await onDelete(memory.id)
    } finally {
      setBusy(false)
    }
  }

  const handleRestore = async () => {
    setBusy(true)
    try {
      await onRestore(memory.id)
    } finally {
      setBusy(false)
    }
  }

  return (
    <tr className="border-b border-border last:border-b-0 group">
      <td className="py-3 px-4 align-top min-w-0">
        <p className="text-sm break-words">{memory.memory}</p>
        <div className="flex flex-wrap gap-1 mt-1.5 min-w-0">
          {memory.scope === 'personal' && <Badge variant="success">Mine</Badge>}
          {memory.stale && <Badge variant="warning">Stale</Badge>}
          {type !== UNTYPED && <Badge variant="accent">{type}</Badge>}
          {tags.map(tag => (
            <Badge key={tag} variant="muted">{tag}</Badge>
          ))}
          {extra.slice(0, 3).map(([key, value]) => (
            <Badge key={key} variant="default" className="font-mono">
              {key}: {String(value)}
            </Badge>
          ))}
        </div>
      </td>
      <td className="py-3 px-4 align-top w-28 text-xs text-muted whitespace-nowrap">
        {memory.score !== undefined && (
          <span className="font-mono text-accent block">{memory.score.toFixed(3)}</span>
        )}
        {formatDate(memory.created_at)}
      </td>
      <td className="py-3 px-4 align-top w-16">
        <div className="flex items-center gap-2">
          {memory.stale && (
            <button
              onClick={handleRestore}
              disabled={busy}
              className="text-muted hover:text-accent transition-colors disabled:opacity-50"
              title="Restore"
            >
              <RotateCcw className="w-3.5 h-3.5" />
            </button>
          )}
          <button
            onClick={handleDelete}
            disabled={busy}
            className="text-muted hover:text-danger transition-colors opacity-100 md:opacity-0 md:group-hover:opacity-100 disabled:opacity-50"
            title="Delete"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        </div>
      </td>
    </tr>
  )
}
```

4. In the `Memory` component, add the new state next to the existing hooks:

```tsx
  const organizations = useAppStore((s) => s.organizations)
  const activeOrgId = useAppStore((s) => s.activeOrgId)
  const myRole: OrgRole = organizations.find((o) => o.id === activeOrgId)?.role ?? 'viewer'
  const canConsolidate = ROLE_RANK[myRole] >= ROLE_RANK.admin

  const [scope, setScope] = useState<MemoryScope>('all')
  const [includeStale, setIncludeStale] = useState(false)
  const [formScope, setFormScope] = useState<'project' | 'personal'>('project')
  const [consolidating, setConsolidating] = useState(false)
```

5. Make loading and searching scope-aware:

```tsx
  const loadAll = useCallback(async () => {
    setLoading(true)
    try {
      const data = await api.memory.list(FETCH_LIMIT, scope, includeStale)
      setMemories(data.memories)
      setPageError(null)
    } catch (e) {
      setPageError(String(e))
    } finally {
      setLoading(false)
    }
  }, [scope, includeStale])
```

and in `handleSearch` replace the search call with:

```tsx
      const data = await api.memory.search(query.trim(), 50, scope, includeStale)
```

`useEffect(() => { loadAll() }, [loadAll])` already re-runs when `scope` or
`includeStale` change, because `loadAll` now depends on them.

6. Pass the scope when creating, in `handleCreate`:

```tsx
      await api.memory.create({
        content: trimmedContent,
        metadata: Object.keys(metadata).length > 0 ? metadata : undefined,
        infer: false,
        scope: formScope,
      })
```

7. Add the restore and consolidate handlers next to `handleDelete`:

```tsx
  const handleRestore = async (id: string) => {
    try {
      await api.memory.restore(id)
      toast.success('Memory restored')
      setMemories(prev => prev.map(m => (m.id === id ? { ...m, stale: false } : m)))
    } catch (e) {
      setPageError(String(e))
    }
  }

  const handleConsolidate = async () => {
    setConsolidating(true)
    try {
      const res = await api.memory.consolidate()
      toast.success(
        `Consolidated ${res.scanned} memories: ${res.deduplicated} merged, ${res.marked_stale} marked stale`,
      )
      await loadAll()
    } catch (e) {
      setPageError(String(e))
    } finally {
      setConsolidating(false)
    }
  }
```

8. Add the Consolidate button in the header, next to "Add memory":

```tsx
          <div className="flex items-center gap-2">
            {canConsolidate && (
              <Button variant="secondary" onClick={() => void handleConsolidate()} loading={consolidating} disabled={consolidating}>
                <Sparkles className="w-3.5 h-3.5" /> Consolidate
              </Button>
            )}
            <Button variant={showAddForm ? 'ghost' : 'primary'} onClick={() => setShowAddForm(v => !v)}>
              {showAddForm ? <><X className="w-3.5 h-3.5" /> Cancel</> : <><Plus className="w-3.5 h-3.5" /> Add memory</>}
            </Button>
          </div>
```

(replacing the single `<Button variant={showAddForm ? ...}>` element).

9. Add the scope selector to the add form, inside the
   `grid grid-cols-1 sm:grid-cols-2 gap-3` block, before the Type input:

```tsx
                <Select
                  label="Scope"
                  value={formScope}
                  onValueChange={v => setFormScope(v as 'project' | 'personal')}
                  options={SCOPE_FORM_OPTIONS}
                />
```

10. Add the scope filter and the stale toggle to the Filters row, before the
    existing type `<Select>`:

```tsx
          <Select
            value={scope}
            onValueChange={v => setScope(v as MemoryScope)}
            options={SCOPE_FILTER_OPTIONS}
            className="w-40"
          />
          <label className="flex items-center gap-1.5 text-xs text-muted">
            <input
              type="checkbox"
              checked={includeStale}
              onChange={e => setIncludeStale(e.target.checked)}
            />
            Show stale
          </label>
```

11. Pass the restore handler to each row:

```tsx
                {visibleMemories.map(memory => (
                  <MemoryRow
                    key={memory.id}
                    memory={memory}
                    onDelete={handleDelete}
                    onRestore={handleRestore}
                  />
                ))}
```

- [ ] **Step 4: Type-check**

Run from `services/ui`: `npx tsc --noEmit`
Expected: no output (clean).

- [ ] **Step 5: Build**

Run from `services/ui`: `npm run build`
Expected: build succeeds.

- [ ] **Step 6: Run the UI unit tests**

Run from `services/ui`: `npx vitest run`
Expected: PASS (the existing `src/lib/tenancy.test.ts`).

- [ ] **Step 7: Commit**

```bash
git add services/ui/src/lib/api.ts services/ui/src/pages/Memory.tsx
git commit -m "add memory scope selector and stale actions"
```

---

### Task 8: Documentation, configuration, and full verification

**Files:**
- Modify: `README.md`
- Modify: `.env.example`
- Modify: `docker-compose.yml`

**Interfaces:**
- Consumes everything built in Tasks 1–7. No new code.
- Produces no new symbols; documents `MEMORY_DECAY_DAYS` (default 180) and
  `MEMORY_DECAY_IDLE_DAYS` (default 90), the personal scope, the new tool
  arguments and `POST /api/memory/consolidate`.

- [ ] **Step 1: Update `.env.example`**

Insert this block immediately before the `# ─── Storage ───` section:

```
# ─── Memory ───────────────────────────────────────
# Consolidation runs daily: it merges duplicate memories and marks old, unused
# ones "stale" (hidden from search and list by default, never deleted).
# A memory becomes stale when it is older than MEMORY_DECAY_DAYS and has not
# been returned by a search in the last MEMORY_DECAY_IDLE_DAYS.
MEMORY_DECAY_DAYS=180
MEMORY_DECAY_IDLE_DAYS=90
```

- [ ] **Step 2: Update `docker-compose.yml`**

In the `server` service `environment:` list, after
`- SEARCH_HYBRID=${SEARCH_HYBRID:-true}`, add:

```yaml
      - MEMORY_DECAY_DAYS=${MEMORY_DECAY_DAYS:-180}
      - MEMORY_DECAY_IDLE_DAYS=${MEMORY_DECAY_IDLE_DAYS:-90}
```

- [ ] **Step 3: Update the README features bullet**

Replace the "Persistent memory" bullet (line ~7) with:

```markdown
- **Persistent memory** — long-term memory with Mem0 + pgvector, scoped per organization and project, with a **personal scope** per user (`scope: "personal"`) alongside the shared project one. Usage is tracked, and a daily consolidation job merges duplicates and marks memories that are old and unused as stale so they stop crowding results (nothing is deleted automatically; stale memories can be restored from the Memory page). Tune with `MEMORY_DECAY_DAYS` / `MEMORY_DECAY_IDLE_DAYS`.
```

- [ ] **Step 4: Update the README MCP tools list**

Replace the Memory line under `## MCP tools` with:

```markdown
- **Memory:** `memory_add(content, metadata, infer, scope)`, `memory_search(query, limit, scope, include_stale)`, `memory_list(limit, scope, include_stale)`, `memory_delete` — `scope` is `project` (shared) or `personal` (yours only); search and list default to `all`. Stale memories are hidden unless `include_stale=true`.
```

- [ ] **Step 5: Verify the README has no stale claims**

Run: `grep -n "memory" README.md`
Confirm nothing still describes memory as project-only, and that `askme` appears
nowhere in the files you touched:
Run: `grep -rn "askme" README.md .env.example docker-compose.yml`
Expected: no matches.

- [ ] **Step 6: Run the full server suite**

Run from `services/server`:
`.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`
Expected: PASS — every test, no failures, no errors. Report the count.

- [ ] **Step 7: Run the full UI checks**

Run from `services/ui`:

```bash
npx tsc --noEmit
npm run build
npx vitest run
```

Expected: `tsc` clean, build succeeds, vitest passes.

- [ ] **Step 8: Commit**

```bash
git add README.md .env.example docker-compose.yml
git commit -m "document personal memory and consolidation"
```

---

## Self-Review

**Spec coverage**

| Spec requirement | Task |
|---|---|
| `personal` scope namespace `f"{ns}::u{user_id}"` | 2 |
| API key caller → `mcp_api_keys.created_by` (column verified present) | 2 |
| Anonymous callers rejected with the exact error string | 2, 3 |
| `memory_add(content, metadata=None, infer=True, scope="project")` | 3 |
| `memory_search(query, limit=10, scope="all", include_stale=False)` merging both namespaces and labelling `scope` | 3 |
| `memory_list(limit=20, scope="all", include_stale=False)` | 3 |
| `memory_delete` ownership check across both namespaces | 3 |
| `search_memories(query, limit, scope, include_stale) -> list[dict]` exposed for `context_pack` | 3 |
| Migration `0005_memory_stats` with the exact DDL | 1 |
| Content hash = sha256 of lowercased, punctuation-stripped, whitespace-collapsed text | 1 |
| Hit bump in one `UPDATE ... WHERE memory_id = ANY($1)` | 1, 3 |
| Stale post-filter, fetch `limit * 2` from Mem0 | 3 |
| `memory_delete` removes the stats row | 3, 4 |
| REST `scope` and `include_stale`, restore action | 4 |
| `consolidate_namespace(org_id, namespace) -> dict` with the three counters | 5 |
| Exact dedup keeps oldest, merges hits | 5 |
| Token Jaccard ≥ 0.9 near-duplicates, 2000 cap with warning | 5 |
| Decay via `MEMORY_DECAY_DAYS` / `MEMORY_DECAY_IDLE_DAYS`, never deletes | 5, 6 |
| Backfill of missing stats rows using Mem0's `created_at` | 5 |
| `consolidate_all()` over every namespace | 5 |
| Daily scheduler job | 6 |
| `POST /api/memory/consolidate`, admin/owner | 6 |
| UI scope selector (Project / Mine / All) on list and add form | 7 |
| UI stale badge with Restore and Delete | 7 |
| UI consolidate button | 7 |
| README, `.env.example`, compose | 8 |

**Placeholder scan:** no TODO/TBD, no "similar to Task N", every code step carries
real code, every test step carries real assertions.

**Type consistency:** `search_memories` / `list_memories` /
`search_in_namespaces` / `list_in_namespaces` keep the same names and parameter
order in Tasks 3, 4 and 5. `resolve_scope_namespaces` returns
`list[tuple[str, str]]` everywhere. `consolidate_namespace` returns the same
three keys in Task 5 and is summed with a `namespaces` key by
`consolidate_project` / `consolidate_all`, which Task 6 and Task 7 both consume.
`memory_stats` helper names are identical in Tasks 1, 3, 4 and 5.

**Decisions taken where the spec was silent or in tension**

1. `mcp_api_keys.created_by` already exists and is already populated, so the
   migration adds nothing to that table (the spec asked the implementer to
   verify).
2. `memory_add`'s `infer` default flips from the current `False` to the `True`
   the spec's signature states; the docstring keeps explaining when to pass
   `False`.
3. `consolidate_all()` iterates the union of `memory_stats` namespaces **and**
   every `projects.memory_namespace`, otherwise the backfill could never reach a
   namespace with no stats rows at all.
4. Text normalisation replaces punctuation with a space (so `"a,b"` tokenises as
   two tokens) rather than deleting it, which also feeds the Jaccard tokens.
5. `MEMORY_DECAY_DAYS` / `MEMORY_DECAY_IDLE_DAYS` are deployment-wide `Settings`
   fields, not per-organization overrides, so no Settings-page field is added.
6. The manual consolidate endpoint is gated with `require_project_role("admin")`,
   which resolves to org admin/owner for org-level roles.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-07-memory-personal-consolidation.md`. Two execution options:

1. **Subagent-Driven (recommended)** — a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints.

Which approach?
