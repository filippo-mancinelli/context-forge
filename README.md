# context-forge

Self-hosted context infrastructure for AI coding agents. Exposes a single MCP endpoint that gives Claude Code, Codex, Cursor, and other MCP clients three capabilities: persistent memory, semantic code search across repositories, and async job execution. Multi-tenant by design, managed from a web UI.

## Features

- **Persistent memory** — long-term memory with Mem0 + pgvector, scoped per organization.
- **Knowledge base** — upload documents (PDF, Word, Excel, PowerPoint, images with OCR, text, and more) via drag-and-drop; they're extracted, chunked, embedded, and made semantically searchable.
- **Hybrid repository search** — index and query local, GitHub, and GitLab repos using tree-sitter parsing. Retrieval fuses dense vector embeddings with lexical full-text ranking (Reciprocal Rank Fusion) so exact identifiers, error strings, and rare tokens surface alongside semantic matches. Set `SEARCH_HYBRID=false` to fall back to vector-only.
- **Async jobs** — offload slow downstream calls without hitting client timeouts.
- **Agent chat** — a built-in chat page where a tool-using agent searches your repos, memory, and knowledge base, showing every retrieval inline so you can verify context is surfaced correctly.
- **Multi-tenancy** — organizations as isolation boundaries with `owner / admin / member / viewer` roles and email invitations.
- **Runtime-first config** — manage repositories, providers, tokens, and indexing from the UI; `.env` and YAML are only for bootstrap.
- **Pluggable providers** — OpenAI, Jina, OpenAI-compatible, or fully local embeddings.

## Architecture

| Service | Stack | Ports |
|---|---|---|
| `context-forge` | Python 3.11, FastAPI (REST), FastMCP (MCP) | `8000/api`, `4000/mcp` |
| `postgres` | PostgreSQL 16 + pgvector | `5432` |
| `ui` | React 18, TypeScript, Vite, TailwindCSS | `3000` |

Indexing uses tree-sitter (Python, JS/TS, Go, Java), with scheduled re-indexing via APScheduler. Re-indexing is **incremental**: for git-backed repos, only files changed since the last indexed commit are re-parsed and re-embedded. Pushes can trigger it immediately via the `/api/webhooks/index` endpoint (set `WEBHOOK_SECRET`; supports GitHub, GitLab, and generic callers).

## Quick start

```bash
bash setup.sh            # or: .\setup.ps1 on Windows
docker compose up -d
```

Then open the UI at `http://localhost:3000` and complete setup with your `SETUP_BOOTSTRAP_TOKEN`.

Minimum environment variables:

```env
POSTGRES_PASSWORD=...
SETUP_BOOTSTRAP_TOKEN=...
OPENAI_API_KEY=...        # or another configured provider
```

See `.env.example` for the full list.

## MCP tools

- **Memory:** `memory_add`, `memory_search`, `memory_list`, `memory_delete`
- **Knowledge base:** `kb_search`, `kb_list`, `kb_get_document`
- **Repositories:** `repo_list`, `repo_search`, `repo_get_file`, `repo_index`, `repo_relationships`
- **Jobs:** `job_submit`, `job_status`, `job_result`

## Agent setup

Connection guides per client:

- [Claude Code](docs/claude-code.md)
- [Codex](docs/codex.md)
- [Cursor](docs/cursor.md)

## Security

The REST API/UI is authenticated after setup. The MCP endpoint has no built-in auth — expose it only on trusted networks or behind a secure proxy/VPN.

## License

MIT
