# Connecting Cursor to context-forge

## Project-Level Config

Create or edit `.cursor/mcp.json` in your project root:

```json
{
  "mcpServers": {
    "context-forge": {
      "url": "http://localhost:4000/mcp"
    }
  }
}
```

## Global Config (all workspaces)

Create or edit `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "context-forge": {
      "url": "http://localhost:4000/mcp"
    }
  }
}
```

## Adding System Instructions

Copy the instructions template to your project:

```bash
cp templates/AGENTS.md /path/to/your/project/.cursor/rules/context-forge.md
```

Or add the content to your existing Cursor rules file.

## Verifying

After saving the config, restart Cursor. In the chat, ask:
```
What MCP tools are available?
```

You should see the context-forge tools listed.

## Remote Server

Replace `localhost:4000` with your server's address:

```json
{
  "mcpServers": {
    "context-forge": {
      "url": "http://your-server.com:4000/mcp"
    }
  }
}
```
