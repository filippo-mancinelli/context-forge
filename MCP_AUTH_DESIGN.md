# MCP Authentication System - Design Document

## Overview
Add authentication to the FastMCP server (port 4000) to allow remote CLI agents to securely connect to context-forge.

## Current State
- **API REST (port 8000)**: Admin authentication with session tokens (24h TTL)
- **MCP Server (port 4000)**: NO authentication - completely open

## Design Decisions

### 1. Authentication Approach: API Keys
**Choice**: Static API Keys instead of session tokens

**Why**:
- Simpler for CLI integration (no login flow required)
- Long-lived tokens suitable for agent connections
- Easier to distribute and revoke
- Can be scoped with specific permissions

**Alternative considered**: Session tokens (like REST API)
- Rejected: Requires login flow, more complex for CLI

### 2. Permission Model: Simple Scopes
**Scopes**:
- `read`: Search repos, read memory, list resources
- `write`: Add memory, trigger indexing
- `admin`: Full access including token management

**Default**: New tokens get `read` + `write` scope

**Why simple scopes**:
- MCP tools don't need fine-grained permissions yet
- Easy to understand and manage
- Can be extended later if needed

### 3. Token Storage
**New table**: `mcp_api_keys`

```sql
CREATE TABLE mcp_api_keys (
    id           BIGSERIAL PRIMARY KEY,
    name         TEXT NOT NULL,
    key_hash     TEXT UNIQUE NOT NULL,
    scope        TEXT NOT NULL DEFAULT 'read,write',
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    last_used_at TIMESTAMPTZ,
    expires_at   TIMESTAMPTZ,
    created_by   BIGINT REFERENCES admin_users(id)
);
```

**Security**:
- Keys stored as SHA-256 hashes (like session tokens)
- Never return raw key after creation
- Optional expiration date

### 4. Authentication Flow

#### CLI Side
```python
# 1. User generates API key via Web UI
# 2. Configures CLI with key:
forge configure --api-key "forge_<key>"

# 3. CLI includes key in MCP transport headers
# FastMCP custom transport with Authorization header
```

#### Server Side
```python
# FastMCP middleware to validate key before tool execution
@mcp.middleware()
async def validate_api_key(request):
    api_key = request.headers.get("X-API-Key")
    if not await validate_mcp_key(api_key):
        raise Unauthorized("Invalid API key")
```

### 5. API Key Format
- Prefix: `forge_`
- Length: 48 characters (secrets.token_urlsafe(36))
- Example: `forge_aB3dE7fG9hJ2kL4mN6pQ8rS0tU2vW4xY6z`

## Implementation Plan

### Phase 1: Database & Backend
1. Add `mcp_api_keys` table to db.py DDL
2. Create API endpoints for key management:
   - `POST /api/mcp/keys` - Create new key
   - `GET /api/mcp/keys` - List keys
   - `DELETE /api/mcp/keys/{id}` - Revoke key
3. Add validation helper in security.py:
   - `validate_mcp_api_key(key_hash) -> bool`

### Phase 2: MCP Server Integration
1. Create custom FastMCP transport with auth headers
2. Add middleware to mcp/server.py:
   - Extract X-API-Key header
   - Validate against database
   - Add key info to context for tools
3. Update tools to check scopes if needed

### Phase 3: Web UI
1. Add "API Keys" section to Settings page
2. Show list of keys with scopes and creation date
3. Add "Generate Key" button with name input
4. Show key ONCE after creation (copy prompt)
5. Add revoke button for each key

### Phase 4: CLI Integration
1. Add `configure` command to store API key locally
2. Modify MCP client to use custom transport
3. Add key to X-API-Key header in requests

## Security Considerations

### Transport Security
- Require HTTPS/WSS for production
- Show warning if using HTTP
- Keys sent in headers (never in URL)

### Key Storage
- Hash keys in database (SHA-256)
- Never log raw keys
- Mark last_used_at for audit trail

### Key Lifecycle
- Support expiration dates
- Manual revocation
- Admin-only key creation

## Migration Path

### For Existing Users
1. No breaking changes - MCP remains open during deployment
2. Add migration warning in UI: "MCP will require authentication"
3. Provide grace period (e.g., 30 days)
4. After grace period, require valid API key

### Optional: Authentication Modes
```python
# config.py
MCP_AUTH_MODE = "open" | "required" | "transition"
```

## Testing Checklist
- [ ] Valid API key can access MCP tools
- [ ] Invalid key is rejected with 401
- [ ] Expired key is rejected
- [ ] Revoked key stops working immediately
- [ ] Scope permissions are enforced
- [ ] CLI can connect with configured key
- [ ] UI shows keys and allows creation/revocation
- [ ] Key is shown only once after creation
