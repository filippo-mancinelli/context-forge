# Second backport: org catalog, repo catalog, database scopes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port into context-forge the three generic features the downstream fork added after the first backport: the organization catalog, repositories in the catalog, and per-project database scopes, plus the SQL validator hardening and the connection reset fix.

**Architecture:** This is a port, not a rewrite. The source of truth for behaviour is the downstream repository at three consistent snapshots. Files changed only downstream are taken from the snapshot and renamed; files changed on both sides are merged by hand on top of the context-forge version; schema changes become additive migration modules, and the three data conversions stay Python functions run at boot.

**Tech Stack:** FastAPI, FastMCP, asyncpg (raw SQL), PostgreSQL 16 + pgvector, APScheduler, React 18 + TypeScript + Vite + Tailwind + Radix UI, pytest, vitest.

**Spec:** the approved plan of 2026-09-29 (summarised in "Decisions" below). There is no separate design document: the downstream code at the snapshots is the specification of behaviour.

## Source repository and snapshots

The downstream fork is checked out at `../aster-care` (sibling of this repository). It is read-only for this work: never commit, stage or edit anything there.

| Phase | Snapshot | Range to port | Content |
|---|---|---|---|
| A | `bd31b47` | `82188a5..bd31b47` | catalog of machines, SSH folders, databases |
| B | `88e49a1` | `bd31b47..88e49a1` | repositories in the catalog |
| C | `797d305` | `88e49a1..797d305` | database scopes, validator hardening, connection reset |

Read a source file with `git -C ../aster-care show <snapshot>:<path>`. Read what a phase changed in a file with `git -C ../aster-care diff <range> -- <path>`. The per-phase file lists are in the SDD workspace: `phase-{A,B,C}-server.txt` and `phase-{A,B,C}-ui.txt`.

## Decisions

- All three features are ported, with the validator hardening exactly as downstream, including the block on `information_schema` and `pg_*`.
- Tests that need a real PostgreSQL are ported and are skipped when `TEST_DATABASE_URL` is unset. The default suite still needs no database.
- Excluded: the downstream design kit (`design-kit/`, `components/shell/`, new kit components, fonts, favicon, login wallpaper, tokens), product connectors (`product_ops`, `ProductConnections*`), `OAuth.tsx`, `scripts/rollback_db_scope_release.sql`, kit-only UI tests.
- No push and no merge: work stays on branch `feature/backport-aster-care-2`.

## Global Constraints

- Server: Python 3.11 in Docker, local dev on 3.14; code root `services/server/src` (import root `src.*`), tests in `services/server/tests`, run with `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider` from `services/server`.
- All DB access is raw SQL through `src.db.get_pool()` (asyncpg). No ORM.
- The inline DDL in `src/db.py` is frozen. Schema changes are modules in `src/migrations/versions/NNNN_<name>.py` exposing `VERSION`, `NAME`, `TRANSACTIONAL`, `async def upgrade(conn)`; the module must not open its own transaction; use `IF NOT EXISTS` forms.
- MCP tools live in `src/mcp/*.py`, are imported on the single tool-import line of `src/main.py`, and are gated with `@requires_permission(...)` from `src/mcp/permissions.py`, which audits and rate-limits the call. A refusal raises `PermissionDenied`. Tools without a permission use `@audit_only`.
- Tests without a database use `tests/fake_db.py` (`FakeConn`, `FakePool`) where they already do. Tests with a database use the `pg_database` fixture and `tests/pgutil.py`.
- UI: React 18 + TypeScript in `services/ui/src`; kit in `components/ui` (context-forge's own kit, unchanged by this work); verify with `npx tsc --noEmit`, `npm run build`, `npx vitest run` from `services/ui`. UI copy is English.
- Naming: the product is `context-forge` / `ContextForge`. Never `aster` outside test fixtures that are plain data. Apply the rename map to every file taken from a snapshot, in this order: `Aster Care`→`ContextForge`, `aster-care-ui`→`context-forge-ui`, `aster-care-server`→`context-forge-server`, `aster-care-api`→`context-forge-api`, `aster-care.yml`→`context-forge.yml`, `aster-care`→`context-forge`, `aster_care`→`context_forge`, `ASTER_CARE`→`CONTEXT_FORGE`, `asterchat-sql-agent validator`→`an internal SQL-agent validator`, `context-forge@example.org`→`context-forge@example.com`.
- Code comments: short, one line where possible, only where needed. Comments copied from a snapshot may stay as they are.
- Commit titles 3–10 words, English, imperative; body optional, max 20 words.
- Git: stage explicit paths only; never `git add -A`/`.`; never `git reset`, `--amend`, `rebase`, `stash`, `checkout`; never `git push`. LF, UTF-8.
- Never touch Docker volumes or containers other than the disposable test database named `cf-backport-test-pg`.

## How each file is treated

- **Taken from the snapshot** (changed only downstream): replace the context-forge file with the snapshot version, apply the rename map, remove any reference to `product_ops`.
- **New file**: take it from the snapshot, apply the rename map, adopt the context-forge conventions listed in the task.
- **Merged by hand** (changed on both sides): start from the context-forge file and apply the downstream change from the phase range. Never replace a merged file with the snapshot version: that would delete context-forge features.

Files merged by hand, across all phases: `api/app.py`, `api/routes/settings.py`, `config.py`, `main.py`, `tenancy.py`, `indexer/indexer.py`, `scheduler.py`, `search.py`, `db.py`, `mcp/datasources.py`, `mcp/ssh_files.py`.

---

### Task 1: PostgreSQL test infrastructure

**Files:**
- Create: `services/server/tests/conftest.py`, `services/server/tests/pgutil.py`, `services/server/tests/test_pg_fixture.py`
- Modify: `services/server/pyproject.toml` (dev extras)

**Interfaces:**
- Produces: fixture `pg_database`; `pgutil.TEST_DATABASE_URL`, `requires_pg`, `database_url`, `run_db`, `prepare_schema`, `fetch`, `fetchval`, `execute`, `seed_org`, `seed_project`, `seed_user`, `seed_project_member`.

- [ ] **Step 1:** Take `conftest.py`, `pgutil.py` and `test_pg_fixture.py` from snapshot `bd31b47`. In `conftest.py` name the throwaway database `cf_test_<hex>`.
- [ ] **Step 2:** In this task `pgutil.prepare_schema` only calls `await db.init_db()` (which runs the migration runner). Leave out `make_legacy_schema` and the conversion calls: Task 2 adds them. Keep English or the original comments, no product names.
- [ ] **Step 3:** Add `"anthropic>=0.40.0"` to the dev extras in `pyproject.toml` with a one-line English comment, and install with `uv pip install --python .venv/Scripts/python.exe -e ".[dev]"` (use the extras name the file actually declares).
- [ ] **Step 4:** Run the suite without `TEST_DATABASE_URL`: everything passes and the fixture test is skipped. Run `tests/test_pg_fixture.py` with `TEST_DATABASE_URL` set to the value given in the dispatch: it passes.
- [ ] **Step 5:** Commit: `Add PostgreSQL test fixtures, skipped without a database`.

### Task 2: Phase A — organization catalog (server)

**Files:**
- Create: `src/migrations/versions/0005_org_catalog.py`; `src/catalog/{__init__,machines,names,selections,migration}.py`; `src/api/routes/catalog.py`; `src/api/routes/project_resources.py`; `src/mcp/catalog_tools.py`; the test files added in `phase-A-server.txt`
- Take from snapshot `bd31b47`: `src/ssh_sources/service.py`, `src/datasources/service.py`, `src/api/routes/ssh_sources.py`, `src/api/routes/datasources.py`, `src/mcp/project_access.py`, `src/mcp/project_admin.py`, `src/mcp/server.py`, `src/projects.py`, and the test files modified in `phase-A-server.txt`
- Merge by hand: `src/db.py`, `src/tenancy.py`, `src/api/app.py`, `src/main.py`, `src/mcp/ssh_files.py`, `src/mcp/datasources.py`

**Interfaces:**
- Produces: tables `machines`, `project_ssh_sources`; REST `/api/catalog/{machines,folders,databases}` and `/api/projects/{pid}/resources`; MCP tools `catalog_list`, `resource_select`, `resource_deselect`; `catalog.migration.apply_catalog_migration()`.

- [ ] **Step 1: Schema module.** `0005_org_catalog.py` (`VERSION = 5`, `NAME = "org_catalog"`, transactional) holds only the additive DDL that phase A added to the downstream inline DDL: new tables and their indexes, new nullable columns on `ssh_sources` and `db_connections`, and the intermediate link table the conversion needs. Do not drop or rewrite anything in it. Do not edit the DDL string in `src/db.py`.
- [ ] **Step 2: `src/db.py` and `src/tenancy.py`.** Port the downstream changes to the Python functions only (`apply_project_migration`, guards), not to the DDL string. In `tenancy.ensure_tenant_storage()` keep the existing calls and add the conversion after `apply_settings_overrides_migration`. The catalog conversions must also run when no default organization exists yet (a fresh database before setup), so that the schema reaches its final shape before the first request: call them outside the `default_org_id is None` early return, and verify their guards are no-ops on empty tables.
- [ ] **Step 3: Catalog modules, routes, services.** New files and files taken from the snapshot, with the rename map.
- [ ] **Step 4: `src/mcp/ssh_files.py`.** Keep the context-forge proposal flow of `ssh_write_file` (`alternatives=("context-write",)`, `_propose_ssh_write`, `client.confined_path`). Apply the downstream machine and selection changes. `_propose_ssh_write` must not read `record["project_id"]`: the record no longer has it; use the current project from the MCP context.
- [ ] **Step 5: `src/mcp/datasources.py`.** Apply the downstream `db_add` rework. Keep `db_execute` and `_propose_db_execute` as they are in context-forge.
- [ ] **Step 6: Conventions.** In `catalog_tools` and in `db_add`/`ssh_source_add`, a refusal for missing rights raises `PermissionDenied` instead of returning `{"status": "error"}`. `selection_rights` decides from `get_current_principal().kind`, not from `user_id is None`. Adjust the ported tests for these two changes.
- [ ] **Step 7: Wiring.** `src/api/app.py`: include the two routers next to the existing ones. `src/main.py`: add `catalog_tools` to the tool-import line.
- [ ] **Step 8: Tests.** Port the phase A tests. Extend `pgutil` with `make_legacy_schema` and make `prepare_schema` run `init_db()` then `apply_catalog_migration()`. Adjust context-forge tests whose fixtures carry the old record shape (`tests/test_write_request_tools.py` SSH record).
- [ ] **Step 9: Verify.** Full suite without a database: green. Full suite with `TEST_DATABASE_URL`: green, no `*_pg.py` test skipped.
- [ ] **Step 10: Commit** in two or three commits (schema and conversion; services, routes and tools; tests may go with the code they cover).

### Task 3: Phase B — repositories in the catalog (server)

**Files:**
- Create: `src/migrations/versions/0006_repo_catalog.py`; `src/catalog/repos.py`; `src/catalog/repo_migration.py`; tests added in `phase-B-server.txt`
- Delete: `src/repo_registry.py`, `tests/test_repo_registry.py`, `tests/test_repo_add_project_scope.py`
- Take from snapshot `88e49a1`: every `src` file in `phase-B-server.txt` that is not in the merge list, and the modified tests
- Merge by hand: `src/db.py`, `src/tenancy.py`, `src/config.py`, `src/search.py`, `src/indexer/indexer.py`, `src/scheduler.py`, `src/api/routes/settings.py`

**Interfaces:**
- Consumes: Task 2's catalog modules and `selections`.
- Produces: `config.RepoRecord`; `catalog.repos` service; table `project_repos`; `repo_id` keys; `apply_repo_catalog_migration()`.

- [ ] **Step 1: Schema module** `0006_repo_catalog.py` (`VERSION = 6`, `NAME = "repo_catalog"`): additive DDL only (new columns on `repos`, nullable `repo_id` on `repo_chunks`, `repo_symbols`, `chunk_annotations`, `index_requests`, the new index). Constraints, `project_repos` and the drops belong to the conversion, as downstream.
- [ ] **Step 2: Conversion.** Read `catalog/repo_migration.py` in full. It moves clone directories and drops columns. If two server instances booting together could run it concurrently, serialise it with a dedicated `pg_advisory_lock`, and say so in the report. Hook it in `ensure_tenant_storage()` after `apply_catalog_migration`.
- [ ] **Step 3: `src/search.py`.** The context-forge statements are per-dimension builder functions using `vector_expr`. Apply the downstream filter through `project_repos`/`repo_id` inside those builders. Every vector CTE must keep the predicate `c.org_id = $2`: the HNSW indexes are partial per organization and are not used without it. Keep the session knobs and `force_custom_plan`.
- [ ] **Step 4: `src/indexer/indexer.py`.** Keep the parser loader tables and the new languages. Apply `RepoRecord`, storage by `repo_id`, `run_pending_index_requests`, and remove `sync_repos_config`.
- [ ] **Step 5: `src/scheduler.py` and `src/api/routes/settings.py`.** Replace the repo registry with the catalog. Keep every context-forge job (jobs, metrics, audit retention, HNSW, write request expiry) and the embedding dimension probe.
- [ ] **Step 6: Tests.** Port phase B tests; extend `pgutil` (`make_legacy_repo_schema`, conversion in `prepare_schema`). Adjust `tests/test_search_hnsw_sql.py`, `tests/test_repo_project_scope.py`, `tests/test_settings_org_scope.py`, `tests/test_indexer_project_scope.py`, `tests/test_symbol_rows_persist.py` if present. Add assertions that every vector CTE built by `search.py` contains `org_id = $2`.
- [ ] **Step 7: Verify** both ways as in Task 2, then commit.

### Task 4: Phase C — database scopes and validator (server)

**Files:**
- Create: `src/migrations/versions/0007_db_scopes.py`; `src/datasources/{scopes,project_scopes,scope_catalog,scope_migration}.py`; tests added in `phase-C-server.txt`
- Take from snapshot `797d305`: `src/datasources/{service,validator,engines,introspect}.py`, `src/api/routes/{catalog,chat,datasources,project_resources}.py`, `src/catalog/{migration,selections}.py`, `src/mcp/catalog_tools.py`, `src/projects.py`, modified tests. Re-apply Task 2's convention changes to files taken again from a snapshot.
- Merge by hand: `src/db.py`, `src/tenancy.py`, `src/mcp/datasources.py`
- Not ported: `scripts/rollback_db_scope_release.sql`

**Interfaces:**
- Consumes: Tasks 2 and 3.
- Produces: table `project_db_scopes`; scope aliases accepted by `db_*` tools; `validate_query(sql, allowed_schema=None)`, `validate_write_query(sql, allowed_schema=None)`; `apply_db_scope_migration()`.

- [ ] **Step 1: Schema module** `0007_db_scopes.py` (`VERSION = 7`, `NAME = "db_scopes"`): `project_db_scopes` with its three indexes, `db_connections.available_scopes` and `scopes_checked_at`, `db_query_log.schema_name`.
- [ ] **Step 2: Conversion** hooked after `apply_repo_catalog_migration`. It keeps its own advisory lock.
- [ ] **Step 3: `src/mcp/datasources.py`.** Keep the context-forge proposal flow. `db_execute` keeps `alternatives=("context-write",)` and `reason`. The proposal path resolves the scope record first, validates the statement with the scope's schema, turns a scope violation into a refusal, and stores the **scope alias** as the request `target`, so an approved write runs on the scope it was proposed for. The read tools accept an alias or a scope id as downstream.
- [ ] **Step 4:** `src/write_requests.py` and `src/write_previews.py` stay unchanged. Adjust their tests to the new record shape.
- [ ] **Step 5: Tests.** Port phase C tests; extend `pgutil` (`make_legacy_db_links`). Add a test that an approved `db_execute` request runs against the alias stored at proposal time.
- [ ] **Step 6: Verify** both ways, then commit.

### Task 5: UI — catalog and repositories

**Files:** the UI files of `phase-A-ui.txt` and `phase-B-ui.txt`, all taken from snapshot `88e49a1` (the last version written against a kit compatible with context-forge's).
- Create: `components/catalog/*`, `pages/Catalog.tsx`, `lib/roles.ts`, `lib/roles.test.ts`, `lib/useRoles.ts`
- Take from snapshot: `pages/{Repos,SshSources,DataSources,Environments}.tsx`
- Delete: `components/SshSourceDialog.tsx`
- Merge by hand: `App.tsx`, `lib/api.ts`, `pages/Settings.tsx`
- Not ported: the phase B changes to `index.css`, `pages/Knowledge.tsx`, `pages/Memory.tsx`, `pages/WebPages.tsx` unless they are functional; decide per file from the diff and list the decision in the report.

- [ ] **Step 1:** New files and pages from the snapshot, rename map applied. Neutral placeholders: `asterchat-prod`→`billing-prod`, `/etc/aster`→`/etc/myapp`, `astercare@…`→`deploy@…`.
- [ ] **Step 2: `App.tsx`.** Add the `/catalog` route and the Project/Organization nav groups. Keep the Approvals entry, the `adminOnly` filter and the pending badge.
- [ ] **Step 3: `lib/api.ts`.** Add catalog types and clients; remove the clients of the REST routes the server no longer has; keep `writeRequests`, `toolCalls`, `mcpKeys.update`, `rate_limit_per_minute`.
- [ ] **Step 4: `pages/Settings.tsx`.** Only remove `forge_config.repos`; keep the rate limit editor.
- [ ] **Step 5: Verify** `npx tsc --noEmit`, `npm run build`, `npx vitest run`; commit.

### Task 6: UI — database scopes

Downstream wrote this part on its own design kit. It is rewritten against the context-forge kit (`Table`, `Badge`, `Banner`, `Dialog`, `Select`, `Input`, `useToast`, `useConfirm`), using the downstream files at `797d305` as the functional reference.

**Files:**
- Create: `components/datasources/ScopeDialog.tsx`, `lib/dbScopes.ts`, `lib/dataSources.ts`, their tests
- Take as they are (no kit imports): `lib/dbScopes.ts`, `lib/dataSources.ts`, `lib/validation.ts`, `lib/messages.ts`, `components/catalog/catalogTabs.ts` and their `.test.ts`
- Modify: `pages/DataSources.tsx`, `pages/DataSourceDetail.tsx`, `components/catalog/DatabasesTab.tsx`, `components/catalog/DatabaseDialog.tsx`, `App.tsx` (route `/datasources/:scopeId`), `lib/api.ts`, `package.json`, `vite.config.ts`

- [ ] **Step 1:** Add `@testing-library/react`, `@testing-library/dom`, `jsdom` as devDependencies at the versions downstream uses, and `test.css: true` plus the jsdom environment for `.dom.test.tsx` files as downstream configures it. Run `npm install`.
- [ ] **Step 2:** Helpers and types.
- [ ] **Step 3:** `ScopeDialog`, scope-aware `DataSources` (one row per scope, "Inferred" warning, "Change scope"), `DataSourceDetail` on the scope without a schema selector, scope counts in `DatabasesTab`, wording in `DatabaseDialog`.
- [ ] **Step 4:** Port the functional DOM tests (`ScopeDialog`, `DataSources`, `DataSourceDetail`, `DatabasesTab`, `DatabaseDialog`, `MachineDialog`, `RepositoriesTab`), adapting assertions to the context-forge markup. Do not port kit-only tests.
- [ ] **Step 5: Verify** the three UI checks; commit.

### Task 7: README, roadmap slots, final verification

**Files:** `README.md`, `docs/superpowers/specs/2026-09-07-platform-improvements-roadmap.md`, `docs/superpowers/plans/2026-09-07-memory-personal-consolidation.md`, `docs/superpowers/plans/2026-09-07-retrieval-eval-reranker.md`

- [ ] **Step 1: README.** Features: organization catalog, per-project selection, database scopes. MCP tools: `catalog_list`, `resource_select`, `resource_deselect`; changed parameters of `ssh_source_add`, `db_add`, and the `db_*` tools (alias or scope id). Permissions: `resource_select` uses `context-write`. Development: tests with `TEST_DATABASE_URL`; `migrate` runs version modules only, the data conversions run at server boot.
- [ ] **Step 2: Upgrade notes.** The three conversions are one-way (they drop legacy columns and tables and move clone directories): back up the database and the repos cache before upgrading. Agents can no longer query `information_schema` or `pg_*`; use `db_schema` and `db_describe`. REST routes removed: writes on `/api/repos`, `/api/ssh-sources`, `/api/datasources`, `/api/{github,gitlab}/repos/add`.
- [ ] **Step 3: Migration slots.** In the roadmap and the two plans, move `0005_memory_stats` to `0008_memory_stats` and `0006_eval` to `0009_eval`.
- [ ] **Step 4: Grep gates.** `git grep -niE 'aster|larkin' -- ':!services/server/tests' ':!docs/superpowers' ':!services/ui/src/**/*.test.*'` returns nothing. `git grep -nE 'product_ops|ProductConnection|design-kit|ibm-plex|sync_repos_config|repo_registry'` returns nothing outside `docs/superpowers`.
- [ ] **Step 5: Full verification.** Server suite without and with `TEST_DATABASE_URL`; UI checks. Report exact totals.
- [ ] **Step 6: Commit.**
