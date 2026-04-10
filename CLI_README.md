# context-forge CLI Authentication

The `forge-cli` tool allows you to configure and test authentication for connecting to a remote context-forge instance.

## Installation

After installing context-forge-server, the CLI will be available as `forge-cli`:

```bash
pip install -e services/server/
```

## Usage

### 1. Generate an API Key

First, generate an API key through the web UI:

1. Open context-forge Settings
2. Go to "MCP Keys" tab
3. Click "Generate API key"
4. Copy the key (shown only once!)

### 2. Configure CLI

Set your API key and server URL:

```bash
forge-cli configure --api-key forge_abc123... --server-url https://your-forge-instance.com
```

Or set them interactively:

```bash
# Set API key
forge-cli configure --api-key forge_abc123...

# Set server URL (default: http://localhost:4000)
forge-cli configure --server-url https://your-forge-instance.com
```

View current configuration:

```bash
forge-cli configure
```

### 3. Test Connection

Verify your credentials work:

```bash
forge-cli test
```

## Configuration

Configuration is stored in `~/.context-forge/config.json`:

```json
{
  "api_key": "forge_abc123...",
  "server_url": "https://your-forge-instance.com"
}
```

## Environment Variables

You can also use environment variables instead of the CLI:

```bash
export CONTEXT_FORGE_API_KEY="forge_abc123..."
export CONTEXT_FORGE_URL="https://your-forge-instance.com"
```

## Using with MCP Clients

When connecting to context-forge from an MCP client, include the API key in the `X-API-Key` header:

```python
# Example MCP client configuration
mcp_client.connect(
    url="https://your-forge-instance.com/mcp",
    headers={"X-API-Key": "forge_abc123..."}
)
```

## API Key Scopes

- `read` - Search repos, read memory
- `write` - Add memory, trigger indexing
- `read,write` - Both read and write (recommended)
- `admin` - Full access including key management
