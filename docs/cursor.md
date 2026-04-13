# Connecting Cursor to context-forge

## Before connecting

1. Start the stack with `docker compose up -d`
2. Open `http://localhost:3000`
3. Complete setup with `SETUP_BOOTSTRAP_TOKEN`
4. Verify the MCP endpoint at `http://localhost:4000/mcp`
5. For remote servers: generate an MCP API key from the UI (Settings → API Keys)

## Workspace config (current project only)

Create or update `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "context-forge": {
      "url": "http://localhost:4000/mcp"
    }
  }
}
```

## Global config (all projects)

Create or update `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "context-forge": {
      "url": "http://localhost:4000/mcp"
    }
  }
}
```

## Remote server with API key authentication

For remote servers, add the `X-API-Key` header with your `forge_` API key:

```json
{
  "mcpServers": {
    "context-forge": {
      "url": "https://your-server.example.com/mcp",
      "headers": {
        "X-API-Key": "forge_YOUR_API_KEY"
      }
    }
  }
}
```

## Project instructions

You can copy the generic agent instructions into a Cursor rules file:

```bash
cp templates/AGENTS.md /path/to/your/project/.cursor/rules/context-forge.md
```

## Security reminder

- The UI/API is authenticated after setup
- For remote servers, always use an MCP API key (`X-API-Key` header)
- API keys can be managed from the UI under Settings → API Keys
- Alternatively, prefer private networking, a VPN, or a secure tunnel for MCP access
