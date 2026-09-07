# context-forge

Self-hosted context infrastructure for AI coding agents. Exposes a single MCP endpoint that gives Claude Code, Codex, Cursor, and other MCP clients persistent memory, semantic code search across repositories, live database schema context, remote files over SSH, reviewed write paths (SQL, files, pull requests), and async job execution. Multi-tenant by design (organizations and projects), managed from a web UI.

## Features

- **Persistent memory** — long-term memory with Mem0 + pgvector, scoped per organization and project.
- **Knowledge base** — upload documents (PDF, Word, Excel, PowerPoint, images with OCR, text, and more) via drag-and-drop, or fetch them from a URL (with SSRF guards); they're extracted, chunked, embedded, and made semantically searchable.
- **Hybrid repository search** — index and query local, GitHub, and GitLab repos (gitlab.com or self-hosted via `GITLAB_URL`) using tree-sitter parsing. Retrieval fuses dense vector embeddings with lexical full-text ranking (Reciprocal Rank Fusion) so exact identifiers, error strings, and rare tokens surface alongside semantic matches. Set `SEARCH_HYBRID=false` to fall back to vector-only.
- **Code intelligence** — a symbol graph (definitions, references, imports) built at index time powers `repo_map` (ranked file overview for a query, definitions first), `repo_neighbors` (callers, callees, and related files) and `repo_symbols`; `repo_get_file` reads by line window so agents pull only the lines they need.
- **Data sources** — connect external databases (PostgreSQL, MySQL, MariaDB, SQLite) directly or through an SSH tunnel. Agents get live schema context (tables, columns, keys, indexes, row estimates) enriched by a human-curated **data dictionary** (per-table and per-column descriptions edited in the UI), and can run validated read-only SQL (single SELECT/SHOW/EXPLAIN statement, enforced LIMIT, timeouts, full audit log). With the `db-write` permission, `db_execute` runs a single validated DML statement (real top-level WHERE required) inside a transaction with a DB-side timeout. Credentials are encrypted at rest when `ENCRYPTION_KEY` is set.
- **SSH file sources** — register a host and a root directory reachable over SSH; agents list, read (head/tail/offset windows), and grep files there, and with `ssh-write` can atomically write files confined to that root.
- **Code changes** — `repo_commit_files` and `repo_open_pr` clone a scratch workspace, commit agent-proposed changes on a branch, push, and open a GitHub PR / GitLab MR for human review.
- **API contracts** — ingest OpenAPI/Swagger specs (URL or pasted JSON/YAML) and GraphQL schemas (live introspection or pasted result). Every operation becomes individually listable and searchable, with `$ref`-resolved request/response schemas, so agents know exactly which endpoints exist and with what payloads.
- **CI/CD context** — live view of recent GitHub Actions / GitLab CI runs for configured repos, and a "why is the pipeline red" tool that returns the failed jobs/steps with the ANSI-stripped tail of their error logs. Uses the tokens already configured for indexing; nothing extra to set up.
- **Async jobs** — offload slow downstream calls without hitting client timeouts. Jobs are executed by the scheduler, so they survive a restart; transient failures are retried with exponential backoff and exhausted jobs land in a `dead` state.
- **Agent chat** — a built-in chat page where a tool-using agent searches your repos, memory, knowledge base, and connected databases, showing every retrieval inline so you can verify context is surfaced correctly. Streams responses (and model reasoning when available), switches models on the fly across the providers you have keys for (OpenAI, Anthropic, DeepSeek), keeps a per-user chat history, and can publish a conversation snapshot as a public share link.
- **Multi-tenancy** — organizations as isolation boundaries with `owner / admin / member / viewer` roles. Each organization holds one or more **projects** that scope every source, search, and memory namespace; users are created by org admins with per-project visibility. Provider keys, embedding model, git tokens, and the Telegram capture bot can all be overridden per organization.
- **Authentication** — local admin login, or any OIDC provider (Keycloak, Authentik, Entra ID, ...) with just-in-time user provisioning. The MCP endpoint accepts API keys with granular permissions or OIDC bearer tokens, and can act as an OAuth authorization server so MCP clients log in from the browser without an API key.
- **Runtime-first config** — manage repositories, providers, tokens, and indexing from the UI; `.env` and YAML are only for bootstrap.
- **Pluggable providers** — OpenAI, Jina, OpenAI-compatible, or fully local embeddings.

## Architecture

| Service | Stack | Ports |
|---|---|---|
| `context-forge` | Python 3.11, FastAPI (REST), FastMCP (MCP) | `8000/api`, `4000/mcp` |
| `postgres` | PostgreSQL 16 + pgvector | `5440` on the host → `5432` in the container |
| `ui` | React 18, TypeScript, Vite, TailwindCSS | `3000` |

Indexing uses tree-sitter (Python, JS/TS, Go, Java), with scheduled re-indexing via APScheduler. Re-indexing is **incremental**: for git-backed repos, only files changed since the last indexed commit are re-parsed and re-embedded, and the symbol graph is rebuilt for them. Pushes can trigger it immediately via the `/api/webhooks/index` endpoint (set `WEBHOOK_SECRET`; supports GitHub, GitLab, and generic callers).

Operations are observable from two endpoints. `GET /metrics` serves Prometheus text exposition on the API port with the queue depths (jobs by status, pending index requests and the age of the oldest one, pending knowledge-base documents and web pages), the scheduler heartbeat, the applied schema version, and MCP tool-call counters; set `METRICS_TOKEN` to require `Authorization: Bearer <token>`, or leave it empty on a private scraping network. `GET /api/health/details` returns the same picture as JSON for organization admins and owners, plus the database latency, and reports `degraded` when the database check fails, the queue or schema-version check fails, the scheduler has not ticked for more than 60 seconds (or never ticked), or the oldest index request is more than an hour old. `GET /api/health` stays public and minimal. No metric is labelled by organization.

Vector search is served by one HNSW index per organization and embedding dimension, built on the typed cast `embedding::vector(N)` and partial on `org_id`, so organizations on different embedding models stay independently indexed. The indexes are created and pruned in the background at startup, when embedding settings are saved without changing the dimension, and every six hours as a self-healing pass; a re-embed instead drops them before the first batch and rebuilds them inline at the end, because vectors of the new width do not fit the old index. Builds run on a dedicated connection with no command timeout and `HNSW_MAINTENANCE_WORK_MEM` (default `256MB`) of work memory, and an index that an interrupted build left INVALID is dropped and rebuilt by the next pass.

## Quick start

```bash
bash setup.sh            # or: .\setup.ps1 on Windows
docker compose up -d
```

The setup wizard creates `.env` from `.env.example` and generates random `POSTGRES_PASSWORD` and `SETUP_BOOTSTRAP_TOKEN` values; you only need to add your `OPENAI_API_KEY` (or another embeddings provider).

Then open the UI at `http://localhost:3000`. On first run the onboarding wizard asks for the **bootstrap token** — the `SETUP_BOOTSTRAP_TOKEN` value in your `.env` (it proves that whoever creates the admin account has access to the server environment) — and for the credentials of the web UI admin account.

Minimum environment variables:

```env
POSTGRES_PASSWORD=...       # generated by the wizard
SETUP_BOOTSTRAP_TOKEN=...   # generated by the wizard
OPENAI_API_KEY=...          # or another configured provider
```

See `.env.example` for the full list (MCP auth, OIDC, webhooks, credential encryption); every variable is passed through to the container by `docker-compose.yml`.

### Upgrading an existing installation

The schema migrates itself at startup. Coming from a version without projects and OIDC:

- every organization gets a `Default` project, and all existing sources, chunks, and memories are attached to it;
- the former local OAuth server for MCP is gone: tokens it issued stop working, and MCP clients should use an API key or the OIDC bridge instead;
- API keys keep working; their legacy scope is mapped to the new permissions (`read` → `context-read`, `write` → `context-read` + `context-write`, `admin` → `*`);
- global LLM/provider overrides move into the per-organization settings;
- embedding columns lose their fixed dimension so each organization can pick its own model. The old ivfflat indexes are dropped in the process and replaced by per-organization HNSW indexes, which the server builds in the background on the first startup after the upgrade; until each build completes vector search runs unindexed on that table (a sequential scan), so it is correct but slow, and the build itself runs on a dedicated connection with no timeout;
- on the first start after this upgrade the server records the existing schema as migration version 1 (the frozen baseline is replayed once; it is idempotent), and later changes apply as numbered migrations; `python -m src.cli migrate --status` shows the current version.

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

## Projects and the MCP endpoint

An organization can hold several projects. Connect to the organization-level endpoint `/mcp/<org-slug>` and pick a project in-session with `list_projects` / `use_project` (memory, search, and every scoped tool follow that choice), or point a client straight at `/mcp/<org-slug>/<project-slug>`. API keys can be restricted to a set of projects, and org admins choose which projects each user can see.

## MCP tools

- **Memory:** `memory_add`, `memory_search`, `memory_list`, `memory_delete`
- **Knowledge base:** `kb_search`, `kb_list`, `kb_get_document`, `kb_add_url`
- **Repositories:** `repo_list`, `repo_search`, `repo_symbols`, `repo_get_file`, `repo_index`, `repo_relationships`, `repo_add`
- **Code intelligence:** `repo_references`, `code_explain`, `repo_annotate`, `repo_annotations`, `repo_map`, `repo_neighbors`
- **Code changes:** `repo_commit_files`, `repo_open_pr` — create a branch, push agent-proposed file changes, and open a GitHub PR / GitLab MR for human review (requires the `repo-write` permission; git tokens need write scope)
- **Data sources:** `db_list`, `db_schema`, `db_describe`, `db_query`, `db_execute` (`db-write`), `db_add` (`sources-write`)
- **SSH files:** `ssh_sources`, `ssh_list_files`, `ssh_read_file`, `ssh_grep`, `ssh_write_file` (`ssh-write`), `ssh_source_add` (`sources-write`)
- **Web pages:** `web_search`, `web_list`, `web_get_page`, `web_add`, `web_refetch`, `web_list_sites`, `web_crawl`, `web_delete`, `web_delete_site`
- **API contracts:** `api_list`, `api_endpoints`, `api_get_endpoint`, `api_add`
- **CI/CD:** `ci_runs`, `ci_failure`
- **Jobs:** `job_submit`, `job_status`, `job_result`
- **Projects:** `list_projects`, `use_project`, `current_project`; with `projects-write`: `create_project`, `rename_project`, `delete_project`, `add_project_member`

Sources registered through MCP (`db_add`, `ssh_source_add`) are created without their secret and stay in a `pending_secret` state until an admin completes them from the UI.

## Agent setup

Connection guides per client:

- [Claude Code](docs/claude-code.md)
- [Codex](docs/codex.md)
- [Cursor](docs/cursor.md)

### Claude Code with OIDC (browser login)

With `OIDC_ENABLED=true` and `MCP_AUTH_MODE=enabled`, the MCP endpoint acts as an OAuth bridge to your identity provider: add the server **without an API key** and authenticate through the provider's login page in the browser. The endpoint is organization-level (`/mcp/<org>`); pick the project afterwards with the `use_project` tool.

```bash
claude mcp add --transport http --scope user context-forge https://<host>/mcp/<org>
```

No `--header`: on first use Claude Code detects that authentication is required, opens the browser on the provider's login page, and receives the token.

> MCP servers are loaded when a session starts: after `claude mcp add` (and after the login) the server and its tools **show up in a new** Claude Code session. In a session that is already open, use `/mcp` → *reconnect* to trigger the login right away.

Alternatively, with an API key (no browser, suited to headless agents):

```bash
claude mcp add --transport http --scope user context-forge https://<host>/mcp/<org> \
  --header "X-API-Key: forge_..."
```

Clients that can only set an `Authorization` header may send the key as `Bearer forge_...`.

## Security

The REST API/UI is authenticated after setup (local admin login, or OIDC when `OIDC_ENABLED=true`). The MCP endpoint has three modes, selected with `MCP_AUTH_MODE`:

- `disabled` — no authentication (local development only);
- `transition` — API keys and OIDC tokens are honored when present; anonymous callers only get read-only tools;
- `enabled` — every request must carry an API key or an OIDC bearer token.

Expose the MCP port only behind TLS with `MCP_AUTH_MODE=enabled`. Data-source and SSH credentials are encrypted at rest when `ENCRYPTION_KEY` is set; git and OIDC tokens are never echoed in error messages.

### MCP permissions

The identity provider only authenticates. Authorization of MCP tools is managed in the application database: each organization role (viewer, member, admin, owner) has a set of permissions, editable from the dashboard under **Organization → MCP permissions**.

| Permission | Grants |
|---|---|
| `context-read` | search and read memory, repositories, knowledge base, web pages, API contracts, CI runs, database schemas |
| `context-write` | add or delete memories, annotate code, add and refetch web pages |
| `db-query` | read-only SQL on connected databases |
| `db-write` | `db_execute` (validated single-statement DML) |
| `repo-write` | `repo_commit_files`, `repo_open_pr` |
| `jobs` | submit and read async jobs |
| `ssh-read` / `ssh-write` | read / write files on SSH sources |
| `projects-write` | create, rename, delete projects and manage their members |
| `sources-write` | register repositories, API contracts, knowledge-base URLs, databases, and SSH sources |
| `*` | everything, including re-indexing and web crawling/deletion |

Defaults: viewer → `context-read`; member → `context-read`, `context-write`, `db-query`, `ssh-read`; admin → the member set plus `jobs`, `projects-write`, `sources-write`; owner → `*`. Write capabilities (`db-write`, `ssh-write`, `repo-write`) are owner-only until granted explicitly.

API keys get granular permissions chosen at creation (**Settings → MCP keys**), never more than the creator's own role allows. OIDC groups under `OIDC_GROUP_PREFIX` are only used to suggest the initial role when a user first logs in.

## License

MIT
