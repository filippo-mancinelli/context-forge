# Retrieval Evaluation and Reranker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure retrieval quality with a golden-query evaluation harness, then add an optional cross-encoder reranker whose effect the harness can prove.

**Architecture:** A new `src/eval/` package holds pure metric maths (`metrics.py`) and a runner (`runner.py`) that replays stored golden queries through the existing repo and knowledge-base search, storing runs in two new tables created by migration `0006_eval`. A REST area (`src/api/routes/eval.py`) and a UI page (`pages/RetrievalEval.tsx`) manage queries and runs. A separate `src/reranker.py` reorders the fused candidate list of `search_repo_chunks` / `search_kb_chunks` using a per-organization provider (`none` / `jina` / `local`), failing open to the fused order.

**Tech Stack:** Python 3.11 (FastAPI, asyncpg raw SQL, httpx), React 18 + TypeScript (Vite, Tailwind), PostgreSQL 16 + pgvector.

**Spec:** `docs/superpowers/specs/2026-09-07-retrieval-eval-reranker-design.md`

## Global Constraints

Copied verbatim from `docs/superpowers/specs/2026-09-07-platform-improvements-roadmap.md` ("Global constraints (bind every plan)"). Every task's requirements implicitly include this section.

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

**Additional notes that bind every task here:**

- `httpx>=0.27.0` is already a dependency in `services/server/pyproject.toml`. No new Python dependency is needed by this plan.
- Feature 1 (versioned migrations) lands before this plan, so `services/server/src/migrations/versions/` already exists.
- Feature 2 (HNSW indexes) lands before this plan, so the vector SQL in `src/search.py` already uses `(embedding::vector(N))` casts and sets `hnsw.ef_search`. **Never edit that SQL text.** This plan only changes the *value* passed as the SQL `LIMIT` parameter and post-processes the returned rows.
- Server test command (from `services/server`): `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/<file>`
- UI checks (from `services/ui`): `npx tsc --noEmit` and `npm run build`

---

## File Structure

**Created:**
- `services/server/src/migrations/versions/0006_eval.py` — `eval_queries` + `eval_runs` DDL
- `services/server/src/eval/__init__.py` — package marker
- `services/server/src/eval/metrics.py` — pure metric maths, no I/O
- `services/server/src/eval/runner.py` — replays golden queries, stores runs
- `services/server/src/api/routes/eval.py` — golden-query CRUD, capture, runs
- `services/server/src/reranker.py` — provider abstraction (`none` / `jina` / `local`)
- `services/ui/src/pages/RetrievalEval.tsx` — the eval page
- Tests: `tests/test_eval_migration.py`, `tests/test_eval_metrics.py`, `tests/test_eval_runner.py`, `tests/test_eval_queries_routes.py`, `tests/test_eval_runs_routes.py`, `tests/test_reranker.py`, `tests/test_reranker_settings.py`, `tests/test_search_reranker.py`

**Modified:**
- `services/server/src/api/app.py` — include the eval router
- `services/server/src/config.py` — reranker settings + runtime override fields
- `services/server/src/org_settings.py` — reranker fields on `OrgSettings`
- `services/server/src/api/routes/settings.py` — validate the reranker provider on save
- `services/server/src/search.py` — call the reranker after fusion
- `services/ui/src/lib/api.ts` — eval types and `api.eval`
- `services/ui/src/App.tsx` — nav entry + route, admin/owner gated
- `services/ui/src/pages/Settings.tsx` — "Search" reranker block in the Models tab
- `README.md`, `.env.example`, `docker-compose.yml`

---

### Task 1: Migration `0006_eval` and the pure metrics module

**Files:**
- Create: `services/server/src/migrations/versions/0006_eval.py`
- Create: `services/server/src/eval/__init__.py`
- Create: `services/server/src/eval/metrics.py`
- Test: `services/server/tests/test_eval_migration.py`
- Test: `services/server/tests/test_eval_metrics.py`

**Interfaces:**
- Consumes: the migration module contract from `docs/superpowers/specs/2026-09-07-versioned-migrations-design.md` — each version module in `src/migrations/versions/` exposes `VERSION: int`, `NAME: str`, `TRANSACTIONAL: bool = True`, and `async def upgrade(conn) -> None`. Transactional modules run inside `async with conn.transaction()`; the runner discovers files named `NNNN_<name>.py` and applies them in numeric order.
- Produces:
  - Tables `eval_queries(id, org_id, project_id, query, expected JSONB, notes, created_by, created_at)` and `eval_runs(id, org_id, project_id, started_at, finished_at, config JSONB, metrics JSONB, per_query JSONB, error)`, plus index `eval_runs_project_idx (project_id, started_at DESC)`.
  - `src.eval.metrics.match_rank(results: Sequence[Mapping[str, Any]], expected: Mapping[str, Any]) -> Optional[int]` — 1-based rank of the first result matching an expectation, `None` when absent.
  - `src.eval.metrics.query_metrics(found_ranks: Sequence[Optional[int]]) -> dict[str, float]` — `{"recall_at_5", "recall_at_10", "rr"}`, each rounded to 4 decimals.
  - `src.eval.metrics.macro_average(per_query: Sequence[Mapping[str, float]]) -> dict` — `{"recall_at_5", "recall_at_10", "mrr", "queries", "hits"}`.

- [ ] **Step 1: Write the failing migration test**

Create `services/server/tests/test_eval_migration.py`:

```python
"""Migration 0006 follows the version-module contract and creates the eval tables."""
import asyncio
import importlib


MODULE = "src.migrations.versions.0006_eval"


class _FakeConn:
    def __init__(self):
        self.statements = []

    async def execute(self, sql, *args):
        self.statements.append(sql)
        return "OK"


def _upgrade_sql() -> str:
    module = importlib.import_module(MODULE)
    conn = _FakeConn()
    asyncio.run(module.upgrade(conn))
    return "\n".join(conn.statements)


def test_module_declares_the_version_contract():
    module = importlib.import_module(MODULE)
    assert module.VERSION == 6
    assert module.NAME == "eval"
    assert module.TRANSACTIONAL is True


def test_upgrade_creates_both_eval_tables_and_the_run_index():
    sql = _upgrade_sql()
    assert "CREATE TABLE IF NOT EXISTS eval_queries" in sql
    assert "CREATE TABLE IF NOT EXISTS eval_runs" in sql
    assert "CREATE INDEX IF NOT EXISTS eval_runs_project_idx" in sql
    assert "(project_id, started_at DESC)" in sql


def test_eval_queries_carries_org_project_and_expected_jsonb():
    sql = _upgrade_sql()
    for column in ("org_id", "project_id", "query", "expected", "notes", "created_by", "created_at"):
        assert column in sql, column
    assert "expected    JSONB NOT NULL" in sql


def test_eval_runs_carries_config_metrics_per_query_and_error():
    sql = _upgrade_sql()
    for column in ("config", "metrics", "per_query", "error", "started_at", "finished_at"):
        assert column in sql, column
```

- [ ] **Step 2: Run the test to verify it fails**

From `services/server`:

```bash
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_eval_migration.py
```

Expected: FAIL with `ModuleNotFoundError: No module named 'src.migrations.versions.0006_eval'`.

- [ ] **Step 3: Write the migration module**

Create `services/server/src/migrations/versions/0006_eval.py`:

```python
"""Golden queries and evaluation runs for the retrieval harness.

New schema changes go in a new version module, never in this one.
"""
from __future__ import annotations

VERSION = 6
NAME = "eval"
TRANSACTIONAL = True

_SQL = """
CREATE TABLE IF NOT EXISTS eval_queries (
    id          BIGSERIAL PRIMARY KEY,
    org_id      BIGINT NOT NULL,
    project_id  BIGINT NOT NULL,
    query       TEXT NOT NULL,
    expected    JSONB NOT NULL,
    notes       TEXT,
    created_by  BIGINT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS eval_runs (
    id          BIGSERIAL PRIMARY KEY,
    org_id      BIGINT NOT NULL,
    project_id  BIGINT NOT NULL,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    config      JSONB NOT NULL,
    metrics     JSONB,
    per_query   JSONB,
    error       TEXT
);

CREATE INDEX IF NOT EXISTS eval_runs_project_idx ON eval_runs (project_id, started_at DESC);
"""


async def upgrade(conn) -> None:
    await conn.execute(_SQL)
```

- [ ] **Step 4: Run the migration test to verify it passes**

```bash
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_eval_migration.py
```

Expected: PASS (4 tests).

- [ ] **Step 5: Write the failing metrics test**

Create `services/server/tests/test_eval_metrics.py`:

```python
"""Metric maths for the retrieval evaluation harness (pure, no I/O)."""
from src.eval import metrics


def test_match_rank_finds_a_repo_expectation_by_repo_and_path():
    results = [
        {"repo_name": "api", "file_path": "src/a.py"},
        {"repo_name": "api", "file_path": "src/b.py"},
    ]
    assert metrics.match_rank(results, {"kind": "repo", "repo": "api", "path": "src/b.py"}) == 2
    assert metrics.match_rank(results, {"kind": "repo", "repo": "api", "path": "src/z.py"}) is None
    assert metrics.match_rank(results, {"kind": "repo", "repo": "ui", "path": "src/a.py"}) is None


def test_match_rank_finds_a_kb_expectation_by_document_id():
    results = [{"document_id": 5}, {"document_id": 12}, {"document_id": 12}]
    assert metrics.match_rank(results, {"kind": "kb", "document_id": 12}) == 2
    assert metrics.match_rank(results, {"kind": "kb", "document_id": 99}) is None


def test_match_rank_ignores_an_unknown_kind():
    assert metrics.match_rank([{"repo_name": "a", "file_path": "b"}], {"kind": "web"}) is None


def test_query_metrics_counts_recall_within_each_window():
    out = metrics.query_metrics([1, 7, None])
    assert out["recall_at_5"] == 0.3333
    assert out["recall_at_10"] == 0.6667
    assert out["rr"] == 1.0


def test_query_metrics_reciprocal_rank_uses_the_first_hit():
    assert metrics.query_metrics([9, 4])["rr"] == 0.25


def test_query_metrics_without_any_hit_is_zero():
    assert metrics.query_metrics([None, None]) == {
        "recall_at_5": 0.0, "recall_at_10": 0.0, "rr": 0.0
    }


def test_query_metrics_with_no_expectations_is_zero():
    assert metrics.query_metrics([]) == {
        "recall_at_5": 0.0, "recall_at_10": 0.0, "rr": 0.0
    }


def test_macro_average_is_the_mean_over_queries():
    per_query = [
        {"recall_at_5": 1.0, "recall_at_10": 1.0, "rr": 1.0},
        {"recall_at_5": 0.0, "recall_at_10": 0.5, "rr": 0.0},
    ]
    assert metrics.macro_average(per_query) == {
        "recall_at_5": 0.5, "recall_at_10": 0.75, "mrr": 0.5, "queries": 2, "hits": 1
    }


def test_macro_average_of_nothing_is_zero():
    assert metrics.macro_average([]) == {
        "recall_at_5": 0.0, "recall_at_10": 0.0, "mrr": 0.0, "queries": 0, "hits": 0
    }
```

- [ ] **Step 6: Run the metrics test to verify it fails**

```bash
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_eval_metrics.py
```

Expected: FAIL with `ModuleNotFoundError: No module named 'src.eval'`.

- [ ] **Step 7: Write the metrics module**

Create `services/server/src/eval/__init__.py`:

```python
"""Retrieval evaluation harness: golden queries, metrics, and runs."""
```

Create `services/server/src/eval/metrics.py`:

```python
"""Retrieval metrics over golden-query expectations.

Pure functions only: they take result lists and rank positions, never touch the
database or the search layer, so they can be tested in isolation.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

ZERO_QUERY = {"recall_at_5": 0.0, "recall_at_10": 0.0, "rr": 0.0}
ZERO_RUN = {"recall_at_5": 0.0, "recall_at_10": 0.0, "mrr": 0.0, "queries": 0, "hits": 0}


def match_rank(
    results: Sequence[Mapping[str, Any]], expected: Mapping[str, Any]
) -> Optional[int]:
    """1-based rank of the first result satisfying an expectation, else None."""
    kind = expected.get("kind")
    for index, result in enumerate(results, start=1):
        if kind == "repo":
            if (
                result.get("repo_name") == expected.get("repo")
                and result.get("file_path") == expected.get("path")
            ):
                return index
        elif kind == "kb":
            document_id = result.get("document_id")
            if document_id is not None and int(document_id) == int(expected["document_id"]):
                return index
    return None


def query_metrics(found_ranks: Sequence[Optional[int]]) -> dict[str, float]:
    """recall@5, recall@10 and reciprocal rank for one query's expected items."""
    total = len(found_ranks)
    if total == 0:
        return dict(ZERO_QUERY)
    hits = [rank for rank in found_ranks if rank is not None]
    best = min(hits) if hits else None
    return {
        "recall_at_5": round(sum(1 for r in hits if r <= 5) / total, 4),
        "recall_at_10": round(sum(1 for r in hits if r <= 10) / total, 4),
        "rr": round(1 / best, 4) if best else 0.0,
    }
```

Append the macro average:

```python
def macro_average(per_query: Sequence[Mapping[str, float]]) -> dict:
    """Unweighted mean of the per-query metrics, plus query and hit counts."""
    total = len(per_query)
    if total == 0:
        return dict(ZERO_RUN)
    return {
        "recall_at_5": round(sum(q["recall_at_5"] for q in per_query) / total, 4),
        "recall_at_10": round(sum(q["recall_at_10"] for q in per_query) / total, 4),
        "mrr": round(sum(q["rr"] for q in per_query) / total, 4),
        "queries": total,
        "hits": sum(1 for q in per_query if q["rr"] > 0),
    }
```

- [ ] **Step 8: Run both tests to verify they pass**

```bash
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_eval_migration.py tests/test_eval_metrics.py
```

Expected: PASS (13 tests).

- [ ] **Step 9: Commit**

```bash
git add services/server/src/migrations/versions/0006_eval.py services/server/src/eval services/server/tests/test_eval_migration.py services/server/tests/test_eval_metrics.py
git commit -m "add eval schema and retrieval metrics"
```

---

### Task 2: Evaluation runner

**Files:**
- Create: `services/server/src/eval/runner.py`
- Test: `services/server/tests/test_eval_runner.py`

**Interfaces:**
- Consumes (all already exist):
  - `src.eval.metrics.match_rank(results, expected) -> Optional[int]`, `query_metrics(found_ranks) -> {"recall_at_5","recall_at_10","rr"}`, `macro_average(per_query) -> {"recall_at_5","recall_at_10","mrr","queries","hits"}`.
  - `src.search.search_repo_chunks(org_id: int, query: str, repos=None, limit: int = 10, project_id: int | None = None) -> list[dict]` where each dict has `repo_name`, `file_path`, `chunk_type`, `content`, `metadata`, `score`.
  - `src.search.search_kb_chunks(org_id: int, query: str, document_ids=None, limit: int = 10, project_id: int | None = None) -> list[dict]` where each dict has `document_id`, `title`, `filename`, `extension`, `chunk_index`, `content`, `metadata`, `score`.
  - `src.search.hybrid_enabled() -> bool`.
  - `src.org_settings.get_org_settings(org_id: int) -> OrgSettings` with `embeddings_model: str` and `embeddings_dims: int`. The reranker fields (`reranker_provider`, `reranker_model`) are added in Task 6, so this task reads them with `getattr(settings, "reranker_provider", "none")` — that keeps working unchanged once Task 6 adds the real fields.
  - `src.db.get_pool()` (asyncpg pool; `async with pool.acquire() as conn`).
  - Tables `eval_queries` and `eval_runs` from Task 1.
- Produces:
  - `src.eval.runner.create_run(org_id: int, project_id: int, k: int) -> int` — inserts the `eval_runs` row with `finished_at = NULL` and returns its id.
  - `src.eval.runner.execute_run(run_id: int, org_id: int, project_id: int, k: int) -> None` — replays the golden queries and updates the row.
  - `src.eval.runner.run_eval(org_id: int, project_id: int, k: int = 10) -> int` — creates the row, schedules `execute_run` with `asyncio.create_task`, returns the run id immediately.

- [ ] **Step 1: Write the failing test**

Create `services/server/tests/test_eval_runner.py`:

```python
"""Evaluation runner: run row creation, metric storage and error capture."""
import asyncio
import json
from types import SimpleNamespace

from src.eval import runner


class _FakeConn:
    def __init__(self, query_rows, run_id=99):
        self.query_rows = query_rows
        self.run_id = run_id
        self.calls = []

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", " ".join(sql.split()), args))
        return self.run_id

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", " ".join(sql.split()), args))
        return self.query_rows

    async def execute(self, sql, *args):
        self.calls.append(("execute", " ".join(sql.split()), args))
        return "OK"


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


def _install(monkeypatch, query_rows):
    conn = _FakeConn(query_rows)

    async def fake_pool():
        return _FakePool(conn)

    async def fake_settings(org_id):
        return SimpleNamespace(embeddings_model="text-embedding-3-small", embeddings_dims=1536)

    monkeypatch.setattr(runner, "get_pool", fake_pool)
    monkeypatch.setattr(runner, "get_org_settings", fake_settings)
    monkeypatch.setattr(runner, "hybrid_enabled", lambda: True)
    return conn


def _last(conn, kind):
    return [c for c in conn.calls if c[0] == kind][-1]
```

Append the tests:

```python
def test_create_run_records_the_effective_config(monkeypatch):
    conn = _install(monkeypatch, [])
    run_id = asyncio.run(runner.create_run(1, 7, 10))
    assert run_id == 99
    kind, sql, args = _last(conn, "fetchval")
    assert "INSERT INTO eval_runs" in sql
    assert args[0] == 1 and args[1] == 7
    config = json.loads(args[2])
    assert config == {
        "embeddings_model": "text-embedding-3-small",
        "embeddings_dims": 1536,
        "hybrid": True,
        "reranker_provider": "none",
        "reranker_model": "",
        "k": 10,
    }


def test_execute_run_stores_metrics_and_per_query(monkeypatch):
    rows = [
        {"id": 1, "query": "where is the scheduler",
         "expected": json.dumps([{"kind": "repo", "repo": "api", "path": "src/b.py"},
                                 {"kind": "repo", "repo": "api", "path": "src/gone.py"}])},
        {"id": 2, "query": "holiday policy",
         "expected": [{"kind": "kb", "document_id": 12}]},
    ]
    conn = _install(monkeypatch, rows)

    async def fake_repo(org_id, query, repos=None, limit=10, project_id=None):
        return [{"repo_name": "api", "file_path": "src/a.py", "content": "a"},
                {"repo_name": "api", "file_path": "src/b.py", "content": "b"}]

    async def fake_kb(org_id, query, document_ids=None, limit=10, project_id=None):
        return [{"document_id": 12, "content": "k"}]

    monkeypatch.setattr(runner, "search_repo_chunks", fake_repo)
    monkeypatch.setattr(runner, "search_kb_chunks", fake_kb)

    asyncio.run(runner.execute_run(99, 1, 7, 10))

    kind, sql, args = _last(conn, "execute")
    assert "UPDATE eval_runs" in sql and "finished_at = NOW()" in sql
    assert args[0] == 99
    stored_metrics = json.loads(args[1])
    per_query = json.loads(args[2])
    assert stored_metrics["queries"] == 2 and stored_metrics["hits"] == 2
    assert stored_metrics["recall_at_10"] == 0.75
    assert stored_metrics["mrr"] == 0.75
    assert per_query[0]["query_id"] == 1
    assert per_query[0]["found"] == [{"kind": "repo", "repo": "api", "path": "src/b.py"}]
    assert per_query[0]["missed"] == [{"kind": "repo", "repo": "api", "path": "src/gone.py"}]
    assert per_query[1]["rr"] == 1.0


def test_execute_run_records_the_search_error(monkeypatch):
    rows = [{"id": 1, "query": "boom", "expected": [{"kind": "repo", "repo": "a", "path": "b"}]}]
    conn = _install(monkeypatch, rows)

    async def boom(*args, **kwargs):
        raise RuntimeError("embeddings unavailable")

    monkeypatch.setattr(runner, "search_repo_chunks", boom)
    asyncio.run(runner.execute_run(99, 1, 7, 10))

    kind, sql, args = _last(conn, "execute")
    assert "error = $2" in sql
    assert args[0] == 99
    assert "embeddings unavailable" in args[1]


def test_run_eval_returns_the_id_and_schedules_the_work(monkeypatch):
    _install(monkeypatch, [])
    started = {}

    async def fake_execute(run_id, org_id, project_id, k):
        started.update(run_id=run_id, org_id=org_id, project_id=project_id, k=k)

    monkeypatch.setattr(runner, "execute_run", fake_execute)

    async def go():
        run_id = await runner.run_eval(1, 7, k=10)
        await asyncio.sleep(0)
        return run_id

    assert asyncio.run(go()) == 99
    assert started == {"run_id": 99, "org_id": 1, "project_id": 7, "k": 10}
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_eval_runner.py
```

Expected: FAIL with `ModuleNotFoundError: No module named 'src.eval.runner'`.

- [ ] **Step 3: Write the runner**

Create `services/server/src/eval/runner.py`:

```python
"""Replays the golden queries of a project and stores the resulting metrics.

Repo expectations are checked against the repository search, knowledge-base
expectations against the kb search, both with the run's ``k`` as the limit. The
run row is written before the work starts so the UI can poll it.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from ..db import get_pool
from ..org_settings import get_org_settings
from ..search import hybrid_enabled, search_kb_chunks, search_repo_chunks
from .metrics import macro_average, match_rank, query_metrics

logger = logging.getLogger(__name__)

_background_tasks: set = set()

_INSERT_RUN_SQL = """
INSERT INTO eval_runs (org_id, project_id, config)
VALUES ($1, $2, $3::jsonb)
RETURNING id
"""

_SELECT_QUERIES_SQL = """
SELECT id, query, expected
FROM eval_queries
WHERE org_id = $1 AND project_id = $2
ORDER BY id
"""

_FINISH_RUN_SQL = """
UPDATE eval_runs
SET finished_at = NOW(), metrics = $2::jsonb, per_query = $3::jsonb
WHERE id = $1
"""

_FAIL_RUN_SQL = """
UPDATE eval_runs
SET finished_at = NOW(), error = $2
WHERE id = $1
"""


def _as_list(value: Any) -> list:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:  # noqa: BLE001
            return []
    return list(value or [])
```

Append the config resolution and run creation:

```python
async def effective_config(org_id: int, k: int) -> dict:
    """The retrieval configuration this run is measuring."""
    settings = await get_org_settings(org_id)
    return {
        "embeddings_model": settings.embeddings_model,
        "embeddings_dims": int(settings.embeddings_dims),
        "hybrid": bool(hybrid_enabled()),
        # The reranker fields land with the reranker task; default until then.
        "reranker_provider": getattr(settings, "reranker_provider", "none") or "none",
        "reranker_model": getattr(settings, "reranker_model", "") or "",
        "k": int(k),
    }


async def create_run(org_id: int, project_id: int, k: int) -> int:
    """Insert an unfinished run row and return its id."""
    config = await effective_config(org_id, k)
    pool = await get_pool()
    async with pool.acquire() as conn:
        run_id = await conn.fetchval(
            _INSERT_RUN_SQL, org_id, project_id, json.dumps(config)
        )
    return int(run_id)
```

Append the query replay:

```python
async def _score_query(org_id: int, project_id: int, expected: list[dict], k: int) -> dict:
    """Ranks of every expectation of one query, split into found and missed."""
    repo_expected = [e for e in expected if e.get("kind") == "repo"]
    kb_expected = [e for e in expected if e.get("kind") == "kb"]
    repo_results = (
        await search_repo_chunks(org_id, _score_query.query, limit=k, project_id=project_id)
        if repo_expected else []
    )
    kb_results = (
        await search_kb_chunks(org_id, _score_query.query, limit=k, project_id=project_id)
        if kb_expected else []
    )
    ranks, found, missed = [], [], []
    for item in expected:
        results = repo_results if item.get("kind") == "repo" else kb_results
        rank = match_rank(results, item)
        ranks.append(rank)
        (found if rank is not None else missed).append(item)
    return {"ranks": ranks, "found": found, "missed": missed}
```

That draft leaks the query through an attribute; replace it with an explicit parameter — write `_score_query` as:

```python
async def _score_query(
    org_id: int, project_id: int, query: str, expected: list[dict], k: int
) -> dict:
    """Ranks of every expectation of one query, split into found and missed."""
    repo_expected = any(e.get("kind") == "repo" for e in expected)
    kb_expected = any(e.get("kind") == "kb" for e in expected)
    repo_results = (
        await search_repo_chunks(org_id, query, limit=k, project_id=project_id)
        if repo_expected else []
    )
    kb_results = (
        await search_kb_chunks(org_id, query, limit=k, project_id=project_id)
        if kb_expected else []
    )
    ranks, found, missed = [], [], []
    for item in expected:
        results = repo_results if item.get("kind") == "repo" else kb_results
        rank = match_rank(results, item)
        ranks.append(rank)
        (found if rank is not None else missed).append(item)
    return {"ranks": ranks, "found": found, "missed": missed}
```

Append the execution and the entry point:

```python
async def execute_run(run_id: int, org_id: int, project_id: int, k: int) -> None:
    """Replay every golden query and finish the run row (or record the error)."""
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(_SELECT_QUERIES_SQL, org_id, project_id)
        per_query = []
        for row in rows:
            expected = _as_list(row["expected"])
            scored = await _score_query(org_id, project_id, row["query"], expected, k)
            entry = {"query_id": int(row["id"]), "query": row["query"]}
            entry.update(query_metrics(scored["ranks"]))
            entry["found"] = scored["found"]
            entry["missed"] = scored["missed"]
            per_query.append(entry)
        metrics = macro_average(per_query)
        async with pool.acquire() as conn:
            await conn.execute(
                _FINISH_RUN_SQL, run_id, json.dumps(metrics), json.dumps(per_query)
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("eval run %s failed (org=%s project=%s)", run_id, org_id, project_id)
        async with pool.acquire() as conn:
            await conn.execute(_FAIL_RUN_SQL, run_id, str(exc))


async def run_eval(org_id: int, project_id: int, k: int = 10) -> int:
    """Create the run row, start the replay in the background, return the id."""
    run_id = await create_run(org_id, project_id, k)
    task = asyncio.create_task(execute_run(run_id, org_id, project_id, k))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return run_id
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_eval_runner.py
```

Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add services/server/src/eval/runner.py services/server/tests/test_eval_runner.py
git commit -m "add retrieval eval runner"
```

---

### Task 3: REST golden queries and candidate capture

**Files:**
- Create: `services/server/src/api/routes/eval.py`
- Modify: `services/server/src/api/app.py` (add the import next to the other `from .routes import ...` lines and the `api.include_router(...)` call next to the others)
- Test: `services/server/tests/test_eval_queries_routes.py`

**Interfaces:**
- Consumes (all already exist):
  - `src.api.deps.ActiveOrg(org_id, role, namespace, name)`, `src.api.deps.ActiveProject(org_id, project_id, role, namespace, name, org_name)`.
  - `src.api.deps.get_active_project` — resolves the project from the `X-Project-Id` header (falling back to the org's default project) and enforces project access.
  - `src.api.deps.require_role(minimum: str)` — dependency factory returning an `ActiveOrg`, 403 when the caller's org role is below `minimum`.
  - `src.api.deps.get_current_user_id` — the authenticated user id.
  - `src.db.get_pool()`.
  - `src.search.search_repo_chunks` / `src.search.search_kb_chunks` (signatures in Task 2's Interfaces).
  - Table `eval_queries` from Task 1.
- Produces:
  - Router `src.api.routes.eval.router` with prefix `/eval`, mounted under `/api`.
  - `GET /api/eval/queries` → `{"queries": [...], "count": n}`
  - `POST /api/eval/queries` (201) → the created query
  - `PUT /api/eval/queries/{query_id}` → the updated query
  - `DELETE /api/eval/queries/{query_id}` → `{"status": "ok", "deleted": id}`
  - `POST /api/eval/queries/capture` `{query}` → `{"candidates": [...], "count": n}`
  - Query shape: `{"id", "query", "expected": [...], "notes", "created_at"}`
  - Candidate shape: `{"kind", "label", "snippet", "score", "expected"}`
  - Module-level `_require_org_admin = require_role("admin")` reused by Task 4's routes in the same file.

- [ ] **Step 1: Write the failing test**

Create `services/server/tests/test_eval_queries_routes.py`:

```python
"""Golden-query CRUD and capture: role gating, project scoping, payload shape."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src import search
from src.api import deps
from src.api.routes import eval as eval_routes

ORG_ADMIN = deps.ActiveOrg(org_id=1, role="admin", namespace="acme", name="Acme")
ORG_MEMBER = deps.ActiveOrg(org_id=1, role="member", namespace="acme", name="Acme")
PROJECT = deps.ActiveProject(
    org_id=1, project_id=7, role="admin", namespace="acme--p", name="P", org_name="Acme"
)


class _FakeConn:
    def __init__(self, row=None, rows=None, fetchval=1):
        self.row = row
        self.rows = rows or []
        self._fetchval = fetchval
        self.calls = []

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", " ".join(sql.split()), args))
        return self.rows

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", " ".join(sql.split()), args))
        return self.row

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", " ".join(sql.split()), args))
        return self._fetchval


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


def _row(query_id=1, query="where is the scheduler", expected=None, notes=None):
    return {
        "id": query_id,
        "query": query,
        "expected": json.dumps(expected if expected is not None else [
            {"kind": "repo", "repo": "api", "path": "src/a.py"}
        ]),
        "notes": notes,
        "created_at": None,
    }


@pytest.fixture
def client(monkeypatch):
    conn = _FakeConn(row=_row(), rows=[_row()])

    async def fake_pool():
        return _FakePool(conn)

    monkeypatch.setattr(eval_routes, "get_pool", fake_pool)

    app = FastAPI()
    app.include_router(eval_routes.router, prefix="/api")
    app.dependency_overrides[deps.get_active_org] = lambda: ORG_ADMIN
    app.dependency_overrides[deps.get_active_project] = lambda: PROJECT
    app.dependency_overrides[deps.get_current_user_id] = lambda: 42
    return TestClient(app), conn
```

Append the tests:

```python
def test_list_queries_is_scoped_to_org_and_project(client):
    tc, conn = client
    resp = tc.get("/api/eval/queries")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 1
    assert body["queries"][0]["expected"] == [
        {"kind": "repo", "repo": "api", "path": "src/a.py"}
    ]
    kind, sql, args = conn.calls[-1]
    assert "FROM eval_queries" in sql
    assert args == (1, 7)


def test_create_query_stores_expected_and_the_author(client):
    tc, conn = client
    resp = tc.post("/api/eval/queries", json={
        "query": "holiday policy",
        "expected": [{"kind": "kb", "document_id": 12}],
        "notes": "from support",
    })
    assert resp.status_code == 201, resp.text
    insert = [c for c in conn.calls if "INSERT INTO eval_queries" in c[1]][0]
    args = insert[2]
    assert args[0] == 1 and args[1] == 7
    assert args[2] == "holiday policy"
    assert json.loads(args[3]) == [{"kind": "kb", "document_id": 12}]
    assert args[5] == 42


def test_create_query_rejects_an_incomplete_repo_expectation(client):
    tc, _ = client
    resp = tc.post("/api/eval/queries", json={
        "query": "q", "expected": [{"kind": "repo", "repo": "api"}]
    })
    assert resp.status_code == 400
    assert "path" in resp.json()["detail"]


def test_create_query_rejects_an_unknown_kind(client):
    tc, _ = client
    resp = tc.post("/api/eval/queries", json={"query": "q", "expected": [{"kind": "web"}]})
    assert resp.status_code == 400


def test_update_query_returns_404_when_it_belongs_elsewhere(client, monkeypatch):
    tc, conn = client
    conn.row = None
    resp = tc.put("/api/eval/queries/5", json={"query": "q", "expected": []})
    assert resp.status_code == 404


def test_delete_query_is_scoped(client):
    tc, conn = client
    resp = tc.delete("/api/eval/queries/5")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "deleted": 5}
    kind, sql, args = conn.calls[-1]
    assert "DELETE FROM eval_queries" in sql
    assert args == (5, 1, 7)


def test_capture_returns_repo_and_kb_candidates_with_ready_expectations(client, monkeypatch):
    tc, _ = client

    async def fake_repo(org_id, query, repos=None, limit=10, project_id=None):
        assert (org_id, project_id, limit) == (1, 7, 10)
        return [{"repo_name": "api", "file_path": "src/a.py", "content": "def a():", "score": 1.0}]

    async def fake_kb(org_id, query, document_ids=None, limit=10, project_id=None):
        return [{"document_id": 12, "title": "Handbook", "filename": "h.pdf",
                 "content": "holidays", "score": 0.8}]

    monkeypatch.setattr(search, "search_repo_chunks", fake_repo)
    monkeypatch.setattr(search, "search_kb_chunks", fake_kb)

    resp = tc.post("/api/eval/queries/capture", json={"query": "holidays"})
    assert resp.status_code == 200, resp.text
    candidates = resp.json()["candidates"]
    assert candidates[0]["expected"] == {"kind": "repo", "repo": "api", "path": "src/a.py"}
    assert candidates[0]["label"] == "api:src/a.py"
    assert candidates[1]["expected"] == {"kind": "kb", "document_id": 12}
    assert candidates[1]["label"] == "Handbook"


def test_members_cannot_reach_the_eval_area(client):
    tc, _ = client
    tc.app.dependency_overrides[deps.get_active_org] = lambda: ORG_MEMBER
    assert tc.get("/api/eval/queries").status_code == 403
    assert tc.post("/api/eval/queries", json={"query": "q", "expected": []}).status_code == 403
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_eval_queries_routes.py
```

Expected: FAIL with `ImportError: cannot import name 'eval' from 'src.api.routes'`.

- [ ] **Step 3: Write the route module**

Create `services/server/src/api/routes/eval.py`:

```python
"""Retrieval evaluation: golden queries, candidate capture, and runs.

Org admin/owner only; the project comes from the ``X-Project-Id`` header
through ``get_active_project``.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...db import get_pool
from ..deps import (
    ActiveOrg,
    ActiveProject,
    get_active_project,
    get_current_user_id,
    require_role,
)

router = APIRouter(prefix="/eval", tags=["eval"])

_require_org_admin = require_role("admin")

EXPECTED_KINDS = ("repo", "kb")

_QUERY_COLUMNS = "id, query, expected, notes, created_at"


class ExpectedItem(BaseModel):
    kind: str
    repo: Optional[str] = None
    path: Optional[str] = None
    document_id: Optional[int] = None


class QueryRequest(BaseModel):
    query: str = Field(min_length=1)
    expected: list[ExpectedItem] = Field(default_factory=list)
    notes: Optional[str] = None


class CaptureRequest(BaseModel):
    query: str = Field(min_length=1)


def _validate_expected(items: list[ExpectedItem]) -> list[dict]:
    """Normalize the expectations, rejecting incomplete or unknown kinds."""
    clean: list[dict] = []
    for item in items:
        if item.kind == "repo":
            if not item.repo or not item.path:
                raise HTTPException(
                    status_code=400, detail="A repo expectation needs 'repo' and 'path'"
                )
            clean.append({"kind": "repo", "repo": item.repo, "path": item.path})
        elif item.kind == "kb":
            if item.document_id is None:
                raise HTTPException(
                    status_code=400, detail="A kb expectation needs 'document_id'"
                )
            clean.append({"kind": "kb", "document_id": int(item.document_id)})
        else:
            raise HTTPException(
                status_code=400, detail=f"kind must be one of {', '.join(EXPECTED_KINDS)}"
            )
    return clean


def _json(value: Any, fallback):
    if value is None:
        return fallback
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:  # noqa: BLE001
            return fallback
    return value


def _query_out(row) -> dict:
    return {
        "id": int(row["id"]),
        "query": row["query"],
        "expected": _json(row["expected"], []),
        "notes": row["notes"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }
```

Append the CRUD routes:

```python
@router.get("/queries")
async def list_queries(
    project: ActiveProject = Depends(get_active_project),
    _org: ActiveOrg = Depends(_require_org_admin),
):
    """Golden queries of the active project."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {_QUERY_COLUMNS} FROM eval_queries "
            "WHERE org_id = $1 AND project_id = $2 ORDER BY id",
            project.org_id, project.project_id,
        )
    queries = [_query_out(r) for r in rows]
    return {"queries": queries, "count": len(queries)}


@router.post("/queries", status_code=201)
async def create_query(
    req: QueryRequest,
    project: ActiveProject = Depends(get_active_project),
    _org: ActiveOrg = Depends(_require_org_admin),
    user_id: int = Depends(get_current_user_id),
):
    """Add a golden query with its expected results."""
    expected = _validate_expected(req.expected)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO eval_queries (org_id, project_id, query, expected, notes, created_by) "
            f"VALUES ($1, $2, $3, $4::jsonb, $5, $6) RETURNING {_QUERY_COLUMNS}",
            project.org_id, project.project_id, req.query,
            json.dumps(expected), req.notes, user_id,
        )
    return _query_out(row)


@router.put("/queries/{query_id}")
async def update_query(
    query_id: int,
    req: QueryRequest,
    project: ActiveProject = Depends(get_active_project),
    _org: ActiveOrg = Depends(_require_org_admin),
):
    """Replace a golden query's text, expectations and notes."""
    expected = _validate_expected(req.expected)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE eval_queries SET query = $4, expected = $5::jsonb, notes = $6 "
            f"WHERE id = $1 AND org_id = $2 AND project_id = $3 RETURNING {_QUERY_COLUMNS}",
            query_id, project.org_id, project.project_id,
            req.query, json.dumps(expected), req.notes,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Golden query not found")
    return _query_out(row)


@router.delete("/queries/{query_id}")
async def delete_query(
    query_id: int,
    project: ActiveProject = Depends(get_active_project),
    _org: ActiveOrg = Depends(_require_org_admin),
):
    """Delete a golden query."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        deleted = await conn.fetchval(
            "DELETE FROM eval_queries WHERE id = $1 AND org_id = $2 AND project_id = $3 "
            "RETURNING id",
            query_id, project.org_id, project.project_id,
        )
    if deleted is None:
        raise HTTPException(status_code=404, detail="Golden query not found")
    return {"status": "ok", "deleted": int(deleted)}
```

Append the capture route (the deferred import lets tests monkeypatch `src.search`):

```python
@router.post("/queries/capture")
async def capture_candidates(
    req: CaptureRequest,
    project: ActiveProject = Depends(get_active_project),
    _org: ActiveOrg = Depends(_require_org_admin),
):
    """Top repo and kb hits for a query, each with a ready-made expectation."""
    from ...search import search_kb_chunks, search_repo_chunks

    repo_hits = await search_repo_chunks(
        project.org_id, req.query, limit=10, project_id=project.project_id
    )
    kb_hits = await search_kb_chunks(
        project.org_id, req.query, limit=10, project_id=project.project_id
    )
    candidates = [
        {
            "kind": "repo",
            "label": f"{r['repo_name']}:{r['file_path']}",
            "snippet": (r.get("content") or "")[:200],
            "score": r.get("score"),
            "expected": {"kind": "repo", "repo": r["repo_name"], "path": r["file_path"]},
        }
        for r in repo_hits
    ] + [
        {
            "kind": "kb",
            "label": r.get("title") or r.get("filename") or f"document {r['document_id']}",
            "snippet": (r.get("content") or "")[:200],
            "score": r.get("score"),
            "expected": {"kind": "kb", "document_id": int(r["document_id"])},
        }
        for r in kb_hits
    ]
    return {"candidates": candidates, "count": len(candidates)}
```

- [ ] **Step 4: Register the router**

In `services/server/src/api/app.py`, add the import after `from .routes import projects as projects_routes`:

```python
from .routes import eval as eval_routes
```

and the include after `api.include_router(projects_routes.router, prefix="/api")`:

```python
api.include_router(eval_routes.router, prefix="/api")
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_eval_queries_routes.py
```

Expected: PASS (8 tests).

- [ ] **Step 6: Commit**

```bash
git add services/server/src/api/routes/eval.py services/server/src/api/app.py services/server/tests/test_eval_queries_routes.py
git commit -m "add eval golden query routes"
```

---

### Task 4: REST evaluation runs

**Files:**
- Modify: `services/server/src/api/routes/eval.py` (append the run routes to the file created in Task 3)
- Test: `services/server/tests/test_eval_runs_routes.py`

**Interfaces:**
- Consumes:
  - From Task 3, already in this file: `router` (prefix `/eval`), `_require_org_admin = require_role("admin")`, `_json(value, fallback)`, and the imports `get_pool`, `ActiveOrg`, `ActiveProject`, `get_active_project`, `HTTPException`, `BaseModel`.
  - From Task 2: `src.eval.runner.run_eval(org_id: int, project_id: int, k: int = 10) -> int` — creates the run row, schedules the replay, returns the id immediately.
  - Table `eval_runs(id, org_id, project_id, started_at, finished_at, config, metrics, per_query, error)` from Task 1.
- Produces:
  - `POST /api/eval/runs` `{"k": 10}` → `{"run_id": id}` (`k` clamped to 1..50)
  - `GET /api/eval/runs?limit=20` → `{"runs": [...], "count": n}` (no `per_query`)
  - `GET /api/eval/runs/{run_id}` → the run including `per_query`
  - Run shape: `{"id", "started_at", "finished_at", "config", "metrics", "error"}`, detail adds `"per_query"`.

- [ ] **Step 1: Write the failing test**

Create `services/server/tests/test_eval_runs_routes.py`:

```python
"""Evaluation run routes: start, list, detail, scoping and role gating."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import deps
from src.api.routes import eval as eval_routes

ORG_ADMIN = deps.ActiveOrg(org_id=1, role="admin", namespace="acme", name="Acme")
ORG_MEMBER = deps.ActiveOrg(org_id=1, role="member", namespace="acme", name="Acme")
PROJECT = deps.ActiveProject(
    org_id=1, project_id=7, role="admin", namespace="acme--p", name="P", org_name="Acme"
)

CONFIG = {"embeddings_model": "text-embedding-3-small", "embeddings_dims": 1536,
          "hybrid": True, "reranker_provider": "none", "reranker_model": "", "k": 10}
METRICS = {"recall_at_5": 0.5, "recall_at_10": 0.75, "mrr": 0.5, "queries": 2, "hits": 1}
PER_QUERY = [{"query_id": 1, "query": "q", "recall_at_5": 1.0, "recall_at_10": 1.0,
              "rr": 1.0, "found": [], "missed": []}]


class _FakeConn:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []
        self.calls = []

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", " ".join(sql.split()), args))
        return self.rows

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", " ".join(sql.split()), args))
        return self.row


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


def _run_row(with_detail=False):
    row = {
        "id": 3,
        "started_at": None,
        "finished_at": None,
        "config": json.dumps(CONFIG),
        "metrics": json.dumps(METRICS),
        "error": None,
    }
    if with_detail:
        row["per_query"] = json.dumps(PER_QUERY)
    return row


@pytest.fixture
def client(monkeypatch):
    conn = _FakeConn(row=_run_row(with_detail=True), rows=[_run_row()])
    started = {}

    async def fake_pool():
        return _FakePool(conn)

    async def fake_run_eval(org_id, project_id, k=10):
        started.update(org_id=org_id, project_id=project_id, k=k)
        return 3

    monkeypatch.setattr(eval_routes, "get_pool", fake_pool)
    monkeypatch.setattr("src.eval.runner.run_eval", fake_run_eval)

    app = FastAPI()
    app.include_router(eval_routes.router, prefix="/api")
    app.dependency_overrides[deps.get_active_org] = lambda: ORG_ADMIN
    app.dependency_overrides[deps.get_active_project] = lambda: PROJECT
    app.dependency_overrides[deps.get_current_user_id] = lambda: 42
    return TestClient(app), conn, started
```

Append the tests:

```python
def test_start_run_returns_the_run_id_immediately(client):
    tc, _, started = client
    resp = tc.post("/api/eval/runs", json={"k": 10})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"run_id": 3}
    assert started == {"org_id": 1, "project_id": 7, "k": 10}


def test_start_run_clamps_k(client):
    tc, _, started = client
    tc.post("/api/eval/runs", json={"k": 999})
    assert started["k"] == 50
    tc.post("/api/eval/runs", json={"k": 0})
    assert started["k"] == 1


def test_list_runs_is_scoped_and_hides_per_query(client):
    tc, conn, _ = client
    resp = tc.get("/api/eval/runs?limit=5")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 1
    assert body["runs"][0]["config"] == CONFIG
    assert body["runs"][0]["metrics"] == METRICS
    assert "per_query" not in body["runs"][0]
    kind, sql, args = conn.calls[-1]
    assert "FROM eval_runs" in sql and "ORDER BY started_at DESC" in sql
    assert args == (1, 7, 5)


def test_run_detail_includes_per_query(client):
    tc, conn, _ = client
    resp = tc.get("/api/eval/runs/3")
    assert resp.status_code == 200, resp.text
    assert resp.json()["per_query"] == PER_QUERY
    kind, sql, args = conn.calls[-1]
    assert args == (3, 1, 7)


def test_run_detail_of_another_project_is_404(client):
    tc, conn, _ = client
    conn.row = None
    assert tc.get("/api/eval/runs/3").status_code == 404


def test_members_cannot_start_or_read_runs(client):
    tc, _, _ = client
    tc.app.dependency_overrides[deps.get_active_org] = lambda: ORG_MEMBER
    assert tc.post("/api/eval/runs", json={"k": 10}).status_code == 403
    assert tc.get("/api/eval/runs").status_code == 403
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_eval_runs_routes.py
```

Expected: FAIL with 404s / `AttributeError` because the run routes do not exist yet.

- [ ] **Step 3: Append the run routes**

Add to `services/server/src/api/routes/eval.py` (`RunRequest` next to the other request models is fine; the routes go at the end of the file):

```python
class RunRequest(BaseModel):
    k: int = 10


_RUN_COLUMNS = "id, started_at, finished_at, config, metrics, error"


def _run_out(row, with_detail: bool = False) -> dict:
    out = {
        "id": int(row["id"]),
        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
        "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
        "config": _json(row["config"], {}),
        "metrics": _json(row["metrics"], None),
        "error": row["error"],
    }
    if with_detail:
        out["per_query"] = _json(row["per_query"], [])
    return out


@router.post("/runs")
async def start_run(
    req: RunRequest,
    project: ActiveProject = Depends(get_active_project),
    _org: ActiveOrg = Depends(_require_org_admin),
):
    """Start an evaluation run in the background and return its id."""
    from ...eval.runner import run_eval

    k = max(1, min(int(req.k), 50))
    run_id = await run_eval(project.org_id, project.project_id, k=k)
    return {"run_id": int(run_id)}


@router.get("/runs")
async def list_runs(
    limit: int = 20,
    project: ActiveProject = Depends(get_active_project),
    _org: ActiveOrg = Depends(_require_org_admin),
):
    """Recent evaluation runs of the active project, newest first."""
    capped = max(1, min(int(limit), 100))
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {_RUN_COLUMNS} FROM eval_runs "
            "WHERE org_id = $1 AND project_id = $2 ORDER BY started_at DESC LIMIT $3",
            project.org_id, project.project_id, capped,
        )
    runs = [_run_out(r) for r in rows]
    return {"runs": runs, "count": len(runs)}


@router.get("/runs/{run_id}")
async def get_run(
    run_id: int,
    project: ActiveProject = Depends(get_active_project),
    _org: ActiveOrg = Depends(_require_org_admin),
):
    """One evaluation run with its per-query detail."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_RUN_COLUMNS}, per_query FROM eval_runs "
            "WHERE id = $1 AND org_id = $2 AND project_id = $3",
            run_id, project.org_id, project.project_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Evaluation run not found")
    return _run_out(row, with_detail=True)
```

Note: `from ...eval.runner import run_eval` is deferred inside the route so the test's `monkeypatch.setattr("src.eval.runner.run_eval", ...)` takes effect.

- [ ] **Step 4: Run the test to verify it passes**

```bash
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_eval_runs_routes.py tests/test_eval_queries_routes.py
```

Expected: PASS (14 tests).

- [ ] **Step 5: Commit**

```bash
git add services/server/src/api/routes/eval.py services/server/tests/test_eval_runs_routes.py
git commit -m "add eval run routes"
```

---

### Task 5: Retrieval eval UI page

**Files:**
- Modify: `services/ui/src/lib/api.ts` (types before `export const api = {`, and an `eval:` block inside the `api` object next to `jobs:`)
- Create: `services/ui/src/pages/RetrievalEval.tsx`
- Modify: `services/ui/src/App.tsx` (import, `navLinks` entry, route)

**Interfaces:**
- Consumes (server side, from Tasks 3 and 4 — the UI calls these exact endpoints):
  - `GET /api/eval/queries` → `{"queries": [{"id", "query", "expected", "notes", "created_at"}], "count"}`
  - `POST /api/eval/queries` `{query, expected, notes}` → the created query (201)
  - `PUT /api/eval/queries/{id}` `{query, expected, notes}` → the updated query
  - `DELETE /api/eval/queries/{id}` → `{"status", "deleted"}`
  - `POST /api/eval/queries/capture` `{query}` → `{"candidates": [{"kind", "label", "snippet", "score", "expected"}], "count"}`
  - `POST /api/eval/runs` `{k}` → `{"run_id"}`
  - `GET /api/eval/runs?limit=20` → `{"runs": [{"id", "started_at", "finished_at", "config", "metrics", "error"}], "count"}`
  - `GET /api/eval/runs/{id}` → the same plus `per_query: [{"query_id", "query", "recall_at_5", "recall_at_10", "rr", "found", "missed"}]`
  - Expectation objects are `{"kind": "repo", "repo": string, "path": string}` or `{"kind": "kb", "document_id": number}`.
- Consumes (UI side, all already exist): `request<T>(path, options?)` in `lib/api.ts` (adds the auth, `X-Org-Id` and `X-Project-Id` headers); `Badge, Banner, Button, Card, Input, Textarea, Table, Thead, Tbody, Tr, Th, Td, useConfirm, useToast` from `../components/ui`; `useAppStore` from `../store` exposing `organizations: {id: number; role: OrgRole}[]` and `activeOrgId: number | null`.
- Produces: `api.eval.*` in `lib/api.ts`, the `RetrievalEval` page at route `/eval`, and an admin/owner-gated nav entry.

- [ ] **Step 1: Add the API types and client block**

In `services/ui/src/lib/api.ts`, insert these types just above `export const api = {`:

```ts
export type EvalExpectation =
  | { kind: 'repo'; repo: string; path: string }
  | { kind: 'kb'; document_id: number }

export interface EvalQuery {
  id: number
  query: string
  expected: EvalExpectation[]
  notes?: string | null
  created_at?: string | null
}

export interface EvalCandidate {
  kind: 'repo' | 'kb'
  label: string
  snippet: string
  score?: number | null
  expected: EvalExpectation
}

export interface EvalRunConfig {
  embeddings_model?: string
  embeddings_dims?: number
  hybrid?: boolean
  reranker_provider?: string
  reranker_model?: string
  k?: number
}

export interface EvalRunMetrics {
  recall_at_5: number
  recall_at_10: number
  mrr: number
  queries: number
  hits: number
}

export interface EvalRun {
  id: number
  started_at: string | null
  finished_at: string | null
  config: EvalRunConfig
  metrics: EvalRunMetrics | null
  error?: string | null
}

export interface EvalPerQuery {
  query_id: number
  query: string
  recall_at_5: number
  recall_at_10: number
  rr: number
  found: EvalExpectation[]
  missed: EvalExpectation[]
}

export interface EvalRunDetail extends EvalRun {
  per_query: EvalPerQuery[]
}

export interface EvalQueryRequest {
  query: string
  expected: EvalExpectation[]
  notes?: string
}
```

Then add this block inside the `api` object, right after the `jobs: { ... },` block:

```ts
  eval: {
    queries: () => request<{ queries: EvalQuery[]; count: number }>('/api/eval/queries'),
    createQuery: (req: EvalQueryRequest) =>
      request<EvalQuery>('/api/eval/queries', { method: 'POST', body: JSON.stringify(req) }),
    updateQuery: (id: number, req: EvalQueryRequest) =>
      request<EvalQuery>(`/api/eval/queries/${id}`, { method: 'PUT', body: JSON.stringify(req) }),
    deleteQuery: (id: number) =>
      request<{ status: string; deleted: number }>(`/api/eval/queries/${id}`, { method: 'DELETE' }),
    capture: (query: string) =>
      request<{ candidates: EvalCandidate[]; count: number }>('/api/eval/queries/capture', {
        method: 'POST',
        body: JSON.stringify({ query }),
      }),
    startRun: (k = 10) =>
      request<{ run_id: number }>('/api/eval/runs', { method: 'POST', body: JSON.stringify({ k }) }),
    runs: (limit = 20) =>
      request<{ runs: EvalRun[]; count: number }>(`/api/eval/runs?limit=${limit}`),
    run: (id: number) => request<EvalRunDetail>(`/api/eval/runs/${id}`),
  },
```

- [ ] **Step 2: Write the page — golden query panel**

Create `services/ui/src/pages/RetrievalEval.tsx` with this first half:

```tsx
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Play, Plus, Search, Trash2 } from 'lucide-react'
import {
  api,
  type EvalCandidate,
  type EvalExpectation,
  type EvalQuery,
  type EvalRun,
  type EvalRunDetail,
} from '../lib/api'
import {
  Badge, Banner, Button, Card, Input, Table, Tbody, Td, Th, Thead, Tr, Textarea,
  useConfirm, useToast,
} from '../components/ui'

function expectationKey(item: EvalExpectation) {
  return item.kind === 'repo' ? `repo:${item.repo}:${item.path}` : `kb:${item.document_id}`
}

function expectationLabel(item: EvalExpectation) {
  return item.kind === 'repo' ? `${item.repo}:${item.path}` : `document ${item.document_id}`
}

function pct(value: number | undefined) {
  return value === undefined ? '—' : `${Math.round(value * 100)}%`
}

function delta(current?: number, previous?: number) {
  if (current === undefined || previous === undefined) return null
  const diff = Math.round((current - previous) * 100)
  if (diff === 0) return <span className="text-muted">=</span>
  return (
    <span className={diff > 0 ? 'text-success' : 'text-danger'}>
      {diff > 0 ? `+${diff}` : diff} pt
    </span>
  )
}

function QueryEditor({ onSaved }: { onSaved: () => void }) {
  const toast = useToast()
  const [text, setText] = useState('')
  const [notes, setNotes] = useState('')
  const [candidates, setCandidates] = useState<EvalCandidate[]>([])
  const [picked, setPicked] = useState<Record<string, EvalExpectation>>({})
  const [finding, setFinding] = useState(false)
  const [saving, setSaving] = useState(false)

  const find = async () => {
    if (!text.trim()) return
    setFinding(true)
    try {
      const res = await api.eval.capture(text.trim())
      setCandidates(res.candidates)
    } catch (e) {
      toast.error(String(e))
    } finally {
      setFinding(false)
    }
  }

  const toggle = (candidate: EvalCandidate) => {
    const key = expectationKey(candidate.expected)
    setPicked((current) => {
      const next = { ...current }
      if (next[key]) delete next[key]
      else next[key] = candidate.expected
      return next
    })
  }

  const save = async () => {
    const expected = Object.values(picked)
    if (!text.trim() || expected.length === 0) return
    setSaving(true)
    try {
      await api.eval.createQuery({ query: text.trim(), expected, notes: notes.trim() || undefined })
      setText(''); setNotes(''); setCandidates([]); setPicked({})
      toast.success('Golden query added')
      onSaved()
    } catch (e) {
      toast.error(String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card className="p-4 space-y-3">
      <h3 className="text-sm font-semibold">New golden query</h3>
      <Input
        label="Query"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="e.g. where is the assignment scheduler"
      />
      <div className="flex gap-2">
        <Button size="sm" variant="ghost" onClick={find} loading={finding} disabled={!text.trim()}>
          <Search className="w-3.5 h-3.5" /> Find candidates
        </Button>
        <Button
          size="sm"
          variant="primary"
          onClick={save}
          loading={saving}
          disabled={!text.trim() || Object.keys(picked).length === 0}
        >
          <Plus className="w-3.5 h-3.5" /> Add
        </Button>
      </div>
      {candidates.length > 0 && (
        <div className="max-h-72 overflow-y-auto space-y-1 border border-border rounded p-2">
          {candidates.map((candidate) => {
            const key = expectationKey(candidate.expected)
            return (
              <label key={key} className="flex gap-2 items-start text-xs cursor-pointer">
                <input type="checkbox" checked={!!picked[key]} onChange={() => toggle(candidate)} />
                <span className="min-w-0">
                  <Badge variant="muted">{candidate.kind}</Badge>{' '}
                  <span className="font-medium">{candidate.label}</span>
                  <span className="block text-muted truncate">{candidate.snippet}</span>
                </span>
              </label>
            )
          })}
        </div>
      )}
      <Textarea
        label="Notes"
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        placeholder="Why this query matters"
        rows={2}
      />
    </Card>
  )
}
```

- [ ] **Step 3: Write the page — query list, runs table and detail**

Append to `services/ui/src/pages/RetrievalEval.tsx`:

```tsx
function QueryList({ queries, onChanged }: { queries: EvalQuery[]; onChanged: () => void }) {
  const confirm = useConfirm()
  const toast = useToast()

  const remove = async (item: EvalQuery) => {
    const ok = await confirm({
      title: 'Delete golden query',
      message: item.query,
      confirmLabel: 'Delete',
      danger: true,
    })
    if (!ok) return
    try {
      await api.eval.deleteQuery(item.id)
      onChanged()
    } catch (e) {
      toast.error(String(e))
    }
  }

  if (queries.length === 0) {
    return <Card className="p-4 text-sm text-muted">No golden queries yet.</Card>
  }

  return (
    <Card className="p-0">
      {queries.map((item) => (
        <div key={item.id} className="border-b border-border last:border-b-0 p-3 text-sm">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <div className="font-medium">{item.query}</div>
              <div className="text-xs text-muted mt-1 space-x-1">
                {item.expected.map((exp) => (
                  <Badge key={expectationKey(exp)} variant="muted">{expectationLabel(exp)}</Badge>
                ))}
              </div>
              {item.notes && <div className="text-xs text-muted mt-1">{item.notes}</div>}
            </div>
            <Button size="sm" variant="ghost" onClick={() => remove(item)} aria-label="Delete">
              <Trash2 className="w-3.5 h-3.5" />
            </Button>
          </div>
        </div>
      ))}
    </Card>
  )
}

function RunDetail({ run }: { run: EvalRunDetail }) {
  return (
    <Card className="p-0 mt-3">
      <Table>
        <Thead>
          <Tr><Th>Query</Th><Th>R@5</Th><Th>R@10</Th><Th>RR</Th><Th>Found</Th><Th>Missed</Th></Tr>
        </Thead>
        <Tbody>
          {run.per_query.map((row) => (
            <Tr key={row.query_id}>
              <Td>{row.query}</Td>
              <Td>{pct(row.recall_at_5)}</Td>
              <Td>{pct(row.recall_at_10)}</Td>
              <Td>{row.rr.toFixed(2)}</Td>
              <Td className="text-xs">
                {row.found.map((e) => <div key={expectationKey(e)}>{expectationLabel(e)}</div>)}
              </Td>
              <Td className="text-xs text-danger">
                {row.missed.map((e) => <div key={expectationKey(e)}>{expectationLabel(e)}</div>)}
              </Td>
            </Tr>
          ))}
        </Tbody>
      </Table>
    </Card>
  )
}
```

- [ ] **Step 4: Write the page — the default export with polling**

Append to `services/ui/src/pages/RetrievalEval.tsx`:

```tsx
export default function RetrievalEval() {
  const toast = useToast()
  const [queries, setQueries] = useState<EvalQuery[]>([])
  const [runs, setRuns] = useState<EvalRun[]>([])
  const [selected, setSelected] = useState<EvalRunDetail | null>(null)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loadQueries = useCallback(async () => {
    try {
      setQueries((await api.eval.queries()).queries)
    } catch (e) {
      setError(String(e))
    }
  }, [])

  const loadRuns = useCallback(async () => {
    try {
      setRuns((await api.eval.runs(20)).runs)
    } catch (e) {
      setError(String(e))
    }
  }, [])

  useEffect(() => { loadQueries(); loadRuns() }, [loadQueries, loadRuns])

  const pending = useMemo(() => runs.some((r) => r.finished_at === null), [runs])

  useEffect(() => {
    if (!pending) return
    const timer = window.setInterval(loadRuns, 3000)
    return () => window.clearInterval(timer)
  }, [pending, loadRuns])

  const start = async () => {
    setStarting(true)
    try {
      await api.eval.startRun(10)
      toast.success('Evaluation run started')
      await loadRuns()
    } catch (e) {
      toast.error(String(e))
    } finally {
      setStarting(false)
    }
  }

  const open = async (runId: number) => {
    try {
      setSelected(await api.eval.run(runId))
    } catch (e) {
      toast.error(String(e))
    }
  }

  const previous = runs[1]?.metrics ?? undefined

  return (
    <div className="p-4 sm:p-8">
      <div className="page-wide">
        <div className="flex items-start justify-between gap-3 mb-6">
          <div>
            <h1>Retrieval eval</h1>
            <p className="text-muted text-sm">
              Golden queries and the runs that measure how well retrieval finds them.
            </p>
          </div>
          <Button variant="primary" onClick={start} loading={starting} disabled={queries.length === 0}>
            <Play className="w-3.5 h-3.5" /> Run now
          </Button>
        </div>

        {error && <Banner variant="danger" className="mb-4">{error}</Banner>}

        <div className="grid gap-6 lg:grid-cols-2">
          <div className="space-y-3">
            <QueryEditor onSaved={loadQueries} />
            <QueryList queries={queries} onChanged={loadQueries} />
          </div>

          <div>
            <Card className="p-0">
              <Table>
                <Thead>
                  <Tr>
                    <Th>Started</Th><Th>Model</Th><Th>Hybrid</Th><Th>Reranker</Th>
                    <Th>R@5</Th><Th>R@10</Th><Th>MRR</Th><Th>Queries</Th>
                  </Tr>
                </Thead>
                <Tbody>
                  {runs.map((run, index) => (
                    <Tr
                      key={run.id}
                      onClick={() => open(run.id)}
                      className="cursor-pointer hover:bg-surface"
                    >
                      <Td>{run.started_at?.slice(0, 19).replace('T', ' ') ?? '—'}</Td>
                      <Td className="text-xs">
                        {run.config.embeddings_model} / {run.config.embeddings_dims}
                      </Td>
                      <Td>{run.config.hybrid ? 'yes' : 'no'}</Td>
                      <Td className="text-xs">{run.config.reranker_provider ?? 'none'}</Td>
                      <Td>
                        {pct(run.metrics?.recall_at_5)}{' '}
                        {index === 0 && delta(run.metrics?.recall_at_5, previous?.recall_at_5)}
                      </Td>
                      <Td>
                        {pct(run.metrics?.recall_at_10)}{' '}
                        {index === 0 && delta(run.metrics?.recall_at_10, previous?.recall_at_10)}
                      </Td>
                      <Td>
                        {run.metrics ? run.metrics.mrr.toFixed(2) : '—'}{' '}
                        {index === 0 && delta(run.metrics?.mrr, previous?.mrr)}
                      </Td>
                      <Td>
                        {run.finished_at === null
                          ? 'running…'
                          : run.error
                            ? <span className="text-danger">error</span>
                            : run.metrics?.queries}
                      </Td>
                    </Tr>
                  ))}
                </Tbody>
              </Table>
            </Card>
            {selected && <RunDetail run={selected} />}
          </div>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 5: Add the nav entry and route, gated to admin/owner**

In `services/ui/src/App.tsx`:

1. Add `Target` to the `lucide-react` import list on line 3.
2. Add the page import after `import Settings from './pages/Settings'`:

```tsx
import RetrievalEval from './pages/RetrievalEval'
```

3. Replace the `navLinks` const with the same list plus the gated entry:

```tsx
const navLinks: { to: string; icon: typeof GitBranch; label: string; adminOnly?: boolean }[] = [
  { to: '/chat', icon: MessagesSquare, label: 'Agent Chat' },
  { to: '/repos', icon: GitBranch, label: 'Repositories' },
  { to: '/datasources', icon: Database, label: 'Data Sources' },
  { to: '/ssh-sources', icon: TerminalSquare, label: 'SSH Files' },
  { to: '/contracts', icon: Braces, label: 'API Contracts' },
  { to: '/knowledge', icon: Library, label: 'Knowledge Base' },
  { to: '/web', icon: Globe, label: 'Web Pages' },
  { to: '/memory', icon: Brain, label: 'Memory' },
  { to: '/environments', icon: Server, label: 'Environments' },
  { to: '/eval', icon: Target, label: 'Retrieval Eval', adminOnly: true },
  { to: '/settings', icon: SlidersHorizontal, label: 'Settings' },
  { to: '/tools', icon: Wrench, label: 'MCP Tools' },
]
```

4. In `NavItems`, add the role filter above the `return` and map over `links` instead of `navLinks`:

```tsx
function NavItems({ onNavigate, showLogout = false }: { onNavigate?: () => void; showLogout?: boolean }) {
  const logout = useAppStore((s) => s.logout)
  const organizations = useAppStore((s) => s.organizations)
  const activeOrgId = useAppStore((s) => s.activeOrgId)
  const role = organizations.find((o) => o.id === activeOrgId)?.role
  const isOrgAdmin = role === 'admin' || role === 'owner'
  const links = navLinks.filter((link) => !link.adminOnly || isOrgAdmin)
  return (
    <>
      <nav className="flex-1 py-2 px-2 overflow-y-auto scrollbar-thin">
        {links.map(({ to, icon: Icon, label }) => (
```

(the rest of `NavItems` is unchanged).

5. Add the route after `<Route path="/settings" element={<Settings />} />`:

```tsx
                <Route path="/eval" element={<RetrievalEval />} />
```

- [ ] **Step 6: Verify the UI compiles and builds**

From `services/ui`:

```bash
npx tsc --noEmit
npm run build
```

Expected: both succeed with no errors.

- [ ] **Step 7: Commit**

```bash
git add services/ui/src/lib/api.ts services/ui/src/pages/RetrievalEval.tsx services/ui/src/App.tsx
git commit -m "add retrieval eval page"
```

---

### Task 6: Reranker module and its settings

**Files:**
- Create: `services/server/src/reranker.py`
- Modify: `services/server/src/config.py` (`Settings`, `RUNTIME_OVERRIDE_FIELDS`, `DEFAULT_RUNTIME_OVERRIDE_VALUES`)
- Modify: `services/server/src/org_settings.py` (`OrgSettings`)
- Modify: `services/server/src/api/routes/settings.py` (validate on save, reset cached models)
- Modify: `services/ui/src/pages/Settings.tsx` ("Search" block in the Models tab)
- Test: `services/server/tests/test_reranker.py`
- Test: `services/server/tests/test_reranker_settings.py`

**Interfaces:**
- Consumes (all already exist):
  - `src.config.get_settings() -> Settings` (a pydantic-settings `BaseSettings`; env vars map to the field names uppercased, e.g. `RERANKER_API_KEY` → `reranker_api_key`).
  - `src.config.RUNTIME_OVERRIDE_FIELDS: tuple[str, ...]` and `src.config.DEFAULT_RUNTIME_OVERRIDE_VALUES: dict` — the per-org overridable keys and their built-in defaults.
  - `src.org_settings.OrgSettings` (pydantic `BaseModel`) and `get_org_settings(org_id) -> OrgSettings`. `ORG_OVERRIDE_FIELDS = RUNTIME_OVERRIDE_FIELDS`, and `get_org_settings` builds `OrgSettings(**{field: getattr(base, field) for field in ORG_OVERRIDE_FIELDS})` — so every field added to `RUNTIME_OVERRIDE_FIELDS` **must** also exist on `OrgSettings` and on `Settings`.
  - `httpx` (already a dependency).
  - `src/api/routes/settings.py:update_runtime_settings` already builds `next_overrides` from `ORG_OVERRIDE_FIELDS` and calls `persist_org_settings_overrides`, `reset_embedder_clients()`, `reset_memory_client()`.
  - `services/ui/src/pages/Settings.tsx` has a `SettingsData['settings_overrides']` interface, a `Field` helper, a `ModelsTab` whose body is `<div className="grid gap-6 lg:grid-cols-3"> … </div>` inside `<div className="space-y-6">`, and `Select` from `../components/ui` with props `{ label, value, onValueChange, options: {value,label}[] }`.
- Produces:
  - `src.reranker.rerank(query: str, candidates: list[dict], text_key: str, top_n: int, org_id: int) -> list[dict]` — the candidates reordered with a `rerank_score` float; returns the input list unchanged for provider `none` and on any error.
  - `src.reranker.PROVIDERS = ("none", "jina", "local")`, `JINA_DEFAULT_MODEL = "jina-reranker-v2-base-multilingual"`, `LOCAL_DEFAULT_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"`.
  - `src.reranker.local_reranker_available() -> bool`, `src.reranker.reset_reranker_models() -> None`.
  - Org settings `reranker_provider` (default `"none"`), `reranker_model` (default `""`), `reranker_top_n` (default `30`); env-only `reranker_api_key` (default `""`).

- [ ] **Step 1: Write the failing reranker test**

Create `services/server/tests/test_reranker.py`:

```python
"""Reranker providers: passthrough, Jina payload/parsing, fail-open, local guard."""
import asyncio
from types import SimpleNamespace

from src import reranker

CANDIDATES = [
    {"content": "alpha", "score": 0.9},
    {"content": "bravo", "score": 0.8},
    {"content": "charlie", "score": 0.7},
]


def _org_settings(monkeypatch, provider, model="", api_key=""):
    async def fake(org_id):
        return SimpleNamespace(
            reranker_provider=provider, reranker_model=model, embeddings_api_key=api_key
        )

    monkeypatch.setattr(reranker, "get_org_settings", fake)


def _env_key(monkeypatch, value):
    monkeypatch.setattr(reranker, "get_settings", lambda: SimpleNamespace(reranker_api_key=value))


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _fake_httpx(monkeypatch, payload=None, error=None):
    captured = {}

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            captured["init"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None):
            captured.update(url=url, json=json, headers=headers)
            if error is not None:
                raise error
            return _FakeResponse(payload)

    monkeypatch.setattr(reranker.httpx, "AsyncClient", _FakeClient)
    return captured
```

Append the tests:

```python
def test_none_provider_returns_the_input_unchanged(monkeypatch):
    _org_settings(monkeypatch, "none")
    out = asyncio.run(reranker.rerank("q", CANDIDATES, "content", 30, 1))
    assert out == CANDIDATES
    assert all("rerank_score" not in c for c in out)


def test_empty_candidates_short_circuit(monkeypatch):
    _org_settings(monkeypatch, "jina")
    assert asyncio.run(reranker.rerank("q", [], "content", 30, 1)) == []


def test_jina_sends_the_documents_and_reorders_by_relevance(monkeypatch):
    _org_settings(monkeypatch, "jina", api_key="fallback-key")
    _env_key(monkeypatch, "")
    captured = _fake_httpx(monkeypatch, payload={"results": [
        {"index": 2, "relevance_score": 0.99},
        {"index": 0, "relevance_score": 0.10},
        {"index": 1, "relevance_score": 0.50},
    ]})

    out = asyncio.run(reranker.rerank("q", CANDIDATES, "content", 30, 1))

    assert captured["url"] == reranker.JINA_RERANK_URL
    assert captured["json"]["model"] == reranker.JINA_DEFAULT_MODEL
    assert captured["json"]["query"] == "q"
    assert captured["json"]["documents"] == ["alpha", "bravo", "charlie"]
    assert captured["headers"]["Authorization"] == "Bearer fallback-key"
    assert captured["init"]["timeout"] == reranker.REQUEST_TIMEOUT
    assert [c["content"] for c in out] == ["charlie", "bravo", "alpha"]
    assert out[0]["rerank_score"] == 0.99


def test_jina_prefers_the_dedicated_env_key_and_the_configured_model(monkeypatch):
    _org_settings(monkeypatch, "jina", model="jina-reranker-v2-base-en", api_key="fallback-key")
    _env_key(monkeypatch, "dedicated-key")
    captured = _fake_httpx(monkeypatch, payload={"results": []})
    asyncio.run(reranker.rerank("q", CANDIDATES, "content", 30, 1))
    assert captured["headers"]["Authorization"] == "Bearer dedicated-key"
    assert captured["json"]["model"] == "jina-reranker-v2-base-en"


def test_jina_failure_keeps_the_fused_order(monkeypatch):
    _org_settings(monkeypatch, "jina", api_key="k")
    _env_key(monkeypatch, "")
    _fake_httpx(monkeypatch, error=RuntimeError("boom"))
    out = asyncio.run(reranker.rerank("q", CANDIDATES, "content", 30, 1))
    assert out == CANDIDATES


def test_missing_key_keeps_the_fused_order(monkeypatch):
    _org_settings(monkeypatch, "jina", api_key="")
    _env_key(monkeypatch, "")
    _fake_httpx(monkeypatch, payload={"results": []})
    assert asyncio.run(reranker.rerank("q", CANDIDATES, "content", 30, 1)) == CANDIDATES


def test_top_n_limits_the_documents_sent(monkeypatch):
    _org_settings(monkeypatch, "jina", api_key="k")
    _env_key(monkeypatch, "")
    captured = _fake_httpx(monkeypatch, payload={"results": [{"index": 0, "relevance_score": 1.0}]})
    out = asyncio.run(reranker.rerank("q", CANDIDATES, "content", 2, 1))
    assert captured["json"]["documents"] == ["alpha", "bravo"]
    assert len(out) == 2


def test_unknown_provider_keeps_the_fused_order(monkeypatch):
    _org_settings(monkeypatch, "cohere")
    assert asyncio.run(reranker.rerank("q", CANDIDATES, "content", 30, 1)) == CANDIDATES


def test_local_availability_follows_the_installed_package(monkeypatch):
    monkeypatch.setattr(reranker.importlib.util, "find_spec", lambda name: None)
    assert reranker.local_reranker_available() is False
    monkeypatch.setattr(reranker.importlib.util, "find_spec", lambda name: object())
    assert reranker.local_reranker_available() is True


def test_local_provider_without_the_package_keeps_the_fused_order(monkeypatch):
    _org_settings(monkeypatch, "local")
    monkeypatch.setattr(reranker.importlib.util, "find_spec", lambda name: None)
    reranker.reset_reranker_models()
    assert asyncio.run(reranker.rerank("q", CANDIDATES, "content", 30, 1)) == CANDIDATES
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_reranker.py
```

Expected: FAIL with `ModuleNotFoundError: No module named 'src.reranker'`.

- [ ] **Step 3: Write the reranker module**

Create `services/server/src/reranker.py`:

```python
"""Cross-encoder reranking of the fused retrieval candidates.

The provider is chosen per organization: ``none`` keeps the fused order,
``jina`` calls the hosted reranker, ``local`` runs a sentence-transformers
CrossEncoder. Every failure logs a warning and returns the fused order.
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
from typing import Any

import httpx

from .config import get_settings
from .org_settings import get_org_settings

logger = logging.getLogger(__name__)

PROVIDERS = ("none", "jina", "local")
JINA_RERANK_URL = "https://api.jina.ai/v1/rerank"
JINA_DEFAULT_MODEL = "jina-reranker-v2-base-multilingual"
LOCAL_DEFAULT_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
REQUEST_TIMEOUT = 10.0

_local_models: dict[str, Any] = {}


def local_reranker_available() -> bool:
    """True when sentence-transformers is installed (the local embeddings build)."""
    return importlib.util.find_spec("sentence_transformers") is not None


def reset_reranker_models() -> None:
    """Drop cached local models so a settings change is picked up."""
    _local_models.clear()


async def rerank(
    query: str, candidates: list[dict], text_key: str, top_n: int, org_id: int
) -> list[dict]:
    """Reorder the candidates by cross-encoder relevance, adding ``rerank_score``."""
    if not candidates:
        return list(candidates)
    settings = await get_org_settings(org_id)
    provider = (getattr(settings, "reranker_provider", "none") or "none").strip()
    if provider == "none":
        return list(candidates)

    pool = list(candidates)[:top_n] if top_n and top_n > 0 else list(candidates)
    texts = [str(c.get(text_key) or "") for c in pool]
    try:
        if provider == "jina":
            scores = await _jina_scores(query, texts, settings)
        elif provider == "local":
            scores = await _local_scores(query, texts, settings)
        else:
            raise ValueError(f"Unknown reranker provider {provider!r}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Reranker %s failed (%s); keeping the fused order", provider, exc)
        return list(candidates)

    ranked = []
    for candidate, score in zip(pool, scores):
        item = dict(candidate)
        item["rerank_score"] = float(score)
        ranked.append(item)
    ranked.sort(key=lambda c: c["rerank_score"], reverse=True)
    return ranked
```

Append the two providers:

```python
async def _jina_scores(query: str, texts: list[str], settings) -> list[float]:
    api_key = get_settings().reranker_api_key or getattr(settings, "embeddings_api_key", "")
    if not api_key:
        raise RuntimeError("No RERANKER_API_KEY and no embeddings API key configured")
    model = getattr(settings, "reranker_model", "") or JINA_DEFAULT_MODEL
    payload = {"model": model, "query": query, "documents": texts, "top_n": len(texts)}
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.post(
            JINA_RERANK_URL,
            json=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        response.raise_for_status()
        data = response.json()
    scores = [0.0] * len(texts)
    for entry in data.get("results", []):
        index = int(entry.get("index", -1))
        if 0 <= index < len(scores):
            scores[index] = float(entry.get("relevance_score", 0.0))
    return scores


async def _local_scores(query: str, texts: list[str], settings) -> list[float]:
    if not local_reranker_available():
        raise RuntimeError("sentence-transformers is not installed in this build")
    model_name = getattr(settings, "reranker_model", "") or LOCAL_DEFAULT_MODEL
    model = _local_models.get(model_name)
    if model is None:
        from sentence_transformers import CrossEncoder

        logger.info("Loading local reranker model: %s", model_name)
        model = CrossEncoder(model_name)
        _local_models[model_name] = model
    loop = asyncio.get_event_loop()
    scores = await loop.run_in_executor(
        None, lambda: model.predict([(query, text) for text in texts])
    )
    return [float(x) for x in scores]
```

- [ ] **Step 4: Run the reranker test to verify it passes**

```bash
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_reranker.py
```

Expected: PASS (10 tests).

- [ ] **Step 5: Write the failing settings test**

Create `services/server/tests/test_reranker_settings.py`:

```python
"""Reranker settings: declared per org, validated when saved."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src import config, org_settings, reranker
from src.api import deps
from src.api.routes import settings as settings_routes

ORG_ADMIN = deps.ActiveOrg(org_id=1, role="admin", namespace="acme", name="Acme")

RERANKER_FIELDS = ("reranker_provider", "reranker_model", "reranker_top_n")


def test_reranker_keys_are_org_overridable_with_defaults():
    for field in RERANKER_FIELDS:
        assert field in config.RUNTIME_OVERRIDE_FIELDS, field
        assert field in config.DEFAULT_RUNTIME_OVERRIDE_VALUES, field
    assert config.DEFAULT_RUNTIME_OVERRIDE_VALUES["reranker_provider"] == "none"
    assert config.DEFAULT_RUNTIME_OVERRIDE_VALUES["reranker_model"] == ""
    assert config.DEFAULT_RUNTIME_OVERRIDE_VALUES["reranker_top_n"] == 30


def test_org_settings_model_carries_the_reranker_defaults():
    resolved = org_settings.OrgSettings()
    assert resolved.reranker_provider == "none"
    assert resolved.reranker_model == ""
    assert resolved.reranker_top_n == 30


def test_the_api_key_is_env_only():
    assert "reranker_api_key" not in config.RUNTIME_OVERRIDE_FIELDS
    assert hasattr(config.Settings(), "reranker_api_key")


@pytest.fixture
def client(monkeypatch):
    persisted = {}

    async def fake_get_org_config(org_id):
        return config.ForgeConfig()

    async def fake_persist_org_config(org_id, forge):
        return None

    async def fake_sync_repos_config(org_id):
        return None

    async def fake_get_org_settings(org_id):
        return org_settings.OrgSettings()

    async def fake_persist(org_id, overrides):
        persisted.update(overrides)

    monkeypatch.setattr(settings_routes, "get_org_config", fake_get_org_config)
    monkeypatch.setattr(settings_routes, "persist_org_config", fake_persist_org_config)
    monkeypatch.setattr(settings_routes, "sync_repos_config", fake_sync_repos_config)
    monkeypatch.setattr(settings_routes, "get_org_settings", fake_get_org_settings)
    monkeypatch.setattr(settings_routes, "persist_org_settings_overrides", fake_persist)
    monkeypatch.setattr(settings_routes, "reset_embedder_clients", lambda *a, **kw: None)
    monkeypatch.setattr(settings_routes, "reset_memory_client", lambda *a, **kw: None)

    app = FastAPI()
    app.include_router(settings_routes.router, prefix="/api")
    app.dependency_overrides[deps.get_active_org] = lambda: ORG_ADMIN
    app.dependency_overrides[deps.get_current_user_id] = lambda: 1
    return TestClient(app), persisted


def _payload(**overrides):
    return {"forge_config": {}, "settings_overrides": overrides}


def test_saving_an_unknown_provider_is_rejected(client):
    tc, _ = client
    resp = tc.put("/api/settings", json=_payload(reranker_provider="cohere"))
    assert resp.status_code == 400
    assert "reranker_provider" in resp.json()["detail"]


def test_saving_local_without_the_package_is_rejected(client, monkeypatch):
    tc, _ = client
    monkeypatch.setattr(settings_routes, "local_reranker_available", lambda: False)
    resp = tc.put("/api/settings", json=_payload(reranker_provider="local"))
    assert resp.status_code == 400
    assert "sentence-transformers" in resp.json()["detail"]


def test_saving_jina_persists_the_reranker_keys(client, monkeypatch):
    tc, persisted = client
    monkeypatch.setattr(settings_routes, "reset_reranker_models", lambda: None)
    resp = tc.put("/api/settings", json=_payload(
        reranker_provider="jina", reranker_model=reranker.JINA_DEFAULT_MODEL, reranker_top_n=40
    ))
    assert resp.status_code == 200, resp.text
    assert persisted["reranker_provider"] == "jina"
    assert persisted["reranker_top_n"] == 40
```

- [ ] **Step 6: Run the settings test to verify it fails**

```bash
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_reranker_settings.py
```

Expected: FAIL — `reranker_provider` is not in `RUNTIME_OVERRIDE_FIELDS`.

- [ ] **Step 7: Add the settings fields**

In `services/server/src/config.py`, add to the `Settings` class right after the `search_hybrid: bool = True` block:

```python
    # Cross-encoder reranking of the fused search candidates: none | jina | local.
    reranker_provider: str = "none"
    reranker_model: str = ""
    reranker_top_n: int = 30
    reranker_api_key: str = ""   # env only; falls back to the org's embeddings key
```

Extend `RUNTIME_OVERRIDE_FIELDS` with the three overridable keys (append before the closing paren):

```python
    "telegram_allowed_chat_ids",
    "reranker_provider",
    "reranker_model",
    "reranker_top_n",
)
```

Extend `DEFAULT_RUNTIME_OVERRIDE_VALUES` the same way:

```python
    "telegram_allowed_chat_ids": "",
    "reranker_provider": "none",
    "reranker_model": "",
    "reranker_top_n": 30,
}
```

In `services/server/src/org_settings.py`, add to the `OrgSettings` model after `telegram_allowed_chat_ids`:

```python
    reranker_provider: str = "none"
    reranker_model: str = ""
    reranker_top_n: int = 30
```

- [ ] **Step 8: Validate the provider when settings are saved**

In `services/server/src/api/routes/settings.py`, add to the imports (next to the other `from ...` lines):

```python
from ...reranker import PROVIDERS as RERANKER_PROVIDERS
from ...reranker import local_reranker_available, reset_reranker_models
```

In `update_runtime_settings`, insert this block right after the `telegram_webhook_secret` conflict check and before `overrides_changed = any(...)`:

```python
    provider = str(next_overrides.get("reranker_provider") or "none")
    if provider not in RERANKER_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"reranker_provider must be one of {', '.join(RERANKER_PROVIDERS)}",
        )
    if provider == "local" and not local_reranker_available():
        raise HTTPException(
            status_code=400,
            detail="The local reranker needs sentence-transformers, which is not installed in this build",
        )
```

And add the cache reset next to the existing resets inside `if overrides_changed:`:

```python
        reset_embedder_clients()
        reset_memory_client()
        reset_reranker_models()
```

- [ ] **Step 9: Run the settings test to verify it passes**

```bash
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_reranker_settings.py tests/test_org_settings.py tests/test_settings_org_scope.py tests/test_oidc_settings.py
```

Expected: PASS (all files; the existing settings tests must stay green).

- [ ] **Step 10: Add the Search block to the Settings UI**

In `services/ui/src/pages/Settings.tsx`:

1. Add to the `settings_overrides` interface after `telegram_org_id?: number`:

```ts
    reranker_provider?: string
    reranker_model?: string
    reranker_top_n?: number
```

2. In `ModelsTab`, after the closing `</div>` of the existing `<div className="grid gap-6 lg:grid-cols-3">` and before the closing `</div>` of `<div className="space-y-6">`, add:

```tsx
      <div className="grid gap-6 lg:grid-cols-3">
        <section>
          <h3 className="text-sm font-semibold mb-3">Search</h3>
          <div className="space-y-3">
            <Select
              label="Reranker provider"
              value={settings.reranker_provider || 'none'}
              onValueChange={v => onChange('reranker_provider', v)}
              options={[
                { value: 'none', label: 'None' },
                { value: 'jina', label: 'Jina' },
                { value: 'local', label: 'Local cross-encoder' },
              ]}
            />
            <Field
              label="Reranker model"
              hint="Empty uses the provider default."
            >
              <Input
                value={settings.reranker_model || ''}
                onChange={e => onChange('reranker_model', e.target.value)}
                placeholder="jina-reranker-v2-base-multilingual"
              />
            </Field>
            <Field
              label="Candidates reranked"
              hint="Fused candidates sent to the reranker before trimming to the requested limit."
            >
              <Input
                type="number"
                value={String(settings.reranker_top_n ?? 30)}
                onChange={e => onChange('reranker_top_n', parseInt(e.target.value, 10) || 30)}
              />
            </Field>
          </div>
        </section>
      </div>
```

- [ ] **Step 11: Verify the UI compiles and builds**

From `services/ui`:

```bash
npx tsc --noEmit
npm run build
```

Expected: both succeed.

- [ ] **Step 12: Commit**

```bash
git add services/server/src/reranker.py services/server/src/config.py services/server/src/org_settings.py services/server/src/api/routes/settings.py services/server/tests/test_reranker.py services/server/tests/test_reranker_settings.py services/ui/src/pages/Settings.tsx
git commit -m "add reranker providers and settings"
```

---

### Task 7: Wire the reranker into search

**Files:**
- Modify: `services/server/src/search.py` (`search_repo_chunks` and `search_kb_chunks` only)
- Test: `services/server/tests/test_search_reranker.py`

**Interfaces:**
- Consumes:
  - `src.reranker.rerank(query: str, candidates: list[dict], text_key: str, top_n: int, org_id: int) -> list[dict]` from Task 6 — returns the candidates reordered with a `rerank_score`; returns the input unchanged for provider `none` and on any error.
  - `src.org_settings.get_org_settings(org_id) -> OrgSettings` with `reranker_provider: str` and `reranker_top_n: int` (Task 6).
  - Existing in `src/search.py`: `embed_text` (imported as `from .indexer.embedder import embed_text`), `_vector_to_pg`, `hybrid_enabled()`, `_normalize_scores(rows)`, `_parse_metadata(value)`, `CANDIDATE_POOL = 50`, and the SQL constants `_REPO_HYBRID_SQL` (params `$1 embedding $2 org_id $3 candidate pool $4 query $5 repos $6 limit $7 project`), `_REPO_VECTOR_SQL` (`$1 embedding $2 org_id $3 repos $4 limit $5 project`), `_KB_HYBRID_SQL` (`$1 embedding $2 org_id $3 candidate pool $4 query $5 document_ids $6 limit $7 project`), `_KB_VECTOR_SQL` (`$1 embedding $2 org_id $3 document_ids $4 limit $5 project`).
  - **Do not edit the SQL constants.** After the HNSW feature they contain `(embedding::vector(N))` casts and an `hnsw.ef_search` setting. The only change here is the *value* bound to the existing `LIMIT` parameter, plus post-processing of the returned rows.
- Produces:
  - `src.search._reranker_config(org_id: int) -> tuple[str, int]` — `(provider, top_n)`, with `top_n = 0` when the provider is `none`.
  - `search_repo_chunks` / `search_kb_chunks` unchanged in signature and result shape, except that reranked results carry an extra `rerank_score` key.

- [ ] **Step 1: Write the failing test**

Create `services/server/tests/test_search_reranker.py`:

```python
"""Search integration: the reranker runs after fusion and trims to the limit."""
import asyncio
from types import SimpleNamespace

from src import search


class _FakeConn:
    def __init__(self, rows):
        self.rows = rows
        self.args = []

    async def fetch(self, sql, *args):
        self.args.append(args)
        return self.rows


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


def _repo_rows(count):
    return [
        {"repo_name": "api", "file_path": f"src/f{i}.py", "chunk_type": "function_definition",
         "content": f"body {i}", "metadata": {}, "score": 1.0 - i / 1000}
        for i in range(count)
    ]


def _kb_rows(count):
    return [
        {"document_id": i, "title": f"doc {i}", "filename": f"d{i}.pdf", "extension": "pdf",
         "chunk_index": 0, "content": f"body {i}", "metadata": {}, "score": 1.0 - i / 1000}
        for i in range(count)
    ]


def _install(monkeypatch, rows, provider, top_n):
    conn = _FakeConn(rows)

    async def fake_pool():
        return _FakePool(conn)

    async def fake_embed(text, org_id):
        return [0.1, 0.2, 0.3]

    async def fake_org_settings(org_id):
        return SimpleNamespace(reranker_provider=provider, reranker_top_n=top_n)

    calls = {}

    async def fake_rerank(query, candidates, text_key, top_n_arg, org_id):
        calls.update(count=len(candidates), text_key=text_key, top_n=top_n_arg)
        return list(reversed(candidates))

    monkeypatch.setattr(search, "get_pool", fake_pool)
    monkeypatch.setattr(search, "embed_text", fake_embed)
    monkeypatch.setattr(search, "get_org_settings", fake_org_settings)
    monkeypatch.setattr(search, "rerank", fake_rerank)
    monkeypatch.setattr(search, "hybrid_enabled", lambda: True)
    return conn, calls
```

Append the tests:

```python
def test_repo_search_widens_the_fetch_and_reorders(monkeypatch):
    conn, calls = _install(monkeypatch, _repo_rows(30), "jina", 30)
    out = asyncio.run(search.search_repo_chunks(1, "q", limit=5, project_id=7))
    # $6 is the LIMIT of _REPO_HYBRID_SQL: widened to the reranker pool.
    assert conn.args[0][5] == 30
    assert calls == {"count": 30, "text_key": "content", "top_n": 30}
    assert len(out) == 5
    assert out[0]["file_path"] == "src/f29.py"


def test_repo_search_without_a_reranker_keeps_the_narrow_limit(monkeypatch):
    conn, calls = _install(monkeypatch, _repo_rows(5), "none", 30)
    out = asyncio.run(search.search_repo_chunks(1, "q", limit=5, project_id=7))
    assert conn.args[0][5] == 5
    assert calls == {}
    assert out[0]["file_path"] == "src/f0.py"


def test_repo_search_never_shrinks_below_the_requested_limit(monkeypatch):
    conn, _ = _install(monkeypatch, _repo_rows(40), "jina", 10)
    asyncio.run(search.search_repo_chunks(1, "q", limit=25, project_id=7))
    assert conn.args[0][5] == 25


def test_kb_search_widens_the_fetch_and_reorders(monkeypatch):
    conn, calls = _install(monkeypatch, _kb_rows(30), "jina", 30)
    out = asyncio.run(search.search_kb_chunks(1, "q", limit=3, project_id=7))
    # $6 is the LIMIT of _KB_HYBRID_SQL.
    assert conn.args[0][5] == 30
    assert calls["count"] == 30
    assert len(out) == 3
    assert out[0]["document_id"] == 29


def test_kb_search_without_a_reranker_is_untouched(monkeypatch):
    conn, calls = _install(monkeypatch, _kb_rows(3), "none", 30)
    out = asyncio.run(search.search_kb_chunks(1, "q", limit=3, project_id=7))
    assert conn.args[0][5] == 3
    assert calls == {}
    assert out[0]["document_id"] == 0
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_search_reranker.py
```

Expected: FAIL with `AttributeError: <module 'src.search'> does not have the attribute 'rerank'`.

- [ ] **Step 3: Add the reranker helper to search.py**

In `services/server/src/search.py`, extend the imports at the top:

```python
from .config import get_settings
from .db import get_pool
from .indexer.embedder import embed_text
from .org_settings import get_org_settings
from .reranker import rerank
```

Then add this helper right after `_normalize_scores`:

```python
async def _reranker_config(org_id: int) -> tuple[str, int]:
    """The organization's reranker provider and candidate pool (0 when disabled)."""
    settings = await get_org_settings(org_id)
    provider = (getattr(settings, "reranker_provider", "none") or "none").strip()
    if provider == "none":
        return provider, 0
    return provider, max(0, int(getattr(settings, "reranker_top_n", 0) or 0))
```

- [ ] **Step 4: Rerank in `search_repo_chunks`**

Replace the body of `search_repo_chunks` after the `project_id` guard with:

```python
    embedding_str = _vector_to_pg(await embed_text(query, org_id))
    pool = await get_pool()
    hybrid = hybrid_enabled()
    _, top_n = await _reranker_config(org_id)
    fetch_limit = max(limit, top_n) if top_n else limit

    async with pool.acquire() as conn:
        if hybrid:
            rows = await conn.fetch(
                _REPO_HYBRID_SQL, embedding_str, org_id, CANDIDATE_POOL, query, repos,
                fetch_limit, project_id,
            )
        else:
            rows = await conn.fetch(
                _REPO_VECTOR_SQL, embedding_str, org_id, repos, fetch_limit, project_id
            )

    results = [
        {
            "repo_name": r["repo_name"],
            "file_path": r["file_path"],
            "chunk_type": r["chunk_type"],
            "content": r["content"],
            "metadata": _parse_metadata(r["metadata"]),
            "score": float(r["score"]),
        }
        for r in rows
    ]
    if hybrid:
        _normalize_scores(results)
    else:
        for r in results:
            r["score"] = round(r["score"], 4)
    if top_n:
        results = await rerank(query, results[:top_n], "content", top_n, org_id)
    return results[:limit]
```

- [ ] **Step 5: Rerank in `search_kb_chunks`**

Replace the body of `search_kb_chunks` after the `project_id` guard with:

```python
    embedding_str = _vector_to_pg(await embed_text(query, org_id))
    pool = await get_pool()
    hybrid = hybrid_enabled()
    _, top_n = await _reranker_config(org_id)
    fetch_limit = max(limit, top_n) if top_n else limit

    async with pool.acquire() as conn:
        if hybrid:
            rows = await conn.fetch(
                _KB_HYBRID_SQL, embedding_str, org_id, CANDIDATE_POOL, query, document_ids,
                fetch_limit, project_id,
            )
        else:
            rows = await conn.fetch(
                _KB_VECTOR_SQL, embedding_str, org_id, document_ids, fetch_limit, project_id
            )

    results = [
        {
            "document_id": int(r["document_id"]),
            "title": r["title"],
            "filename": r["filename"],
            "extension": r["extension"],
            "chunk_index": r["chunk_index"],
            "content": r["content"],
            "metadata": _parse_metadata(r["metadata"]),
            "score": float(r["score"]),
        }
        for r in rows
    ]
    if hybrid:
        _normalize_scores(results)
    else:
        for r in results:
            r["score"] = round(r["score"], 4)
    if top_n:
        results = await rerank(query, results[:top_n], "content", top_n, org_id)
    return results[:limit]
```

`search_web_chunks` and `search_repo_symbols` are unchanged.

- [ ] **Step 6: Run the search tests to verify they pass**

```bash
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_search_reranker.py tests/test_search_requires_project.py tests/test_repo_project_scope.py tests/test_kb_project_scope.py
```

Expected: PASS (all four files).

- [ ] **Step 7: Commit**

```bash
git add services/server/src/search.py services/server/tests/test_search_reranker.py
git commit -m "rerank fused search candidates"
```

---

### Task 8: Documentation, environment, and full verification

**Files:**
- Modify: `README.md` (Features list, the "Upgrading an existing installation" note about eval, and the env-var mention)
- Modify: `.env.example` (Search section)
- Modify: `docker-compose.yml` (server `environment:` list)
- No new tests; this task runs the full server suite and the UI checks.

**Interfaces:**
- Consumes (produced by the earlier tasks, and what the docs must describe):
  - Per-organization settings, editable in Settings → Models → **Search**: `reranker_provider` (`none` | `jina` | `local`, default `none`), `reranker_model` (default empty = `jina-reranker-v2-base-multilingual` for Jina, `cross-encoder/ms-marco-MiniLM-L-6-v2` for local), `reranker_top_n` (default `30`).
  - Env-only variable `RERANKER_API_KEY`, falling back to the organization's embeddings API key.
  - UI page **Retrieval Eval** at `/eval`, admin/owner only.
  - Migration `0006_eval` creating `eval_queries` and `eval_runs`.
  - The MCP tool list is unchanged; no new permission is introduced.
- Produces: documentation and deployment configuration; a green server suite and green UI checks.

- [ ] **Step 1: Add the README feature bullets**

In `README.md`, insert these two bullets in the `## Features` list, right after the "Hybrid repository search" bullet:

```markdown
- **Reranking** — an optional cross-encoder reorders the fused candidates before they reach the agent: `none` (default), `jina` (hosted, `RERANKER_API_KEY` or the organization's embeddings key), or a local sentence-transformers cross-encoder. Configured per organization in Settings → Models → Search; any provider error falls back to the fused order, so search never breaks.
- **Retrieval evaluation** — a golden-query harness (**Retrieval Eval** in the sidebar, org admins) records the queries that must find specific files or documents, then replays them on demand and stores recall@5, recall@10 and MRR per run together with the embedding model, hybrid flag and reranker in effect. Consecutive runs show the delta, so changing a model or turning the reranker on is a measured decision.
```

- [ ] **Step 2: Replace the stale ivfflat upgrade note**

In `README.md`, in the "### Upgrading an existing installation" list, the last bullet currently ends with the sequential-scan caveat. Replace that bullet with:

```markdown
- embedding columns lose their fixed dimension so each organization can pick its own model; HNSW indexes are then built per organization and dimension.
```

- [ ] **Step 3: Document the reranker environment variable**

In `README.md`, immediately after the "See `.env.example` for the full list ..." paragraph in "Quick start", add:

```markdown
Search behaviour is configured per organization in the UI (Settings → Models → Search: reranker provider, model, and how many fused candidates get reranked). Only the reranker credential is environment-only: set `RERANKER_API_KEY` when the Jina reranker should use a key of its own, otherwise it reuses the organization's embeddings key.
```

- [ ] **Step 4: Extend `.env.example`**

In `.env.example`, replace the `# ─── Search ───` section with:

```env
# ─── Search ───────────────────────────────────────
# Hybrid retrieval fuses dense vector similarity with lexical full-text ranking
# (Reciprocal Rank Fusion) so exact identifiers/error strings surface alongside
# semantic matches. Set to false for vector-only search.
SEARCH_HYBRID=true

# Optional cross-encoder reranking of the fused candidates. The provider, model
# and candidate pool are per-organization settings (Settings → Models → Search);
# only the credential lives here. Empty reuses the organization's embeddings key.
#   jina  → https://api.jina.ai/v1/rerank, default model jina-reranker-v2-base-multilingual
#   local → sentence-transformers CrossEncoder, default cross-encoder/ms-marco-MiniLM-L-6-v2
#           (needs the local embeddings build)
RERANKER_API_KEY=
```

- [ ] **Step 5: Pass the variable through compose**

In `docker-compose.yml`, in the server service's `environment:` list, add right after the `- SEARCH_HYBRID=${SEARCH_HYBRID:-true}` line:

```yaml
      - RERANKER_API_KEY=${RERANKER_API_KEY:-}
```

- [ ] **Step 6: Run the full server test suite**

From `services/server`:

```bash
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider
```

Expected: PASS, with no failures and no errors. If any pre-existing test fails, fix the regression this plan introduced (do not delete or skip tests).

- [ ] **Step 7: Run the UI checks**

From `services/ui`:

```bash
npx tsc --noEmit
npm run build
npx vitest run
```

Expected: `tsc` prints nothing, `npm run build` succeeds, `vitest run` passes (it reports "No test files found" when the UI has no test files — that is acceptable, but a failing run is not).

- [ ] **Step 8: Confirm the results before claiming completion**

State explicitly, quoting the actual output lines: the number of server tests that passed, that `npx tsc --noEmit` produced no errors, and that `npm run build` completed. Do not claim success without those lines in front of you.

- [ ] **Step 9: Commit**

```bash
git add README.md .env.example docker-compose.yml
git commit -m "document retrieval eval and reranker"
```

---

## Self-Review

**1. Spec coverage**

| Spec requirement | Task |
|---|---|
| Migration `0006_eval` with `eval_queries` / `eval_runs` / `eval_runs_project_idx` | 1 |
| `recall_at_5`, `recall_at_10`, `rr`, macro averages | 1 (`metrics.py`, pure) |
| `run_eval(org_id, project_id, k=10) -> run_id`, repo vs kb expectations, `limit=k` | 2 |
| Row created immediately with `finished_at = NULL`; work in `asyncio.create_task` | 2 (`run_eval`) |
| Run records the effective config from org settings | 2 (`effective_config`) |
| Errors captured into `eval_runs.error` | 2 |
| `GET/POST/PUT/DELETE /api/eval/queries` | 3 |
| `POST /api/eval/queries/capture` returning top-10 candidates to tick | 3 |
| `POST /api/eval/runs`, `GET /api/eval/runs?limit=20`, `GET /api/eval/runs/{id}` | 4 |
| Org admin/owner, project from `X-Project-Id` | 3 and 4 (`require_role("admin")` + `get_active_project`) |
| Sidebar page **Retrieval eval**, route `/eval`, admin/owner | 5 |
| Add form, "Find candidates" with checkboxes, notes, delete | 5 |
| Runs table with model/dims/hybrid/reranker/recall/MRR, "Run now", per-query detail, delta on the two most recent runs, polling | 5 |
| `rerank(query, candidates, text_key, top_n, org_id)` with `rerank_score` | 6 |
| Providers `none` / `jina` (10 s timeout, `RERANKER_API_KEY` → `EMBEDDINGS_API_KEY`) / `local` (lazy import) | 6 |
| Clear error at settings save time when `local` is unavailable | 6 (settings route 400) |
| Fail open on any provider error | 6 |
| Settings `reranker_provider`, `reranker_model`, `reranker_top_n` (default 30), Settings UI "Search" block, `RERANKER_API_KEY` env only | 6 |
| Integration after RRF fusion / vector ranking: take top `reranker_top_n`, rerank, return top `limit` | 7 |
| Tests: metric maths, runner with fakes, capture shape, CRUD role gating, project scoping, reranker (`none`, jina payload+parsing, fail open, local guard), search integration | 1–7 |
| README features, Settings/env vars, tools list unchanged | 8 |

No gaps. The tools list is deliberately untouched (no MCP tool and no new permission), matching the spec.

**2. Placeholder scan**

No "TBD", "implement later", "similar to Task N", or "add error handling" instructions. Every code step carries the code. Task 2's `_score_query` shows a first draft immediately followed by the replacement that is actually to be written — the replacement is complete and explicit about superseding the draft.

**3. Type consistency**

- `match_rank` / `query_metrics` / `macro_average` defined in Task 1 are called with the same names and argument shapes in Task 2.
- `create_run` / `execute_run` / `run_eval` signatures match between Task 2's implementation and Task 4's route (`run_eval(org_id, project_id, k=k)`) and Task 2's tests.
- `rerank(query, candidates, text_key, top_n, org_id)` is identical in Task 6's implementation, Task 6's tests, and Task 7's call sites and fake.
- `reranker_provider` / `reranker_model` / `reranker_top_n` are spelled the same in `config.py`, `org_settings.py`, `settings.py`, `search.py`, `Settings.tsx` and the runner's `effective_config`.
- The run and query JSON shapes produced by Tasks 3–4 match the TypeScript interfaces in Task 5 field for field (`id`, `query`, `expected`, `notes`, `created_at`; `id`, `started_at`, `finished_at`, `config`, `metrics`, `error`, `per_query`).
- Task 2 reads `reranker_provider` / `reranker_model` via `getattr(..., default)` because Task 6 adds those fields; the call keeps working unchanged afterwards, and Task 2's test asserts the `"none"` / `""` defaults exactly.
