# Connecting Claude Code to context-forge

## Before connecting

1. Start the stack with `docker compose up -d`
2. Open `http://localhost:3000`
3. Finish setup with `SETUP_BOOTSTRAP_TOKEN`
4. Verify `http://localhost:4000/mcp` is reachable
5. Generate an MCP API key from the UI (Settings → API Keys) — the key starts with `forge_`

## One-command setup (current project only)

```bash
claude mcp add context-forge http://localhost:4000/mcp --transport http
```

## Global setup (all projects)

Use `--scope user` so the server is available in every project:

```bash
claude mcp add context-forge http://localhost:4000/mcp --transport http --scope user
```

With API key authentication (recommended for remote servers):

```bash
claude mcp add context-forge https://your-server.example.com/mcp \
  --transport http --scope user \
  --header "X-API-Key: forge_YOUR_API_KEY"
```

> **Important:** place the server name and URL *before* the flags.
> The `--header` flag consumes all following arguments, so flags after it may be ignored.

## Verify

```bash
claude mcp list
```

Then ask Claude Code which MCP tools are available.

## Project instructions

Claude Code reads `CLAUDE.md` from the project root.

```bash
cp templates/CLAUDE.md /path/to/your/project/CLAUDE.md
```

## Remote server

```bash
claude mcp add context-forge https://your-server.example.com/mcp \
  --transport http --scope user \
  --header "X-API-Key: forge_YOUR_API_KEY"
```

If you prefer a tunnel instead of exposing the endpoint publicly:

```bash
ssh -L 4000:localhost:4000 user@your-server.example.com
claude mcp add context-forge http://localhost:4000/mcp --transport http --scope user
```

## Scope reference

| Scope | Flag | Config file | Available in |
|-------|------|-------------|--------------|
| local (default) | `--scope local` | project `.mcp.json` | current project only |
| user | `--scope user` | `~/.claude.json` | all projects |

## Security reminder

- The web UI/API is authenticated after setup
- For remote servers, always use an MCP API key (`X-API-Key` header)
- API keys can be managed from the UI under Settings → API Keys
- Alternatively, expose MCP only on a trusted network, VPN, or tunnel
