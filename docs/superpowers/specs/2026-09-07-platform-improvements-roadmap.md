# Platform improvements roadmap (2026-09-07)

Nine features approved for context-forge, executed sequentially on branch
`feature/platform-improvements` with subagent-driven development. Each feature
has its own spec (this directory) and plan (`docs/superpowers/plans/`).

| # | Feature | Spec | Migration slot |
|---|---|---|---|
| 1 | Versioned schema migrations | `2026-09-07-versioned-migrations-design.md` | `0001_baseline` |
| 2 | HNSW vector indexes per organization | `2026-09-07-hnsw-indexes-design.md` | none (runtime-managed indexes) |
| 3 | Operational robustness (job retry, metrics, health) | `2026-09-07-ops-robustness-design.md` | `0002_jobs_retry` |
| 4 | MCP tool audit and per-key rate limits | `2026-09-07-mcp-audit-ratelimit-design.md` | `0003_mcp_tool_calls` |
| 5 | Human approvals for write tools | `2026-09-07-write-approvals-design.md` | `0004_write_requests` |
| 6 | More languages in the symbol graph | `2026-09-07-symbol-languages-design.md` | none |
| 7 | Personal memory and consolidation | `2026-09-07-memory-personal-consolidation-design.md` | `0005_memory_stats` |
| 8 | `context_pack` tool | `2026-09-07-context-pack-design.md` | none |
| 9 | Retrieval evaluation and reranker | `2026-09-07-retrieval-eval-reranker-design.md` | `0006_eval` |

Execution order is the table order. Feature 1 must land first: every later
schema change is a migration module, never an edit to the inline DDL string.

## Global constraints (bind every plan)

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
- Code comments: short, one line where possible, only where needed. Commit titles 3–10 words,
  English, imperative; body optional, max 20 words.
- README: each feature updates the sections it touches (features, tools,
  env vars, permissions) in the same plan.
- Do not touch `services/server/src/api/routes/product_*`: it does not exist
  here and must not be recreated.
