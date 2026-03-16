# Connecting OpenAI Codex to context-forge

## Via Config File

Add to your Codex configuration file (`~/.codex/config.toml` or project-level `codex.toml`):

```toml
[[mcp_servers]]
name = "context-forge"
transport = "http"
url = "http://localhost:4000/mcp"
```

## Via CLI

```bash
codex mcp add --transport http context-forge http://localhost:4000/mcp
```

## AGENTS.md Instructions

Codex automatically reads `AGENTS.md` from the project root. Copy the template:

```bash
cp templates/AGENTS.md /path/to/your/project/AGENTS.md
```

## JSON Config Alternative

Some Codex versions use a JSON config:

```json
{
  "mcpServers": {
    "context-forge": {
      "transport": "http",
      "url": "http://localhost:4000/mcp"
    }
  }
}
```

## Remote Server

Replace `localhost:4000` with your server address. For security, consider using an SSH tunnel or placing context-forge behind a reverse proxy with authentication.
