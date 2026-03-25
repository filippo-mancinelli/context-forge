# Connecting Cursor to context-forge

## Before connecting

1. Start the stack with `docker compose up -d`
2. Open `http://localhost:3000`
3. Complete setup with `SETUP_BOOTSTRAP_TOKEN`
4. Verify the MCP endpoint at `http://localhost:4000/mcp`

## Workspace config

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

## Global config

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

## Project instructions

You can copy the generic agent instructions into a Cursor rules file:

```bash
cp templates/AGENTS.md /path/to/your/project/.cursor/rules/context-forge.md
```

## Remote server

Replace `localhost` with your server hostname or proxy URL:

```json
{
  "mcpServers": {
    "context-forge": {
      "url": "http://your-server.example.com:4000/mcp"
    }
  }
}
```

Security reminder:

- The UI/API is authenticated after setup
- The MCP endpoint still has no built-in authentication
- Prefer private networking, a VPN, or a secure tunnel for remote MCP access
