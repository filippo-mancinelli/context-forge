# Connecting Claude Code to context-forge

## Before connecting

1. Start the stack with `docker compose up -d`
2. Open `http://localhost:3000`
3. Finish setup with `SETUP_BOOTSTRAP_TOKEN`
4. Verify `http://localhost:4000/mcp` is reachable

## One-command setup

```bash
claude mcp add --transport http context-forge http://localhost:4000/mcp
```

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
claude mcp add --transport http context-forge http://your-server.example.com:4000/mcp
```

If you want a safer remote path, use an SSH tunnel:

```bash
ssh -L 4000:localhost:4000 user@your-server.example.com
claude mcp add --transport http context-forge http://localhost:4000/mcp
```

Security reminder:

- The web UI/API is authenticated after setup
- The MCP endpoint still has no built-in authentication
- Expose MCP only on a trusted network, VPN, or tunnel
