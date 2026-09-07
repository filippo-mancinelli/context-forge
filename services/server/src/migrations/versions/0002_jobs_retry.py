"""Durable retries for async jobs: attempt counters, schedule and last error."""
from __future__ import annotations

VERSION = 2
NAME = "jobs_retry"
TRANSACTIONAL = True


async def upgrade(conn) -> None:
    await conn.execute(
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS attempts INT NOT NULL DEFAULT 0"
    )
    await conn.execute(
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS max_attempts INT NOT NULL DEFAULT 3"
    )
    await conn.execute(
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS next_attempt_at "
        "TIMESTAMPTZ NOT NULL DEFAULT NOW()"
    )
    await conn.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS last_error TEXT")
    # Serves the scheduler's claim query.
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS jobs_due_idx ON jobs (status, next_attempt_at)"
    )
