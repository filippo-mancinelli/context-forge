# context-forge — Agent Context Infrastructure

**context-forge** is a self-hosted platform that provides AI agents with persistent memory, semantic code search, and async job execution through a single MCP (Model Context Protocol) endpoint.

---

## Project Overview

context-forge is a Docker-based microservices platform consisting of three main services:

| Service | Technology | Port | Purpose |
|---------|------------|------|---------|
| `postgres` | PostgreSQL 16 + pgvector | 5432 | Vector database for embeddings and metadata |
| `context-forge` | Python 3.11 (FastMCP + FastAPI) | 4000 (MCP), 8000 (API) | Core server with MCP tools and REST API |
| `ui` | React 18 + Vite + Tailwind CSS | 3000 | Web dashboard for managing repos, memory, jobs |

### Architecture Diagram

```
Claude Code / Codex / Cursor
        │
        │ MCP HTTP
        ▼
context-forge (:4000/mcp + :8000/api)
    ├── memory_*     — Mem0 persistent memory (pgvector)
    ├── repo_*       — Semantic code search (tree-sitter + pgvector)
    └── job_*        — Async HTTP jobs (no more MCP timeouts)
        │
        ▼
   PostgreSQL + pgvector

Web UI (:3000)
    ├── Repos — indexing status, trigger re-index
    ├── Memory — browse and search memories
    ├── Tools — MCP tool reference
    └── Jobs — async job monitor
```

---

## Technology Stack

### Backend (services/server/)
- **Python 3.11+** with type hints throughout
- **FastMCP** — MCP server for AI agent tools
- **FastAPI** — REST API for the Web UI
- **Uvicorn** — ASGI server (runs both MCP and API concurrently)
- **Database**: asyncpg + SQLAlchemy + pgvector
- **Memory**: Mem0 with pgvector backend
- **Embeddings**: OpenAI (default), Jina, or local (sentence-transformers)
- **Code Parsing**: tree-sitter (Python, JS/TS, Go, Java)
- **Git**: GitPython for repo cloning/management
- **Scheduling**: APScheduler for periodic indexing
- **Configuration**: Pydantic Settings + YAML

### Frontend (services/ui/)
- **React 18** with TypeScript
- **Vite** — build tool
- **React Router** — navigation
- **Tailwind CSS** — styling
- **Lucide React** — icons

### Infrastructure
- **Docker Compose** — local development and deployment
- **Nginx** — static file serving for UI

---

## Project Structure

```
context-forge/
├── docker-compose.yml           # Main service definitions
├── docker-compose.override.yml  # Local volume mounts (not in git)
├── context-forge.yml            # Repo configuration (not in git)
├── .env                         # Environment variables (not in git)
├── setup.sh / setup.ps1         # Setup wizards
├── README.md                    # User documentation
├── docs/                        # Agent connection guides
│   ├── claude-code.md
│   ├── codex.md
│   └── cursor.md
├── templates/                   # Project instruction templates
│   ├── AGENTS.md
│   └── CLAUDE.md
└── services/
    ├── server/                  # Python backend
    │   ├── Dockerfile
    │   ├── pyproject.toml       # Dependencies
    │   └── src/
    │       ├── main.py          # Entry point (dual server)
    │       ├── config.py        # Settings + context-forge.yml loader
    │       ├── db.py            # PostgreSQL pool + DDL
    │       ├── scheduler.py     # APScheduler setup
    │       ├── api/
    │       │   ├── app.py       # FastAPI app
    │       │   └── routes/      # REST endpoints
    │       ├── mcp/
    │       │   ├── server.py    # FastMCP instance
    │       │   ├── memory.py    # memory_* tools (Mem0)
    │       │   ├── repos.py     # repo_* tools (search/index)
    │       │   └── jobs.py      # job_* tools (async HTTP)
    │       └── indexer/
    │           ├── indexer.py   # Tree-sitter parsing + embedding
    │           ├── embedder.py  # OpenAI/local embeddings
    │           └── git_manager.py # Git clone/pull logic
    └── ui/                      # React frontend
        ├── Dockerfile
        ├── package.json
        ├── vite.config.ts
        ├── tailwind.config.js
        └── src/
            ├── App.tsx          # Layout + routing
            ├── lib/api.ts       # API client
            └── pages/           # Repos, Memory, Tools, Jobs
```

---

## Build and Run Commands

### Prerequisites
- Docker and Docker Compose v2
- For local setup: bash (Linux/macOS) or PowerShell (Windows)

### Initial Setup

```bash
# Linux/macOS
bash setup.sh

# Windows PowerShell
.\setup.ps1
```

The setup wizard:
1. Creates `.env` with a random Postgres password
2. Creates `context-forge.yml` for repo configuration
3. Creates `docker-compose.override.yml` for volume mounts
4. Builds and starts all services

### Manual Setup (if preferred)

```bash
# 1. Copy and edit configuration files
cp .env.example .env
cp context-forge.yml.example context-forge.yml
cp docker-compose.override.yml.example docker-compose.override.yml

# 2. Edit .env — set OPENAI_API_KEY (and optionally GITHUB_TOKEN, GITLAB_TOKEN)
# 3. Edit context-forge.yml — add your repositories
# 4. Edit docker-compose.override.yml — mount local repo paths

# 5. Start services
docker compose up -d
```

### Useful Commands

```bash
# View logs
docker compose logs -f context-forge

# Restart after config changes
docker compose restart context-forge

# Stop everything
docker compose down

# Reset database (warning: deletes all data)
docker compose down -v && docker compose up -d

# Check service health
curl http://localhost:8000/api/health
```

---

## Configuration

### Environment Variables (.env)

| Variable | Description | Default |
|----------|-------------|---------|
| `POSTGRES_PASSWORD` | DB password | (generated) |
| `OPENAI_API_KEY` | OpenAI API key for embeddings + Mem0 | (required for openai mode) |
| `EMBEDDINGS_PROVIDER` | `openai` \| `jina` \| `openai-compatible` \| `local` | `openai` |
| `EMBEDDINGS_MODEL` | Embedding model name | `text-embedding-3-small` |
| `EMBEDDINGS_DIMS` | Embedding dimensions | `1536` |
| `LLM_PROVIDER` | LLM for Mem0: `openai` \| `anthropic` \| `deepseek` | `openai` |
| `LLM_MODEL` | LLM model name | `gpt-4o-mini` |
| `DEEPSEEK_API_KEY` | DeepSeek API key (if `LLM_PROVIDER=deepseek`) | (optional) |
| `GITHUB_TOKEN` | GitHub personal access token | (optional) |
| `GITLAB_TOKEN` | GitLab token | (optional) |

### Repository Configuration (context-forge.yml)

```yaml
repos:
  # Local repo (requires volume mount in docker-compose.override.yml)
  - name: my-backend
    type: local
    path: /repos/my-backend
    language: python

  # GitHub repo (auto-cloned, requires GITHUB_TOKEN for private)
  - name: my-frontend
    type: github
    url: https://github.com/myorg/my-frontend
    branch: main

  # GitLab self-hosted (any URL supported)
  - name: platform
    type: gitlab
    url: https://gitlab.mycompany.com/team/platform
    branch: develop

# Memory settings
memory:
  user_id: default

# Indexing settings
indexing:
  auto: true
  schedule: "0 */6 * * *"   # re-index every 6 hours
  exclude:
    - "**/.git/**"
    - "**/node_modules/**"
    - "**/__pycache__/**"
    - "**/*.pyc"
    - "**/dist/**"
    - "**/build/**"
    - "**/.next/**"
    - "**/coverage/**"
    - "**/*.min.js"
    - "**/*.lock"
    - "**/package-lock.json"
    - "**/target/**"
    - "**/*.class"
    - "**/*.jar"
    - "**/*.war"
    - "**/.gradle/**"
    - "**/*.iml"
    - "**/.idea/**"
    - "**/.settings/**"
    - "**/.project"
    - "**/.classpath"
    - "**/.angular/**"
    - "**/*.map"
  max_file_size_kb: 500
  chunk_size: 400
  chunk_overlap: 50
```

### Volume Mounts (docker-compose.override.yml)

For local repos only — remote repos are cloned automatically:

```yaml
services:
  context-forge:
    volumes:
      - /home/user/projects/my-backend:/repos/my-backend:ro
      # Windows:
      # - C:/Users/user/projects/my-backend:/repos/my-backend:ro
```

---

## Code Style Guidelines

### Python (Backend)

- **Type hints**: All functions use `from __future__ import annotations` and type hints
- **Docstrings**: Google-style docstrings for all public functions
- **Imports**: Grouped as: stdlib → third-party → local (with `from __future__` first)
- **Async**: Use `async`/`await` for I/O operations; thread pools for CPU-bound work (indexing)
- **Error handling**: Use try/except with logging; return `{"status": "error", ...}` dicts for MCP tools
- **Naming**: snake_case for functions/variables, PascalCase for classes, UPPER_CASE for constants

Example:
```python
from __future__ import annotations

import logging
from typing import Optional

from .server import mcp

logger = logging.getLogger(__name__)

@mcp.tool()
async def memory_add(content: str, metadata: Optional[dict] = None) -> dict:
    """Save a memory that persists across sessions.

    Args:
        content: The text to remember
        metadata: Optional key-value metadata tags

    Returns:
        dict with the created memory id and content
    """
    try:
        result = _get_memory().add(content, metadata=metadata or {})
        return {"status": "ok", "memory": result}
    except Exception as e:
        logger.error("memory_add failed: %s", e)
        return {"status": "error", "error": str(e)}
```

### TypeScript/React (Frontend)

- **Types**: Explicit interfaces for API responses
- **Components**: Functional components with hooks
- **Styling**: Tailwind utility classes
- **Naming**: camelCase for variables/functions, PascalCase for components/interfaces

---

## MCP Tools Reference

### Memory Tools (Mem0 + pgvector)

| Tool | Description |
|------|-------------|
| `memory_add(content, metadata?, user_id?)` | Save a fact, decision, or note persistently |
| `memory_search(query, limit?)` | Semantic search across stored memories |
| `memory_list(limit?)` | List recent memories |
| `memory_delete(memory_id)` | Delete a memory |

### Repository Tools (tree-sitter + pgvector)

| Tool | Description |
|------|-------------|
| `repo_search(query, repos?, limit?)` | Semantic search across indexed code |
| `repo_get_file(repo, path)` | Read a file by repo name and path |
| `repo_list()` | List repos with indexing status |
| `repo_index(repo?)` | Trigger re-indexing |
| `repo_relationships(repo?)` | Discover related repos via embedding similarity |

### Async Job Tools

| Tool | Description |
|------|-------------|
| `job_submit(url, method?, payload?, headers?)` | Submit a slow HTTP call as a background job |
| `job_status(job_id)` | Poll: pending / running / done / error |
| `job_result(job_id)` | Get the result when done |

---

## Testing Strategy

The project currently relies on:
- Type checking: Python type hints throughout; TypeScript for UI
- Health check endpoint: `GET /api/health`
- Manual verification via Web UI and MCP tool calls

No automated test suite is present. When adding features:
1. Verify the server starts without errors: `docker compose up -d`
2. Check health: `curl http://localhost:8000/api/health`
3. Test MCP tools via an agent or HTTP POST to `/mcp`
4. Verify UI loads and displays data correctly

---

## Security Considerations

1. **MCP endpoint has no authentication** — only expose on internal networks or via VPN
2. **For production**: Place behind a reverse proxy (Nginx/Traefik) with HTTPS
3. **API keys**: Store in `.env`, never commit to git
4. **Local repos**: Mounted read-only (`:ro`) in container
5. **Git tokens**: Use read-only tokens with minimal scope (`repo` for GitHub, `read_repository` for GitLab)

---

## Deployment on VPS (Dokploy / Coolify / Portainer)

context-forge is a standard Docker Compose stack:

1. Copy the project to your server (git clone or scp)
2. Create `.env` and `context-forge.yml` on the server
3. `docker compose up -d`
4. Open port 4000 (MCP) and 3000 (UI) on your firewall, or use a reverse proxy

For Dokploy: create a new "Compose" application pointing to this repository.

---

## Development Workflow

### Adding a New MCP Tool

1. Add tool function in `services/server/src/mcp/` (memory.py, repos.py, or jobs.py)
2. Use `@mcp.tool()` decorator
3. Follow existing patterns: async function, type hints, docstring, error handling
4. Restart server: `docker compose restart context-forge`
5. Verify via Web UI Tools page or agent

### Adding API Endpoints

1. Add route in `services/server/src/api/routes/`
2. Include router in `services/server/src/api/app.py`
3. Restart server

### Modifying the UI

1. Edit files in `services/ui/src/`
2. Run locally: `cd services/ui && npm run dev`
3. Or rebuild container: `docker compose up -d --build ui`

### Database Schema Changes

DDL is in `services/server/src/db.py`. The schema is applied on startup via `init_db()`. To add tables/columns:
1. Add DDL to the `DDL` string in `db.py`
2. Restart server — changes apply automatically

---

## Key Dependencies

### Production
- fastmcp>=2.0.0
- fastapi>=0.115.0
- uvicorn[standard]>=0.30.0
- asyncpg>=0.29.0
- sqlalchemy[asyncio]>=2.0.0
- pgvector>=0.3.0
- mem0ai>=0.1.0
- openai>=1.50.0
- tree-sitter>=0.23.0 + language bindings
- gitpython>=3.1.40
- apscheduler>=3.10.4

### Development
- Python 3.11+
- Node.js 20+ (for UI)
- Docker + Docker Compose

---

## License

MIT License — See README.md for details.
