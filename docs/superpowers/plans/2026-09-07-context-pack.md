# `context_pack` tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One MCP call that returns the memory, repo map, code, knowledge-base, database-schema and web context needed to start a task, packed into a token budget.

**Architecture:** A pure budgeting module (`src/context_pack/budget.py`) owns token estimation, share allocation, ordering, truncation and omission counting with zero I/O. A thin tool module (`src/mcp/context_pack.py`) resolves org/project once, checks `context-read` once, runs six source adapters concurrently with a per-source timeout, and hands the candidates to the budget module. Each adapter calls the same internal coroutine the corresponding tool uses — nothing is reimplemented.

**Tech Stack:** Python 3.11 (local dev 3.14), FastMCP 3.4.x, asyncpg via `src.db.get_pool()`, pytest.

**Spec:** `docs/superpowers/specs/2026-09-07-context-pack-design.md`

## Global Constraints

Copied verbatim from `docs/superpowers/specs/2026-09-07-platform-improvements-roadmap.md` ("Global constraints"):

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

Feature-specific constraints for this plan:

- No new MCP permission: `context_pack` reuses the existing `context-read`.
- No new database table, no migration, no new Python dependency, no UI page, no REST route.
- Paths below are relative to `services/server` unless stated otherwise; every
  test command is run from `services/server`.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/context_pack/__init__.py` (create) | package marker, empty |
| `src/context_pack/budget.py` (create) | pure budgeting: `estimate_tokens`, `SECTION_KINDS`, `SECTION_WEIGHTS`, `Candidate`, `list_text`, `validate_args`, `allocate_shares`, `truncate_candidate`, `pack`. No imports from `src.db`, `src.mcp`, `asyncio`, or anything doing I/O. |
| `src/mcp/context_pack.py` (create) | the `context_pack` MCP tool: argument validation, org/project resolution, permission gate, concurrent source runner with per-source timeout, six adapters wiring the existing internal coroutines. |
| `src/main.py` (modify, line 32) | import the new tool module so it registers on the FastMCP instance. |
| `tests/test_context_pack_budget.py` (create) | budget unit tests, no I/O at all. |
| `tests/test_context_pack_tool.py` (create) | tool machinery: validation, gating, timeouts, error isolation, with fake adapters. |
| `tests/test_context_pack_sources.py` (create) | adapters wired to the real internal coroutines, all monkeypatched. |
| `README.md`, `templates/CLAUDE.md`, `templates/AGENTS.md` (modify, repo root) | documentation. |

---

## Task 1: Pure budget module

**Files:**
- Create: `services/server/src/context_pack/__init__.py`
- Create: `services/server/src/context_pack/budget.py`
- Test: `services/server/tests/test_context_pack_budget.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces, all in `src.context_pack.budget`:
  - `SECTION_KINDS: tuple[str, ...] = ("memory", "repo_map", "code", "kb", "db", "web")`
  - `SECTION_WEIGHTS: dict[str, int]` — memory 15, repo_map 25, code 30, kb 15, db 10, web 5 (the spec's percentages as integers, so allocation is integer arithmetic).
  - `MIN_BUDGET_TOKENS = 1000`, `MAX_BUDGET_TOKENS = 32000`
  - `estimate_tokens(text: str) -> int`
  - `list_text(items: list) -> str`
  - `@dataclass class Candidate: payload: dict[str, Any]; text: str; truncate_key: str | None = None; score: float = 0.0`
  - `validate_args(budget_tokens: int, include: list[str] | None) -> str | None`
  - `allocate_shares(budget_tokens: int, include: list[str]) -> dict[str, int]`
  - `truncate_candidate(candidate: Candidate, share: int) -> int`
  - `pack(budget_tokens: int, include: list[str], candidates: dict[str, list[Candidate]], errors: dict[str, str] | None = None) -> dict` returning `{"tokens_used": int, "sections": [{"kind": str, "items": list[dict], "tokens": int, "error": str | None}], "omitted": {kind: int}}`

**Context the implementer needs:** this module contains no I/O and no imports from `src.db`, `src.mcp` or `asyncio` — its tests run without a database or an event loop. The budgeting rules come from the spec:

- token estimate `ceil(len(text) / 4)`;
- shares memory 15 %, repo_map 25 %, code 30 %, kb 15 %, db 10 %, web 5 %; sections excluded via `include` give their share to the others proportionally;
- a section that uses less than its share releases the remainder to the sections **after** it, in the `SECTION_KINDS` order;
- items enter in score order until the section's available tokens are exhausted; the first item of a section is always included even if it exceeds the share, truncated to the share with `"truncated": true`;
- items not included are counted in `omitted`.

- [ ] **Step 1: Write the failing tests**

Create `services/server/tests/test_context_pack_budget.py`:

```python
"""Budgeting for context_pack: shares, carry-over, truncation, omissions.

Pure arithmetic — no database, no event loop, no monkeypatching.
"""
from src.context_pack import budget


def test_estimate_tokens_is_a_quarter_of_the_characters():
    assert budget.estimate_tokens("") == 0
    assert budget.estimate_tokens("abcd") == 1
    assert budget.estimate_tokens("abcde") == 2


def test_full_budget_splits_by_the_spec_percentages():
    shares = budget.allocate_shares(10000, list(budget.SECTION_KINDS))
    assert shares == {
        "memory": 1500, "repo_map": 2500, "code": 3000,
        "kb": 1500, "db": 1000, "web": 500,
    }


def test_excluded_sections_hand_their_share_to_the_others():
    shares = budget.allocate_shares(10000, ["memory", "code"])
    # 15 and 30 of a 45-point total.
    assert shares == {"memory": 3333, "code": 6666}


def test_validate_args_rejects_out_of_range_budget_and_unknown_sections():
    assert budget.validate_args(6000, None) is None
    assert budget.validate_args(6000, ["memory", "db"]) is None
    assert "between" in budget.validate_args(999, None)
    assert "between" in budget.validate_args(32001, None)
    assert "unknown" in budget.validate_args(6000, ["memory", "twitter"])
    assert "empty" in budget.validate_args(6000, [])


def _candidate(text: str, score: float = 1.0) -> budget.Candidate:
    return budget.Candidate(
        payload={"text": text, "score": score}, text=text,
        truncate_key="text", score=score,
    )


def test_items_enter_in_score_order():
    out = budget.pack(
        10000, ["memory"],
        {"memory": [_candidate("low", 0.1), _candidate("high", 0.9)]},
    )
    assert [i["text"] for i in out["sections"][0]["items"]] == ["high", "low"]


def test_unused_share_flows_to_the_later_sections():
    out = budget.pack(
        10000, ["memory", "code"],
        {
            "memory": [_candidate("x" * 40)],          # 10 tokens of a 3333 share
            "code": [_candidate("y" * 8000, 0.9)],     # 2000 tokens
        },
    )
    memory_section, code_section = out["sections"]
    assert memory_section["tokens"] == 10
    assert code_section["tokens"] == 2000
    assert out["tokens_used"] == 2010
    assert out["omitted"] == {}


def test_items_that_do_not_fit_are_counted_as_omitted():
    out = budget.pack(
        10000, ["memory", "code"],
        {
            "memory": [_candidate("x" * 40)],
            "code": [_candidate("y" * 8000, 0.9), _candidate("z" * 40000, 0.8)],
        },
    )
    code_section = out["sections"][1]
    assert len(code_section["items"]) == 1
    assert out["omitted"] == {"code": 1}


def test_first_item_of_a_section_is_truncated_instead_of_dropped():
    out = budget.pack(
        1000, list(budget.SECTION_KINDS),
        {"memory": [_candidate("x" * 4000)]},   # 1000 tokens against a 150 share
    )
    item = out["sections"][0]["items"][0]
    assert item["truncated"] is True
    assert len(item["text"]) == 600
    assert out["sections"][0]["tokens"] == 150


def test_truncating_a_list_payload_drops_trailing_entries():
    tables = [
        {"name": "users", "columns": ["id", "email"]},
        {"name": "roles", "columns": ["id", "label"]},
        {"name": "sites", "columns": ["id", "title"]},
    ]
    candidate = budget.Candidate(
        payload={"connection": "main", "tables": tables},
        text=budget.list_text(tables), truncate_key="tables", score=1.0,
    )
    used = budget.truncate_candidate(candidate, 15)
    assert candidate.payload["truncated"] is True
    assert len(candidate.payload["tables"]) == 1
    assert used <= 15


def test_sections_carry_their_source_error_and_stay_empty():
    out = budget.pack(
        10000, ["memory", "web"], {"memory": [_candidate("hello")]},
        errors={"web": "web search failed"},
    )
    web_section = out["sections"][1]
    assert web_section["items"] == []
    assert web_section["tokens"] == 0
    assert web_section["error"] == "web search failed"
    assert out["sections"][0]["error"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run from `services/server`:

```
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_context_pack_budget.py
```

Expected: collection error, `ModuleNotFoundError: No module named 'src.context_pack'`.

- [ ] **Step 3: Write the implementation**

Create `services/server/src/context_pack/__init__.py` as an empty file (no content).

Create `services/server/src/context_pack/budget.py`:

```python
"""Token budgeting for context_pack: pure arithmetic, no I/O."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

SECTION_KINDS = ("memory", "repo_map", "code", "kb", "db", "web")

# The spec's percentages as integers: integer arithmetic keeps the split exact.
SECTION_WEIGHTS: dict[str, int] = {
    "memory": 15, "repo_map": 25, "code": 30, "kb": 15, "db": 10, "web": 5,
}

MIN_BUDGET_TOKENS = 1000
MAX_BUDGET_TOKENS = 32000


def estimate_tokens(text: str) -> int:
    """Tokens of a piece of text: no tokenizer, four characters each."""
    if not text:
        return 0
    return math.ceil(len(text) / 4)


def list_text(items: list[Any]) -> str:
    """Canonical text of a list payload; used for both estimate and truncation."""
    return " ".join(str(i) for i in items)


@dataclass
class Candidate:
    payload: dict[str, Any]
    text: str
    truncate_key: Optional[str] = None
    score: float = 0.0


def validate_args(budget_tokens: Any, include: Optional[list[str]]) -> Optional[str]:
    if isinstance(budget_tokens, bool) or not isinstance(budget_tokens, int):
        return "budget_tokens must be an integer"
    if budget_tokens < MIN_BUDGET_TOKENS or budget_tokens > MAX_BUDGET_TOKENS:
        return f"budget_tokens must be between {MIN_BUDGET_TOKENS} and {MAX_BUDGET_TOKENS}"
    if include is not None:
        if not include:
            return "include must not be empty; omit it to get every section"
        unknown = [k for k in include if k not in SECTION_KINDS]
        if unknown:
            return (
                f"unknown include values: {', '.join(unknown)}; "
                f"allowed: {', '.join(SECTION_KINDS)}"
            )
    return None


def allocate_shares(budget_tokens: int, include: list[str]) -> dict[str, int]:
    """Initial share of each requested section; excluded ones split proportionally."""
    kinds = [k for k in SECTION_KINDS if k in include]
    total = sum(SECTION_WEIGHTS[k] for k in kinds)
    if not total:
        return {}
    return {k: budget_tokens * SECTION_WEIGHTS[k] // total for k in kinds}


def truncate_candidate(candidate: Candidate, share: int) -> int:
    """Shrink a candidate to fit `share` tokens; returns the tokens it now costs."""
    key = candidate.truncate_key
    value = candidate.payload.get(key) if key else None
    candidate.payload["truncated"] = True
    if isinstance(value, str):
        candidate.payload[key] = value[: max(0, share) * 4]
        return estimate_tokens(candidate.payload[key])
    if isinstance(value, list):
        items = list(value)
        while items and estimate_tokens(list_text(items)) > share:
            items.pop()
        candidate.payload[key] = items
        return estimate_tokens(list_text(items))
    return max(0, share)


def pack(
    budget_tokens: int,
    include: list[str],
    candidates: dict[str, list[Candidate]],
    errors: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """Fill the requested sections in order, releasing leftovers to the next ones."""
    shares = allocate_shares(budget_tokens, include)
    errors = errors or {}
    sections: list[dict[str, Any]] = []
    omitted: dict[str, int] = {}
    carry = 0
    total_used = 0

    for kind in SECTION_KINDS:
        if kind not in shares:
            continue
        available = shares[kind] + carry
        ordered = sorted(candidates.get(kind) or [], key=lambda c: c.score, reverse=True)
        items: list[dict[str, Any]] = []
        used = 0
        dropped = 0
        for index, candidate in enumerate(ordered):
            cost = estimate_tokens(candidate.text)
            if not items and cost > available:
                used = truncate_candidate(candidate, available)
                items.append(candidate.payload)
                dropped = len(ordered) - 1
                break
            if used + cost > available:
                dropped = len(ordered) - index
                break
            items.append(candidate.payload)
            used += cost
        if dropped:
            omitted[kind] = dropped
        carry = max(0, available - used)
        total_used += used
        sections.append(
            {"kind": kind, "items": items, "tokens": used, "error": errors.get(kind)}
        )

    return {"tokens_used": total_used, "sections": sections, "omitted": omitted}
```

- [ ] **Step 4: Run the tests to verify they pass**

```
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_context_pack_budget.py
```

Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add services/server/src/context_pack services/server/tests/test_context_pack_budget.py
git commit -m "add pure token budget for context pack"
```

---

## Task 2: Tool skeleton — validation, gating, concurrent sources

**Files:**
- Create: `services/server/src/mcp/context_pack.py`
- Modify: `services/server/src/main.py:32` (the tool-module import line)
- Test: `services/server/tests/test_context_pack_tool.py`

**Interfaces:**
- Consumes, from `src.context_pack.budget` (Task 1, already on disk):
  - `SECTION_KINDS = ("memory", "repo_map", "code", "kb", "db", "web")`
  - `validate_args(budget_tokens, include) -> str | None`
  - `pack(budget_tokens, include, candidates: dict[str, list[Candidate]], errors: dict[str, str] | None) -> {"tokens_used": int, "sections": [{"kind", "items", "tokens", "error"}], "omitted": dict}`
  - `@dataclass Candidate(payload: dict, text: str, truncate_key: str | None = None, score: float = 0.0)`
- Produces, in `src.mcp.context_pack`:
  - `SOURCE_TIMEOUT_SECONDS = 5.0`
  - `_SOURCES: dict[str, Callable[[dict], Awaitable[list[Candidate]]]]` — the adapter registry, empty in this task, filled in Tasks 3 and 4.
  - `async def _run_source(kind: str, ctx: dict) -> tuple[str, list, str | None]`
  - the MCP tool `async def context_pack(task, budget_tokens=6000, include=None, repos=None) -> dict`
  - the adapter context dict `ctx = {"task": str, "org_id": int, "project_id": int, "repos": list[str] | None}` passed to every adapter.

**Context the implementer needs:**

- Tools are plain async functions decorated with `@mcp.tool()` from `src/mcp/server.py` (FastMCP 3.4 returns the function unchanged) and gated with `@requires_permission("context-read")` from `src/mcp/permissions.py`. When the caller's permission set contains neither the permission nor `"*"`, the decorator raises `ToolError`; a `None` permission set allows everything.
- Org/project resolution copies `src/mcp/code_graph.py`: `org_id = await resolve_org_id()` and `project_id = await require_project_id()` from `src/mcp/context.py`, both inside a `try` that turns exceptions into `{"status": "error", "error": str(e)}`. `require_project_id()` raises `ToolError("No project selected: ...")` when nothing is selected.
- Existing tools return `{"status": "ok", ...}` / `{"status": "error", "error": ...}`; keep that envelope around the spec's response fields.

- [ ] **Step 1: Write the failing tests**

Create `services/server/tests/test_context_pack_tool.py`:

```python
"""context_pack machinery: gating, validation, concurrency, error isolation.

The sources themselves are faked here; Tasks 3-4 test the real wiring.
"""
import asyncio
import time

import pytest

from src.context_pack import budget
from src.mcp import context, context_pack
from src.mcp.permissions import set_current_permissions


def _underlying(tool):
    return getattr(tool, "fn", tool)


@pytest.fixture(autouse=True)
def _ctx():
    context.set_current_org_id(1)
    context.set_current_project_id(18)
    set_current_permissions(frozenset({"context-read"}))
    yield
    context.set_current_org_id(None)
    context.set_current_project_id(None)
    set_current_permissions(None)


def _fake_source(text, score=1.0):
    async def _source(ctx):
        return [
            budget.Candidate(
                payload={"text": text, "score": score}, text=text,
                truncate_key="text", score=score,
            )
        ]

    return _source


def _call(**kwargs):
    return asyncio.run(_underlying(context_pack.context_pack)(**kwargs))


def _section(out, kind):
    return [s for s in out["sections"] if s["kind"] == kind][0]


def test_requires_the_context_read_permission():
    set_current_permissions(frozenset({"jobs"}))
    with pytest.raises(Exception, match="context-read"):
        _call(task="anything")


def test_rejects_an_empty_task():
    out = _call(task="   ")
    assert out["status"] == "error"
    assert "task" in out["error"]


def test_rejects_a_budget_outside_the_range():
    assert _call(task="t", budget_tokens=999)["status"] == "error"
    assert _call(task="t", budget_tokens=32001)["status"] == "error"


def test_rejects_an_unknown_include_value():
    out = _call(task="t", include=["memory", "twitter"])
    assert out["status"] == "error"
    assert "twitter" in out["error"]


def test_returns_only_the_requested_sections(monkeypatch):
    monkeypatch.setitem(context_pack._SOURCES, "memory", _fake_source("remembered"))
    monkeypatch.setitem(context_pack._SOURCES, "code", _fake_source("def f(): ..."))
    out = _call(task="wire the pack", include=["memory"])
    assert out["status"] == "ok"
    assert out["task"] == "wire the pack"
    assert out["budget_tokens"] == 6000
    assert [s["kind"] for s in out["sections"]] == ["memory"]
    assert out["sections"][0]["items"][0]["text"] == "remembered"
    assert out["tokens_used"] == out["sections"][0]["tokens"] > 0


def test_every_section_is_present_by_default(monkeypatch):
    monkeypatch.setitem(context_pack._SOURCES, "memory", _fake_source("remembered"))
    out = _call(task="t")
    assert [s["kind"] for s in out["sections"]] == list(budget.SECTION_KINDS)


def test_a_failing_source_does_not_fail_the_pack(monkeypatch):
    async def _boom(ctx):
        raise RuntimeError("kb exploded")

    monkeypatch.setitem(context_pack._SOURCES, "kb", _boom)
    monkeypatch.setitem(context_pack._SOURCES, "memory", _fake_source("remembered"))
    out = _call(task="t", include=["memory", "kb"])
    assert out["status"] == "ok"
    assert _section(out, "kb")["items"] == []
    assert "kb exploded" in _section(out, "kb")["error"]
    assert _section(out, "memory")["items"][0]["text"] == "remembered"
    assert _section(out, "memory")["error"] is None


def test_a_hanging_source_times_out_alone(monkeypatch):
    async def _hang(ctx):
        await asyncio.sleep(30)
        return []

    monkeypatch.setattr(context_pack, "SOURCE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setitem(context_pack._SOURCES, "web", _hang)
    monkeypatch.setitem(context_pack._SOURCES, "memory", _fake_source("remembered"))
    out = _call(task="t", include=["memory", "web"])
    assert "timed out" in _section(out, "web")["error"]
    assert _section(out, "memory")["items"][0]["text"] == "remembered"


def test_sources_run_concurrently(monkeypatch):
    async def _slow(ctx):
        await asyncio.sleep(0.2)
        return []

    for kind in ("memory", "code", "kb"):
        monkeypatch.setitem(context_pack._SOURCES, kind, _slow)
    before = time.monotonic()
    _call(task="t", include=["memory", "code", "kb"])
    assert time.monotonic() - before < 0.5


def test_needs_a_selected_project(monkeypatch):
    from src import config

    context.set_current_project_id(None)
    monkeypatch.setattr(context, "_selected_project_for_session", lambda: None)
    monkeypatch.setattr(
        config, "get_settings", lambda: type("S", (), {"mcp_auth_mode": "enabled"})()
    )
    out = _call(task="t")
    assert out["status"] == "error"
    assert "project" in out["error"].lower()


def test_the_tool_module_is_imported_by_main():
    import inspect

    from src import main

    assert "context_pack" in inspect.getsource(main)
```

- [ ] **Step 2: Run the tests to verify they fail**

```
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_context_pack_tool.py
```

Expected: collection error, `ImportError: cannot import name 'context_pack' from 'src.mcp'`.

- [ ] **Step 3: Write the implementation**

Create `services/server/src/mcp/context_pack.py`:

```python
"""context_pack: one call that bundles the context needed to start a task.

Every section reuses the internal coroutine of the matching tool; the
permission is checked once, here.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

from .server import mcp
from .permissions import requires_permission
from .context import require_project_id, resolve_org_id
from ..context_pack import budget as budget_mod

logger = logging.getLogger(__name__)

SOURCE_TIMEOUT_SECONDS = 5.0

# kind -> adapter(ctx) -> list[budget_mod.Candidate]; filled by the wiring below.
_SOURCES: dict[str, Callable[[dict], Awaitable[list[budget_mod.Candidate]]]] = {}


async def _run_source(kind: str, ctx: dict) -> tuple[str, list, Optional[str]]:
    """Run one source under a timeout; a failure never leaves its own section."""
    source = _SOURCES.get(kind)
    if source is None:
        return kind, [], None
    try:
        items = await asyncio.wait_for(source(ctx), timeout=SOURCE_TIMEOUT_SECONDS)
        return kind, items, None
    except asyncio.TimeoutError:
        return kind, [], f"{kind} timed out after {SOURCE_TIMEOUT_SECONDS:g}s"
    except Exception as e:  # noqa: BLE001
        logger.warning("context_pack source %s failed: %s", kind, e)
        return kind, [], str(e)


@mcp.tool()
@requires_permission("context-read")
async def context_pack(
    task: str,
    budget_tokens: int = 6000,
    include: Optional[list[str]] = None,
    repos: Optional[list[str]] = None,
) -> dict:
    """Bundle everything needed to start a task into one budgeted answer.

    Replaces the usual opening sequence — memory_search, repo_map, repo_search,
    kb_search, db_schema, web_search — with a single call: the sources are
    queried concurrently and their best results are packed into `budget_tokens`,
    so you read one response instead of guessing how much of each to fetch.
    Call it first on any new task, then drill down with the individual tools.

    Args:
        task: what you are about to do, in plain words; used as the query for
            every source.
        budget_tokens: total size of the pack, 1000..32000 (default 6000).
        include: subset of ["memory", "repo_map", "code", "kb", "db", "web"];
            omit for all of them. Excluded sections give their share to the rest.
        repos: restrict the repo_map and code sections to these repo names.

    Returns:
        dict with `sections` (one per kind, with items, tokens and a per-source
        error), `tokens_used`, and `omitted` counts per section.
    """
    if not task or not task.strip():
        return {"status": "error", "error": "task must not be empty"}
    invalid = budget_mod.validate_args(budget_tokens, include)
    if invalid:
        return {"status": "error", "error": invalid}

    try:
        org_id = await resolve_org_id()
        project_id = await require_project_id()
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}
    if org_id is None:
        return {"status": "error", "error": "No active organization"}

    kinds = [k for k in budget_mod.SECTION_KINDS if include is None or k in include]
    ctx = {
        "task": task.strip(),
        "org_id": org_id,
        "project_id": project_id,
        "repos": repos,
    }
    results = await asyncio.gather(*[_run_source(kind, ctx) for kind in kinds])
    candidates = {kind: items for kind, items, _ in results}
    errors = {kind: error for kind, _, error in results if error}
    packed = budget_mod.pack(budget_tokens, kinds, candidates, errors=errors)
    return {
        "status": "ok",
        "task": ctx["task"],
        "budget_tokens": budget_tokens,
        **packed,
    }
```

Modify `services/server/src/main.py` line 32 — the current line is:

```python
    from .mcp import memory, repos, jobs, knowledge, web, datasources, contracts, ci, code_tools, code_graph, project_tools, project_admin, ssh_files, git_write_tools  # noqa: F401
```

Replace it with (one line, `context_pack` added after `code_graph`):

```python
    from .mcp import memory, repos, jobs, knowledge, web, datasources, contracts, ci, code_tools, code_graph, context_pack, project_tools, project_admin, ssh_files, git_write_tools  # noqa: F401
```

- [ ] **Step 4: Run the tests to verify they pass**

```
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_context_pack_tool.py
```

Expected: PASS (11 tests).

- [ ] **Step 5: Commit**

```bash
git add services/server/src/mcp/context_pack.py services/server/src/main.py services/server/tests/test_context_pack_tool.py
git commit -m "add context pack tool skeleton"
```

---

## Task 3: Wire the memory, repo_map and code sources

**Files:**
- Modify: `services/server/src/mcp/context_pack.py` (three adapters, registered in `_SOURCES`)
- Test: `services/server/tests/test_context_pack_sources.py`

**Interfaces:**
- Consumes:
  - `src.context_pack.budget.Candidate(payload: dict, text: str, truncate_key: str | None = None, score: float = 0.0)` and `budget.list_text(items: list) -> str` (Task 1).
  - `src.mcp.context_pack._SOURCES` (Task 2), the adapter registry keyed by section kind; each adapter is `async def _x_candidates(ctx: dict) -> list[Candidate]` with `ctx = {"task": str, "org_id": int, "project_id": int, "repos": list[str] | None}`. The tool wraps every adapter in `asyncio.wait_for(..., timeout=SOURCE_TIMEOUT_SECONDS)` and converts exceptions into the section's `error`.
  - `src.mcp.memory.search_memories(query, limit, scope, include_stale) -> list[dict]` — lands with the personal-memory feature; call it with `scope="all"`, `include_stale=False`, `limit=8`. Items are Mem0-shaped dicts: `id`, the text under `memory` (older payloads use `text`), `score`, and `scope` (`"project"` or `"personal"`).
  - `src.mcp.code_graph._edges_and_defs(project_id: int, repos: list[str] | None) -> tuple[list[dict], list[dict]]` — edge rows `{src_file, dst_file, weight}` and definition rows `{file_path, name, kind, repo_name}`.
  - `src.indexer.symbols.rank_files(edge_rows, definition_rows, query=None, limit=25) -> list[dict]` returning `{"file_path": str, "score": float, "defines": list[str]}` (a PageRank over the symbol graph, personalised by the query).
  - `src.search.search_repo_chunks(org_id, query, repos=None, limit=10, project_id=None) -> list[dict]` returning `{"repo_name", "file_path", "chunk_type", "content", "metadata", "score"}`; `metadata` is a dict that carries `start_line` for chunks parsed by tree-sitter.
- Produces: `_SOURCES["memory"]`, `_SOURCES["repo_map"]`, `_SOURCES["code"]` with the spec's item shapes: memory `{id, text, score, scope}`, repo_map `{repo, path, symbols, score}`, code `{repo, path, start_line, end_line, snippet, score}`.

**Context the implementer needs:** adapters import their dependency **inside** the function (late import, as every tool module in `src/mcp` already does) so tests can monkeypatch the module attribute. Section limits come from the spec: memory 8, repo_map 12 files, code 10 chunks. For a candidate whose `truncate_key` points at a list, `text` must be `budget_mod.list_text(payload[key])` so estimate and truncation agree.

- [ ] **Step 1: Write the failing tests**

Create `services/server/tests/test_context_pack_sources.py`:

```python
"""Each context_pack section wires the real internal coroutine of its tool."""
import asyncio

import pytest

from src.mcp import context, context_pack
from src.mcp.permissions import set_current_permissions


def _underlying(tool):
    return getattr(tool, "fn", tool)


@pytest.fixture(autouse=True)
def _ctx():
    context.set_current_org_id(1)
    context.set_current_project_id(18)
    set_current_permissions(frozenset({"context-read"}))
    yield
    context.set_current_org_id(None)
    context.set_current_project_id(None)
    set_current_permissions(None)


def _call(**kwargs):
    return asyncio.run(_underlying(context_pack.context_pack)(**kwargs))


def _section(out, kind):
    return [s for s in out["sections"] if s["kind"] == kind][0]


def test_memory_section_searches_both_scopes(monkeypatch):
    from src.mcp import memory as memory_mod

    calls = {}

    async def _fake_search(query, limit=10, scope="all", include_stale=False):
        calls.update(query=query, limit=limit, scope=scope, include_stale=include_stale)
        return [
            {"id": "m-1", "memory": "we use pgvector", "score": 0.81, "scope": "project"},
            {"id": "m-2", "memory": "filippo prefers uv", "score": 0.4, "scope": "personal"},
        ]

    monkeypatch.setattr(memory_mod, "search_memories", _fake_search, raising=False)
    out = _call(task="how do we store vectors", include=["memory"])

    assert calls == {
        "query": "how do we store vectors", "limit": 8,
        "scope": "all", "include_stale": False,
    }
    items = _section(out, "memory")["items"]
    assert items[0] == {
        "id": "m-1", "text": "we use pgvector", "score": 0.81, "scope": "project",
    }
    assert items[1]["scope"] == "personal"


def test_repo_map_section_ranks_the_symbol_graph(monkeypatch):
    from src.mcp import code_graph

    seen = {}

    async def _fake_edges_and_defs(project_id, repos):
        seen.update(project_id=project_id, repos=repos)
        edges = [{"src_file": "a.py", "dst_file": "core.py", "weight": 4}]
        defs = [
            {"file_path": "core.py", "name": "Scheduler", "kind": "class_definition",
             "repo_name": "context-forge"},
            {"file_path": "a.py", "name": "caller", "kind": "function_definition",
             "repo_name": "context-forge"},
        ]
        return edges, defs

    monkeypatch.setattr(code_graph, "_edges_and_defs", _fake_edges_and_defs)
    out = _call(task="Scheduler", include=["repo_map"], repos=["context-forge"])

    assert seen == {"project_id": 18, "repos": ["context-forge"]}
    item = _section(out, "repo_map")["items"][0]
    assert item["path"] == "core.py"
    assert item["repo"] == "context-forge"
    assert "Scheduler" in item["symbols"]
    assert item["score"] > 0


def test_code_section_uses_the_hybrid_repo_search(monkeypatch):
    from src import search

    seen = {}

    async def _fake_search(org_id, query, repos=None, limit=10, project_id=None):
        seen.update(org_id=org_id, query=query, repos=repos, limit=limit,
                    project_id=project_id)
        return [
            {
                "repo_name": "context-forge", "file_path": "src/search.py",
                "chunk_type": "function_definition",
                "content": "def hybrid_enabled():\n    return True\n",
                "metadata": {"start_line": 45}, "score": 0.77,
            }
        ]

    monkeypatch.setattr(search, "search_repo_chunks", _fake_search)
    out = _call(task="hybrid search toggle", include=["code"], repos=["context-forge"])

    assert seen == {
        "org_id": 1, "query": "hybrid search toggle", "repos": ["context-forge"],
        "limit": 10, "project_id": 18,
    }
    item = _section(out, "code")["items"][0]
    assert item["repo"] == "context-forge"
    assert item["path"] == "src/search.py"
    assert item["start_line"] == 45
    assert item["end_line"] == 47
    assert item["snippet"].startswith("def hybrid_enabled")
    assert item["score"] == 0.77
```

- [ ] **Step 2: Run the tests to verify they fail**

```
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_context_pack_sources.py
```

Expected: 3 failures — the sections come back empty (`IndexError: list index out of range` on `items[0]`) because no adapter is registered yet.

- [ ] **Step 3: Write the implementation**

In `services/server/src/mcp/context_pack.py`, add the section limits next to `SOURCE_TIMEOUT_SECONDS`:

```python
MEMORY_LIMIT = 8
REPO_MAP_LIMIT = 12
CODE_LIMIT = 10
```

Add the three adapters and their registration after `_run_source`, above the `@mcp.tool()` definition:

```python
async def _memory_candidates(ctx: dict) -> list[budget_mod.Candidate]:
    from .memory import search_memories

    rows = await search_memories(
        ctx["task"], limit=MEMORY_LIMIT, scope="all", include_stale=False
    )
    out = []
    for row in rows:
        text = row.get("memory") or row.get("text") or ""
        score = float(row.get("score") or 0.0)
        out.append(
            budget_mod.Candidate(
                payload={
                    "id": row.get("id"), "text": text,
                    "score": score, "scope": row.get("scope"),
                },
                text=text, truncate_key="text", score=score,
            )
        )
    return out


async def _repo_map_candidates(ctx: dict) -> list[budget_mod.Candidate]:
    from ..indexer import symbols as symbols_mod
    from .code_graph import _edges_and_defs

    edge_rows, def_rows = await _edges_and_defs(ctx["project_id"], ctx["repos"])
    repo_by_file = {r["file_path"]: r.get("repo_name") for r in def_rows}
    ranked = symbols_mod.rank_files(
        edge_rows, def_rows, query=ctx["task"], limit=REPO_MAP_LIMIT
    )
    out = []
    for entry in ranked:
        symbols = list(entry.get("defines") or [])
        score = float(entry.get("score") or 0.0)
        out.append(
            budget_mod.Candidate(
                payload={
                    "repo": repo_by_file.get(entry["file_path"]),
                    "path": entry["file_path"], "symbols": symbols, "score": score,
                },
                text=budget_mod.list_text(symbols), truncate_key="symbols", score=score,
            )
        )
    return out


async def _code_candidates(ctx: dict) -> list[budget_mod.Candidate]:
    from ..search import search_repo_chunks

    rows = await search_repo_chunks(
        ctx["org_id"], ctx["task"], repos=ctx["repos"], limit=CODE_LIMIT,
        project_id=ctx["project_id"],
    )
    out = []
    for row in rows:
        content = row.get("content") or ""
        metadata = row.get("metadata") or {}
        start_line = int(metadata.get("start_line") or 0)
        score = float(row.get("score") or 0.0)
        out.append(
            budget_mod.Candidate(
                payload={
                    "repo": row.get("repo_name"), "path": row.get("file_path"),
                    "start_line": start_line,
                    "end_line": start_line + content.count("\n"),
                    "snippet": content, "score": score,
                },
                text=content, truncate_key="snippet", score=score,
            )
        )
    return out


_SOURCES["memory"] = _memory_candidates
_SOURCES["repo_map"] = _repo_map_candidates
_SOURCES["code"] = _code_candidates
```

- [ ] **Step 4: Run the tests to verify they pass**

```
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_context_pack_sources.py tests/test_context_pack_tool.py
```

Expected: PASS (14 tests).

- [ ] **Step 5: Commit**

```bash
git add services/server/src/mcp/context_pack.py services/server/tests/test_context_pack_sources.py
git commit -m "wire memory repo map and code sections"
```

---

## Task 4: Wire the kb, db and web sources

**Files:**
- Modify: `services/server/src/mcp/context_pack.py` (three adapters, registered in `_SOURCES`)
- Modify: `services/server/tests/test_context_pack_sources.py` (append the new tests)

**Interfaces:**
- Consumes:
  - `src.context_pack.budget.Candidate(payload: dict, text: str, truncate_key: str | None = None, score: float = 0.0)` and `budget.list_text(items: list) -> str` (Task 1).
  - `src.mcp.context_pack._SOURCES` and the adapter contract `async def _x_candidates(ctx: dict) -> list[Candidate]` with `ctx = {"task": str, "org_id": int, "project_id": int, "repos": list[str] | None}` (Tasks 2–3). The tool runs every adapter under `asyncio.wait_for(..., timeout=SOURCE_TIMEOUT_SECONDS)` (5 s) and turns exceptions into the section's `error`.
  - `src.kb.store.search_documents(org_id, project_id, query, limit=10, document_ids=None) -> list[dict]` returning `{"document_id", "title", "filename", "extension", "chunk_index", "content", "metadata", "score"}` — exactly what `kb_search` calls.
  - `src.web.store.search_pages(org_id, project_id, query, limit=10, page_ids=None) -> list[dict]` returning `{"page_id", "title", "url", "chunk_index", "content", "metadata", "score"}` — what `web_search` calls.
  - `src.datasources.service.list_connections(org_id, project_id) -> list[dict]`, each with `name`, `engine`, `host`, `database_name`, `description`, `status`, `annotation_count`.
  - `src.datasources.service.schema_overview(org_id, project_id, ref, schema=None) -> dict` with `{"dialect", "default_schema", "schema", "schemas", "views", "connection", "connection_id", "tables": [{"name", "comment", "column_count", "estimated_rows", "description"}]}` — `ref` is the connection name; the overview has **no** column names.
  - `src.datasources.service.describe_table(org_id, project_id, ref, table, schema=None, sample_rows=0) -> dict` with `{"columns": [{"name", "type", "nullable", ...}], ...}` — this is where column names come from.
  - `src.indexer.symbols.query_tokens(query: str | None) -> set[str]` — plausible identifiers extracted from a natural-language question; used for the table/task overlap score.
- Produces: `_SOURCES["kb"]`, `_SOURCES["db"]`, `_SOURCES["web"]` with the spec's item shapes: kb `{document, title, excerpt, score}`, db `{connection, tables: [{name, columns}]}`, web `{url, title, excerpt, score}`.

**Context the implementer needs:** section limits from the spec — kb 6, web 4, db top 8 tables across the project's connections with column names only. Connections are iterated in `list_connections` order; a connection whose overview fails is skipped (logged at info) instead of failing the section. The `describe_table` calls for the selected tables run in one `asyncio.gather(..., return_exceptions=True)`; a table whose description fails contributes `"columns": []`.

- [ ] **Step 1: Write the failing tests**

Append to `services/server/tests/test_context_pack_sources.py`:

```python
def test_kb_section_uses_the_document_search(monkeypatch):
    from src.kb import store as kb_store

    seen = {}

    async def _fake_documents(org_id, project_id, query, limit=10, document_ids=None):
        seen.update(org_id=org_id, project_id=project_id, query=query, limit=limit)
        return [
            {
                "document_id": 7, "title": "Runbook", "filename": "runbook.pdf",
                "extension": "pdf", "chunk_index": 2,
                "content": "restart the indexer with docker compose restart",
                "metadata": {}, "score": 0.7,
            }
        ]

    monkeypatch.setattr(kb_store, "search_documents", _fake_documents)
    out = _call(task="how do I restart the indexer", include=["kb"])

    assert seen == {
        "org_id": 1, "project_id": 18,
        "query": "how do I restart the indexer", "limit": 6,
    }
    assert _section(out, "kb")["items"][0] == {
        "document": "runbook.pdf", "title": "Runbook",
        "excerpt": "restart the indexer with docker compose restart", "score": 0.7,
    }


def test_web_section_uses_the_page_search(monkeypatch):
    from src.web import store as web_store

    seen = {}

    async def _fake_pages(org_id, project_id, query, limit=10, page_ids=None):
        seen.update(org_id=org_id, project_id=project_id, query=query, limit=limit)
        return [
            {
                "page_id": 3, "title": "asyncpg docs", "url": "https://example.test/pool",
                "chunk_index": 0, "content": "pool acquire returns a connection",
                "metadata": {}, "score": 0.55,
            }
        ]

    monkeypatch.setattr(web_store, "search_pages", _fake_pages)
    out = _call(task="asyncpg pool", include=["web"])

    assert seen == {"org_id": 1, "project_id": 18, "query": "asyncpg pool", "limit": 4}
    assert _section(out, "web")["items"][0] == {
        "url": "https://example.test/pool", "title": "asyncpg docs",
        "excerpt": "pool acquire returns a connection", "score": 0.55,
    }


def test_db_section_ranks_tables_by_the_task_and_lists_their_columns(monkeypatch):
    from src.datasources import service

    described = []

    async def _fake_list(org_id, project_id):
        return [{"name": "main", "engine": "postgresql"}]

    async def _fake_overview(org_id, project_id, ref, schema=None):
        return {
            "schema": "public", "connection": ref,
            "tables": [
                {"name": "invoices", "description": "billing invoices",
                 "comment": None, "column_count": 4},
                {"name": "unrelated", "description": None,
                 "comment": None, "column_count": 2},
            ],
        }

    async def _fake_describe(org_id, project_id, ref, table, schema=None, sample_rows=0):
        described.append((ref, table, schema))
        return {"columns": [{"name": "id"}, {"name": "total"}]}

    monkeypatch.setattr(service, "list_connections", _fake_list)
    monkeypatch.setattr(service, "schema_overview", _fake_overview)
    monkeypatch.setattr(service, "describe_table", _fake_describe)
    out = _call(task="where are invoices stored", include=["db"])

    item = _section(out, "db")["items"][0]
    assert item["connection"] == "main"
    assert item["tables"][0] == {"name": "invoices", "columns": ["id", "total"]}
    assert described[0] == ("main", "invoices", "public")


def test_db_section_survives_a_table_that_cannot_be_described(monkeypatch):
    from src.datasources import service

    async def _fake_list(org_id, project_id):
        return [{"name": "main", "engine": "postgresql"}]

    async def _fake_overview(org_id, project_id, ref, schema=None):
        return {"schema": "public", "tables": [{"name": "invoices"}]}

    async def _boom(org_id, project_id, ref, table, schema=None, sample_rows=0):
        raise RuntimeError("permission denied")

    monkeypatch.setattr(service, "list_connections", _fake_list)
    monkeypatch.setattr(service, "schema_overview", _fake_overview)
    monkeypatch.setattr(service, "describe_table", _boom)
    out = _call(task="invoices", include=["db"])

    section = _section(out, "db")
    assert section["error"] is None
    assert section["items"][0]["tables"][0] == {"name": "invoices", "columns": []}


def test_db_section_skips_a_connection_whose_schema_is_unreachable(monkeypatch):
    from src.datasources import service

    async def _fake_list(org_id, project_id):
        return [
            {"name": "down", "engine": "mysql"},
            {"name": "up", "engine": "postgresql"},
        ]

    async def _fake_overview(org_id, project_id, ref, schema=None):
        if ref == "down":
            raise RuntimeError("connection refused")
        return {"schema": "public", "tables": [{"name": "invoices"}]}

    async def _fake_describe(org_id, project_id, ref, table, schema=None, sample_rows=0):
        return {"columns": [{"name": "id"}]}

    monkeypatch.setattr(service, "list_connections", _fake_list)
    monkeypatch.setattr(service, "schema_overview", _fake_overview)
    monkeypatch.setattr(service, "describe_table", _fake_describe)
    out = _call(task="invoices", include=["db"])

    assert [i["connection"] for i in _section(out, "db")["items"]] == ["up"]
```

- [ ] **Step 2: Run the tests to verify they fail**

```
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_context_pack_sources.py
```

Expected: the 5 new tests fail (empty sections: `IndexError` / `assert [] == [...]`); the 3 from Task 3 still pass.

- [ ] **Step 3: Write the implementation**

In `services/server/src/mcp/context_pack.py`, add the limits next to the existing ones:

```python
KB_LIMIT = 6
WEB_LIMIT = 4
DB_TABLE_LIMIT = 8
```

Add the three adapters next to the others, and register them below the existing `_SOURCES[...] = ...` lines:

```python
async def _kb_candidates(ctx: dict) -> list[budget_mod.Candidate]:
    from ..kb import store as kb_store

    rows = await kb_store.search_documents(
        ctx["org_id"], ctx["project_id"], ctx["task"], limit=KB_LIMIT
    )
    out = []
    for row in rows:
        excerpt = row.get("content") or ""
        score = float(row.get("score") or 0.0)
        out.append(
            budget_mod.Candidate(
                payload={
                    "document": row.get("filename") or str(row.get("document_id")),
                    "title": row.get("title"), "excerpt": excerpt, "score": score,
                },
                text=excerpt, truncate_key="excerpt", score=score,
            )
        )
    return out


async def _web_candidates(ctx: dict) -> list[budget_mod.Candidate]:
    from ..web import store as web_store

    rows = await web_store.search_pages(
        ctx["org_id"], ctx["project_id"], ctx["task"], limit=WEB_LIMIT
    )
    out = []
    for row in rows:
        excerpt = row.get("content") or ""
        score = float(row.get("score") or 0.0)
        out.append(
            budget_mod.Candidate(
                payload={
                    "url": row.get("url"), "title": row.get("title"),
                    "excerpt": excerpt, "score": score,
                },
                text=excerpt, truncate_key="excerpt", score=score,
            )
        )
    return out


async def _db_candidates(ctx: dict) -> list[budget_mod.Candidate]:
    from ..datasources import service
    from ..indexer.symbols import query_tokens

    connections = await service.list_connections(ctx["org_id"], ctx["project_id"])
    tokens = {t.lower() for t in query_tokens(ctx["task"])}
    ranked: list[tuple[float, str, str, Optional[str]]] = []
    for connection in connections:
        name = connection.get("name")
        try:
            overview = await service.schema_overview(ctx["org_id"], ctx["project_id"], name)
        except Exception as e:  # noqa: BLE001
            logger.info("context_pack: no schema for connection %s: %s", name, e)
            continue
        schema = overview.get("schema")
        for table in overview.get("tables") or []:
            haystack = " ".join(
                str(table.get(k) or "") for k in ("name", "description", "comment")
            ).lower()
            score = float(sum(1 for t in tokens if t in haystack))
            ranked.append((score, name, table.get("name"), schema))

    ranked.sort(key=lambda r: (-r[0], r[1] or "", r[2] or ""))
    top = ranked[:DB_TABLE_LIMIT]
    details = await asyncio.gather(
        *[
            service.describe_table(
                ctx["org_id"], ctx["project_id"], conn, table, schema=schema
            )
            for _, conn, table, schema in top
        ],
        return_exceptions=True,
    )

    by_connection: dict[str, list[dict]] = {}
    scores: dict[str, float] = {}
    for (score, conn_name, table_name, _), detail in zip(top, details):
        columns: list[str] = []
        if not isinstance(detail, BaseException):
            columns = [c["name"] for c in detail.get("columns") or []]
        by_connection.setdefault(conn_name, []).append(
            {"name": table_name, "columns": columns}
        )
        scores[conn_name] = max(scores.get(conn_name, 0.0), score)

    return [
        budget_mod.Candidate(
            payload={"connection": conn_name, "tables": tables},
            text=budget_mod.list_text(tables), truncate_key="tables",
            score=scores.get(conn_name, 0.0),
        )
        for conn_name, tables in by_connection.items()
    ]


_SOURCES["kb"] = _kb_candidates
_SOURCES["db"] = _db_candidates
_SOURCES["web"] = _web_candidates
```

- [ ] **Step 4: Run the tests to verify they pass**

```
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_context_pack_sources.py tests/test_context_pack_tool.py tests/test_context_pack_budget.py
```

Expected: PASS (27 tests).

- [ ] **Step 5: Commit**

```bash
git add services/server/src/mcp/context_pack.py services/server/tests/test_context_pack_sources.py
git commit -m "wire kb db and web sections"
```

---

## Task 5: Document the tool and verify the whole suite

**Files:**
- Modify: `README.md` (repo root) — "Features" list and "MCP tools" list
- Modify: `templates/CLAUDE.md` (repo root)
- Modify: `templates/AGENTS.md` (repo root)
- Test: the full server suite

**Interfaces:**
- Consumes: the finished tool `context_pack(task, budget_tokens=6000, include=None, repos=None)` from `src/mcp/context_pack.py`, gated with the existing `context-read` permission (no new permission, so the README permissions table stays as it is), with sections `memory`, `repo_map`, `code`, `kb`, `db`, `web`.
- Produces: documentation only.

- [ ] **Step 1: Add the README feature bullet**

In `README.md`, in the `## Features` list, insert this bullet immediately after the `- **Persistent memory** — ...` bullet:

```markdown
- **Task context pack** — `context_pack` answers "what do I need to know to start this task" in one round trip: memory, the ranked repo map, code chunks, knowledge-base passages, database schema and web pages are queried concurrently and packed into a token budget, each source with its own timeout so a slow one never blocks the answer.
```

- [ ] **Step 2: Add the README tools line**

In `README.md`, in the `## MCP tools` list, insert a new first bullet above `- **Memory:** \`memory_add\`, ...`:

```markdown
- **Task context:** `context_pack` — one call that bundles memory, the ranked repo map, code chunks, knowledge-base passages, database schema and web pages for a task, packed into a token budget (`budget_tokens`, default 6000); call it before the individual search tools
```

- [ ] **Step 3: Update `templates/CLAUDE.md`**

Under `## Mandatory Behaviors`, above the existing `**At the start of every session:**` block, insert (the inner fence is a real code block in the template):

````markdown
**At the start of every task:**
```
context_pack("what you are about to do")
```
One call returns memory, the ranked repo map, code, documents, database schema
and web pages for that task, inside a token budget. Use the tools below only
for what the pack did not cover.

````

Then, under `## Available Tools`, insert this section above `### Memory (persistent across all sessions)`:

```markdown
### Task context
| Tool | When to use |
|------|-------------|
| `context_pack(task, budget_tokens?, include?, repos?)` | First call on any new task: memory + repo map + code + documents + DB schema + web, budgeted |
```

- [ ] **Step 4: Update `templates/AGENTS.md`**

Insert a new section between the intro line (`You have access to **context-forge**, ...`) and `## Memory`:

```markdown
## Task context

- **`context_pack(task, budget_tokens?, include?, repos?)`** — Start here. One call bundles the relevant memories, the ranked repo map, code chunks, knowledge-base passages, external database schema and scraped web pages for the task you describe, packed into `budget_tokens` (default 6000, range 1000–32000). Restrict it with `include=["memory", "code"]` or `repos=["my-repo"]`. Use the specific tools below to go deeper on whatever the pack surfaced.
```

In the `## Best Practices` numbered list, add a new first item and renumber the existing ones (1→2, 2→3, 3→4, 4→5):

```markdown
1. **Call `context_pack` first** on any new task, then use the specific tools for depth.
```

- [ ] **Step 5: Run the full server suite**

From `services/server`:

```
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider
```

Expected: the whole suite passes, including the three new files. If a pre-existing test fails, report it rather than editing it.

- [ ] **Step 6: Commit**

```bash
git add README.md templates/CLAUDE.md templates/AGENTS.md
git commit -m "document the context pack tool"
```

---

## Self-Review

**Spec coverage**

| Spec requirement | Task |
|---|---|
| Tool `context_pack(task, budget_tokens=6000, include, repos)` in `src/mcp/context_pack.py`, gated `context-read` | 2 |
| Response `task`, `budget_tokens`, `tokens_used`, `sections[kind/items/tokens/error]`, `omitted` | 1 (shape) + 2 (envelope) |
| `estimate_tokens` = `ceil(len/4)` | 1 |
| Shares 15/25/30/15/10/5 with proportional redistribution on `include` | 1 |
| Leftover released to the sections after it | 1 |
| Score order, first-item truncation with `truncated`, `omitted` counts | 1 |
| Concurrent sources, 5 s timeout each, per-source error, one failure never fails the pack | 2 |
| memory → `search_memories`, scope `all`, limit 8 | 3 |
| repo_map → `_edges_and_defs` + `rank_files`, top 12 | 3 |
| code → `search_repo_chunks`, top 10 | 3 |
| kb → `kb.store.search_documents`, top 6 | 4 |
| db → `list_connections` + `schema_overview` ranked by task token overlap, top 8 tables, column names only | 4 |
| web → `web.store.search_pages`, top 4 | 4 |
| Project scoping from the MCP context (`require_project_id`) | 2 |
| Tests: budget shares, `include`, remainder, truncation, omitted, `estimate_tokens`, timeout and error isolation, permission gate, argument validation | 1, 2 |
| README tools list "Task context"; templates suggest calling it first | 5 |

**Resolved spec ambiguities**

- The spec's response example has no `status` key; every existing tool returns one, so the payload is `{"status": "ok", ...}` plus the spec's fields (`{"status": "error", "error": ...}` for validation and context failures).
- `error` is present on every section (`None` when the source succeeded), not only on `web`.
- Shares are integer weights (15/25/30/15/10/5) rather than floats, so `int(budget * 0.30)` cannot round down to 2999.
- "Releases the remainder to the sections after it" is a carry accumulated in `SECTION_KINDS` order: a section's available budget is its share plus everything unspent before it.
- Item fields the spec left open: kb `document` = the document's filename (its id as a string when absent); web items are `{url, title, excerpt, score}`; code `end_line` = `start_line + snippet.count("\n")` because repo-chunk metadata stores only `start_line`.
- `schema_overview` returns no column names, so the db section describes only the top-ranked tables with `describe_table` (one `asyncio.gather`, capped at 8 tables across all connections); a table that cannot be described keeps `columns: []`, a connection whose overview fails is skipped.
- Truncation of list-valued payloads (`symbols`, `tables`) drops trailing entries until the section share fits, using the same `list_text` rendering the estimate used; the labels around those lists (file path, connection name) are not counted in the estimate.
