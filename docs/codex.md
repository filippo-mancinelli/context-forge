# Connecting Codex to context-forge

## Before connecting

1. Start the stack with `docker compose up -d`
2. Open the UI on `http://localhost:3000`
3. Complete setup with `SETUP_BOOTSTRAP_TOKEN`
4. Confirm the MCP endpoint is reachable at `http://localhost:4000/mcp`

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

## Project instructions

Codex reads `AGENTS.md` from the project root.

```bash
cp templates/AGENTS.md /path/to/your/project/AGENTS.md
```

## Remote server

Replace `localhost` with your server hostname or reverse proxy URL:

```bash
codex mcp add context-forge --url http://your-server.example.com:4000/mcp
```

Security reminder:

- The UI/API is protected by admin auth after setup
- The MCP endpoint itself does not yet have built-in auth
- Prefer a VPN, SSH tunnel, or private network exposure for MCP
