# context-forge

Runtime-first agent context infrastructure for Claude Code, Codex, Cursor, and other MCP clients.

context-forge provides:

- Persistent memory with Mem0 + pgvector
- Semantic repository search across local, GitHub, and GitLab repos
- Async HTTP jobs for slow downstream services
- One MCP endpoint plus a web UI for runtime administration

## Runtime-first model

After bootstrap, the source of truth is the runtime configuration stored in Postgres.

- Use the web UI to manage repositories, providers, tokens, indexing, and memory defaults
- Keep `.env` for bootstrap and infrastructure secrets
- Keep `context-forge.yml` only for bootstrap defaults, disaster recovery, or legacy import

On startup, if runtime config is missing but bootstrap config contains meaningful values, context-forge imports that configuration into the database automatically.

## Services

- `postgres`: PostgreSQL 16 + pgvector
- `context-forge`: MCP on `:4000/mcp`, REST API on `:8000/api`
- `ui`: React app on `:3000`

The primary landing page in the UI is now `Repositories`.

## Quick start

### 1. Bootstrap files

Run the setup script or create the files manually.

Linux or macOS:

```bash
bash setup.sh
```

Windows PowerShell:

```powershell
.\setup.ps1
```

Manual bootstrap:

```bash
cp .env.example .env
cp context-forge.yml.example context-forge.yml
cp docker-compose.override.yml.example docker-compose.override.yml
```

What the bootstrap files are for:

- `.env`: Docker/runtime secrets and first-boot defaults
- `context-forge.yml`: optional baseline repo/indexing config for import
- `docker-compose.override.yml`: local repo mounts only

### 2. Required environment variables

At minimum set:

```env
POSTGRES_PASSWORD=...
SETUP_BOOTSTRAP_TOKEN=...
```

Optional provider defaults:

```env
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
DEEPSEEK_API_KEY=...
GITHUB_TOKEN=...
GITLAB_TOKEN=...

LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini

EMBEDDINGS_PROVIDER=openai
EMBEDDINGS_MODEL=text-embedding-3-small
EMBEDDINGS_DIMS=1536
EMBEDDINGS_API_KEY=
EMBEDDINGS_BASE_URL=
```

Supported embedding providers in the app:

- `openai`
- `jina`
- `openai-compatible`
- `local`

### 3. Start the stack

```bash
docker compose up -d
```

### 4. Complete setup in the UI

Open:

- UI: [http://localhost:3000](http://localhost:3000)
- API health: [http://localhost:8000/api/health](http://localhost:8000/api/health)

Setup behavior:

- Fresh install: enter bootstrap token, admin account, and optional initial runtime config
- Legacy install: if bootstrap files were auto-imported, create only the admin account and continue

After login:

- `Repositories` is the operational home page
- `Settings` is the runtime control plane

## Local repositories

For local repositories, mount them into the server container through `docker-compose.override.yml`.

Example:

```yaml
services:
  context-forge:
    volumes:
      - /home/user/projects/my-backend:/repos/my-backend:ro
      # Windows:
      # - C:/Users/user/projects/my-backend:/repos/my-backend:ro
```

Then add the mounted path from the UI or place it in `context-forge.yml` for first import.

## Hosted embedding examples

OpenAI:

```env
EMBEDDINGS_PROVIDER=openai
EMBEDDINGS_MODEL=text-embedding-3-small
EMBEDDINGS_DIMS=1536
OPENAI_API_KEY=...
```

OpenAI-compatible provider:

```env
EMBEDDINGS_PROVIDER=openai-compatible
EMBEDDINGS_MODEL=<provider-model>
EMBEDDINGS_DIMS=<provider-dims>
EMBEDDINGS_API_KEY=...
EMBEDDINGS_BASE_URL=https://provider.example/v1
```

Jina:

```env
EMBEDDINGS_PROVIDER=jina
EMBEDDINGS_MODEL=jina-embeddings-v3
EMBEDDINGS_DIMS=1024
EMBEDDINGS_API_KEY=...
```

Important:

- Changing embeddings provider or model requires repository re-indexing
- Changing `EMBEDDINGS_DIMS` requires resetting vector-backed data and then re-indexing

## MCP tools

Memory:

- `memory_add`
- `memory_search`
- `memory_list`
- `memory_delete`

Repositories:

- `repo_list`
- `repo_search`
- `repo_get_file`
- `repo_index`
- `repo_relationships`

Jobs:

- `job_submit`
- `job_status`
- `job_result`

## Web UI

Main pages:

- `Repositories`: home page for repo browsing, import, indexing, and drill-down
- `Settings`: runtime config for providers, tokens, indexing, and manual repo entries
- `Memory`
- `MCP Tools`
- `Async Jobs`

## Agent connection guides

- [docs/claude-code.md](docs/claude-code.md)
- [docs/codex.md](docs/codex.md)
- [docs/cursor.md](docs/cursor.md)

## Useful commands

```bash
docker compose logs -f context-forge
docker compose restart context-forge
docker compose down
docker compose down -v && docker compose up -d
curl http://localhost:8000/api/health
```

## Deployment notes

For remote deployments:

1. Copy the repository to the server
2. Create `.env`
3. Optionally create `context-forge.yml` and `docker-compose.override.yml`
4. Run `docker compose up -d`
5. Open the UI and complete setup with `SETUP_BOOTSTRAP_TOKEN`

Security notes:

- The REST UI/API has admin auth after setup
- The MCP endpoint currently has no built-in authentication
- Expose MCP only on trusted networks or behind a secure proxy/VPN

## License

MIT
