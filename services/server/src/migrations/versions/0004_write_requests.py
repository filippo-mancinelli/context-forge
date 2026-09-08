"""Write requests: agent-proposed SQL and file writes awaiting human approval."""
from __future__ import annotations

VERSION = 4
NAME = "write_requests"
TRANSACTIONAL = True

SQL = """
CREATE TABLE IF NOT EXISTS write_requests (
    id             BIGSERIAL PRIMARY KEY,
    org_id         BIGINT NOT NULL,
    project_id     BIGINT NOT NULL,
    kind           TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending',
    target         TEXT NOT NULL,
    payload        JSONB NOT NULL,
    preview        JSONB NOT NULL,
    reason         TEXT,
    requested_by_kind TEXT NOT NULL,
    requested_by_id   BIGINT,
    requested_by      TEXT NOT NULL,
    decided_by     BIGINT,
    decided_at     TIMESTAMPTZ,
    decision_note  TEXT,
    result         JSONB,
    error          TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at     TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '7 days'
);

CREATE INDEX IF NOT EXISTS write_requests_org_status_idx
    ON write_requests (org_id, status, created_at DESC);
"""


async def upgrade(conn) -> None:
    await conn.execute(SQL)
