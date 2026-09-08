# MCP tool audit and per-key rate limits — design

## Problem

Write tools are in production and nothing records who called what. Only SQL
queries have an audit trail (`query_log`). API keys have no throughput limit.

## Design

### Principal in the MCP context

`src/mcp/context.py` gains a `Principal` (`kind: "user" | "api_key" | "anonymous"`,
`id: int | None`, `label: str`) with `set_current_principal` /
`get_current_principal`. `src/mcp/auth.py` sets it wherever it resolves a
caller: OIDC user → `("user", user_id, username)`, API key →
`("api_key", key_id, key_name)`, auth disabled/transition anonymous →
`("anonymous", None, "anonymous")`.

### Audit table

Migration `0003_mcp_tool_calls`:

```sql
CREATE TABLE mcp_tool_calls (
    id            BIGSERIAL PRIMARY KEY,
    org_id        BIGINT,
    project_id    BIGINT,
    principal_kind TEXT NOT NULL,
    principal_id  BIGINT,
    principal     TEXT NOT NULL,
    tool          TEXT NOT NULL,
    permission    TEXT,
    outcome       TEXT NOT NULL,            -- ok | denied | error | rate_limited
    duration_ms   INT NOT NULL,
    error         TEXT,
    args_summary  JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX mcp_tool_calls_org_created_idx ON mcp_tool_calls (org_id, created_at DESC);
CREATE INDEX mcp_tool_calls_org_tool_idx ON mcp_tool_calls (org_id, tool, created_at DESC);
ALTER TABLE mcp_api_keys ADD COLUMN rate_limit_per_minute INT;   -- NULL = unlimited
```

### Recording

New module `src/mcp/audit.py`:

- `summarize_args(kwargs) -> dict`: keys kept; string values truncated to 200
  chars; values whose key matches `secret|password|token|key|content|sql`
  replaced by `"<redacted>"` except `sql`, which is kept truncated (it is the
  audited action); nested dicts/lists summarised as `"<dict:3>"`/`"<list:5>"`.
- `record_call(...)`: builds the row and enqueues it on an `asyncio.Queue`
  (max 1000; when full the oldest is dropped and a warning is logged once per
  minute). A background writer task started by the MCP server lifespan drains
  the queue in batches of 50 with one `INSERT ... SELECT * FROM unnest(...)`.
  Recording never raises into the tool.
- `audited(tool_name, permission)` async context manager used by the
  permission decorator: measures duration, sets outcome from the exception
  type (`PermissionDenied` → `denied`, `RateLimited` → `rate_limited`, other
  exceptions → `error`, else `ok`). A tool that returns normally but answers
  `{"status": "error", "error": ...}` is recorded as `error` with that text
  (scrubbed, truncated to 500 chars); its return value is untouched. Increments
  the `contextforge_mcp_tool_calls_total{tool,outcome}` counter from
  `src/metrics.py`.

`requires_permission` in `src/mcp/permissions.py` wraps the call with
`audited(fn.__name__, permission)`. Tools without a permission
(`list_projects`, `use_project`, `current_project`) get a new no-permission
decorator `@audit_only` from the same module so every tool call is recorded.

### Rate limit

`src/mcp/ratelimit.py`: in-memory sliding window per API key id
(`deque` of timestamps, pruned to the last 60 s). `check(key_id, limit)`
returns True when under the limit and records the call, else False. Enforced
inside `requires_permission` before the tool runs, only for
`principal.kind == "api_key"` with a non-null limit; over the limit raises
`ToolError("Rate limit exceeded: N calls per minute for this API key")` and the
audit outcome is `rate_limited`. The limit is read from the auth middleware
(loaded with the key) and stored on the principal as `rate_limit_per_minute`.
Single-process semantics are documented; multi-replica deployments need a
shared store (out of scope).

### Retention

Setting `MCP_AUDIT_RETENTION_DAYS` (default 90, `.env.example`, compose).
Scheduler job daily: `DELETE FROM mcp_tool_calls WHERE created_at < NOW() - interval`.

### REST

`src/api/routes/tool_calls.py`, admin/owner of the org:

- `GET /api/organizations/{org_id}/tool-calls?limit=50&offset=0&tool=&outcome=&principal=&project_id=&since=`
  → `{"calls": [...], "total": n}` newest first (limit max 200).
- `GET /api/organizations/{org_id}/tool-calls/stats?window=24h|7d`
  → `{"by_tool": [{"tool","calls","errors","denied","p95_ms"}], "by_principal": [...], "total": n}`.
- `POST/PUT` on MCP keys accept `rate_limit_per_minute` (existing routes in
  `src/api/routes/mcp_keys.py`); listing returns it.

### UI

- `pages/Organization.tsx`: new tab **Tool activity** (admin/owner): stats strip
  (total calls, errors, denied, rate limited in the window; window toggle
  24h/7d), then a table (time, principal, tool, project, outcome badge,
  duration) with filters for tool, outcome and principal, paginated. Mobile
  card layout like the other tables.
- `pages/Settings.tsx` MCP keys: "Rate limit (calls/min)" numeric field on
  create, shown in the list; empty = unlimited.

## Tests (no live DB)

- `summarize_args` redaction and truncation cases.
- `audited` outcomes for ok/denied/error/rate_limited and duration recorded.
- Queue writer batches and never raises when the insert fails (fake pool).
- Rate limiter window behaviour and the decorator raising at the limit only
  for api-key principals.
- REST routes: role gating (member → 403), filters passed to SQL, stats shape.
- `permissions_from_*` untouched: existing permission tests still pass.

## README

Security section: paragraph on the audit trail and the Organization tab;
Settings keys mention rate limits; env var `MCP_AUDIT_RETENTION_DAYS`.
