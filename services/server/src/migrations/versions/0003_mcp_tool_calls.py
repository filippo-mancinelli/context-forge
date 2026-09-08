"""Audit trail of MCP tool calls, plus a per-key rate limit column."""

VERSION = 3
NAME = "mcp_tool_calls"
TRANSACTIONAL = True

SQL = """
CREATE TABLE IF NOT EXISTS mcp_tool_calls (
    id             BIGSERIAL PRIMARY KEY,
    org_id         BIGINT,
    project_id     BIGINT,
    principal_kind TEXT NOT NULL,
    principal_id   BIGINT,
    principal      TEXT NOT NULL,
    tool           TEXT NOT NULL,
    permission     TEXT,
    outcome        TEXT NOT NULL,
    duration_ms    INT NOT NULL,
    error          TEXT,
    args_summary   JSONB,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS mcp_tool_calls_org_created_idx
    ON mcp_tool_calls (org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS mcp_tool_calls_org_tool_idx
    ON mcp_tool_calls (org_id, tool, created_at DESC);

ALTER TABLE mcp_api_keys ADD COLUMN IF NOT EXISTS rate_limit_per_minute INT;
"""


async def upgrade(conn) -> None:
    await conn.execute(SQL)
