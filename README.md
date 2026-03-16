# context-forge

**Agent context infrastructure — plug and play with Claude Code, Codex, and Cursor.**

context-forge is a self-hosted platform that gives your AI agents:

- **Persistent memory** across sessions (Mem0 + pgvector)
- **Semantic search** across multiple repositories (local + GitHub + GitLab)
- **Async job execution** for slow HTTP calls that would otherwise time out
- **One MCP endpoint** that works with any MCP-compatible agent

```
docker compose up -d
claude mcp add --transport http context-forge http://localhost:4000/mcp
```

---

## Architecture

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

**Services:** 3 Docker containers — `postgres`, `context-forge`, `ui`

---

## Quick Start

### 1. Clone and configure

```bash
git clone https://github.com/yourorg/context-forge
cd context-forge

# Linux / macOS
bash setup.sh

# Windows PowerShell
.\setup.ps1
```

The setup wizard:
- Creates `.env` with a random Postgres password
- Creates `context-forge.yml` for your repos
- Creates `docker-compose.override.yml` for volume mounts
- Starts all services

### 2. Manual setup (if you prefer)

```bash
cp .env.example .env
# Edit .env: set OPENAI_API_KEY (and optionally GITHUB_TOKEN, GITLAB_TOKEN)

cp context-forge.yml.example context-forge.yml
# Edit context-forge.yml: add your repositories

cp docker-compose.override.yml.example docker-compose.override.yml
# Edit docker-compose.override.yml: mount your local repo paths

docker compose up -d
```

### 3. Connect your agent

**Claude Code:**
```bash
claude mcp add --transport http context-forge http://localhost:4000/mcp
```

**Cursor** — add to `.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "context-forge": {
      "url": "http://localhost:4000/mcp"
    }
  }
}
```

**Codex** — add to `~/.codex/config.toml`:
```toml
[[mcp_servers]]
name = "context-forge"
transport = "http"
url = "http://localhost:4000/mcp"
```

### 4. Add system prompt to your project

```bash
# For Claude Code
cp templates/CLAUDE.md /path/to/your/project/CLAUDE.md

# For Codex / any agent
cp templates/AGENTS.md /path/to/your/project/AGENTS.md
```

---

## Configuration

### Repositories (`context-forge.yml`)

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

indexing:
  auto: true
  schedule: "0 */6 * * *"   # re-index every 6 hours
```

### Volume mounts (`docker-compose.override.yml`)

For local repos only — remote repos are cloned automatically:

```yaml
services:
  context-forge:
    volumes:
      - /home/user/projects/my-backend:/repos/my-backend:ro
      # Windows:
      # - C:/Users/user/projects/my-backend:/repos/my-backend:ro
```

### Environment variables (`.env`)

| Variable | Description | Default |
|----------|-------------|---------|
| `POSTGRES_PASSWORD` | DB password | (required) |
| `OPENAI_API_KEY` | OpenAI API key for embeddings + Mem0 | (required for openai mode) |
| `EMBEDDINGS_PROVIDER` | `openai` or `local` | `openai` |
| `EMBEDDINGS_MODEL` | Embedding model name | `text-embedding-3-small` |
| `EMBEDDINGS_DIMS` | Embedding dimensions | `1536` |
| `LLM_PROVIDER` | LLM for Mem0 extraction | `openai` |
| `LLM_MODEL` | LLM model | `gpt-4o-mini` |
| `GITHUB_TOKEN` | GitHub personal access token | (optional) |
| `GITLAB_TOKEN` | GitLab token | (optional) |

#### Local embeddings (no API key needed)

```env
EMBEDDINGS_PROVIDER=local
EMBEDDINGS_MODEL=all-MiniLM-L6-v2
EMBEDDINGS_DIMS=384
```

> Note: local embeddings download the model on first use (~100MB). Also configure Mem0 with a local LLM (Ollama) to be fully offline.

---

## MCP Tools Reference

### Memory

| Tool | Description |
|------|-------------|
| `memory_add(content, metadata?)` | Save a fact, decision, or note persistently |
| `memory_search(query, limit?)` | Semantic search across stored memories |
| `memory_list(limit?)` | List recent memories |
| `memory_delete(memory_id)` | Delete a memory |

### Repositories

| Tool | Description |
|------|-------------|
| `repo_search(query, repos?, limit?)` | Semantic search across indexed code |
| `repo_get_file(repo, path)` | Read a file by repo name and path |
| `repo_list()` | List repos with indexing status |
| `repo_index(repo?)` | Trigger re-indexing |
| `repo_relationships(repo?)` | Discover related repos via embedding similarity |

### Async Jobs

| Tool | Description |
|------|-------------|
| `job_submit(url, method?, payload?, headers?)` | Submit a slow HTTP call as a background job |
| `job_status(job_id)` | Poll: pending / running / done / error |
| `job_result(job_id)` | Get the result when done |

**Example — calling a slow AI agent without timeout:**
```python
# In your agent:
job = job_submit(
    url="http://my-sql-agent:8000/api/v1/query",
    payload={"question": "How many users signed up today?"}
)
# job["job_id"] = "abc-123..."

# Poll until done:
status = job_status("abc-123...")
# {"job_status": "running", ...}

# Get result:
result = job_result("abc-123...")
# {"status": "ok", "result": {"answer": "42 users", ...}}
```

---

## Web UI

Open `http://localhost:3000` to access the dashboard:

- **Repositories** — view indexing status, trigger re-indexing, sync config
- **Memory** — browse, search, and delete stored memories
- **MCP Tools** — list all available tools with descriptions and copy-to-clipboard
- **Async Jobs** — monitor background job execution in real time

---

## Useful Commands

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

## Deployment on VPS (Dokploy / Coolify / Portainer)

context-forge is a standard Docker Compose stack — deploy it anywhere:

1. Copy the project to your server (git clone or scp)
2. Create `.env` and `context-forge.yml` on the server
3. `docker compose up -d`
4. Open port 4000 (MCP) and 3000 (UI) on your firewall, or use a reverse proxy

For Dokploy: create a new "Compose" application pointing to this repository.

> Security: For production, put context-forge behind a reverse proxy (Nginx/Traefik) with HTTPS. The MCP endpoint currently has no authentication — only expose it on your internal network or via VPN.

---

## Extending with Custom MCP Servers

context-forge's MCP endpoint is additive — you can also configure additional MCP servers in parallel (GitHub MCP, Jira MCP, etc.) in your agent's config alongside context-forge.

See the [Model Context Protocol registry](https://github.com/modelcontextprotocol/servers) for available community servers.

---

## License

MIT
