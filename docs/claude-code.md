# Connecting Claude Code to context-forge

## One-Command Setup

```bash
claude mcp add --transport http context-forge http://localhost:4000/mcp
```

That's it. Verify with:

```bash
claude mcp list
```

## Manual Setup

Add to `~/.claude.json` (create if it doesn't exist):

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

## Project-Level Instructions

Copy the system prompt template to any project you work on:

```bash
cp templates/CLAUDE.md /path/to/your/project/CLAUDE.md
```

Claude Code automatically reads `CLAUDE.md` from the project root as instructions.

## Verifying the Connection

In Claude Code, run:

```
> What MCP tools do you have available?
```

You should see tools like `memory_add`, `repo_search`, `job_submit`, etc.

## Remote Server (VPS)

If context-forge runs on a remote server, replace `localhost` with the server address:

```bash
claude mcp add --transport http context-forge http://your-server.com:4000/mcp
```

Or use an SSH tunnel for security:

```bash
ssh -L 4000:localhost:4000 user@your-server.com
# then:
claude mcp add --transport http context-forge http://localhost:4000/mcp
```
