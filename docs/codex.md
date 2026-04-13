# Connecting Codex to context-forge

## Before connecting

1. Start the stack with `docker compose up -d`
2. Open the UI on `http://localhost:3000`
3. Complete setup with `SETUP_BOOTSTRAP_TOKEN`
4. Confirm the MCP endpoint is reachable at `http://localhost:4000/mcp`
5. For remote servers: generate an MCP API key from the UI (Settings → API Keys)

## CLI

```bash
codex mcp add context-forge --url http://localhost:4000/mcp
```

## Config file

Add to `~/.codex/config.toml` or project-level Codex config:

```toml
[[mcp_servers]]
name = "context-forge"
transport = "http"
url = "http://localhost:4000/mcp"
```

## Remote server with API key authentication

For remote servers, include the `X-API-Key` header with your `forge_` API key:

```bash
codex mcp add context-forge --url https://your-server.example.com/mcp \
  --header "X-API-Key: forge_YOUR_API_KEY"
```

Or in `~/.codex/config.toml`:

```toml
[[mcp_servers]]
name = "context-forge"
transport = "http"
url = "https://your-server.example.com/mcp"

[mcp_servers.headers]
X-API-Key = "forge_YOUR_API_KEY"
```

## Project instructions

Codex reads `AGENTS.md` from the project root.

```bash
cp templates/AGENTS.md /path/to/your/project/AGENTS.md
```

## Security reminder

- The UI/API is protected by admin auth after setup
- For remote servers, always use an MCP API key (`X-API-Key` header)
- API keys can be managed from the UI under Settings → API Keys
- Alternatively, prefer a VPN, SSH tunnel, or private network for MCP access
