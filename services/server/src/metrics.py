"""Prometheus metrics for context-forge. One registry, no per-organization labels."""
from __future__ import annotations

import time
from typing import Optional

from prometheus_client import CollectorRegistry, Counter, Gauge, generate_latest

from .db import get_pool
from .mcp import audit as mcp_audit
from .mcp.jobs import JOB_STATUSES
from .migrations.runner import current_version

registry = CollectorRegistry()

jobs_by_status = Gauge(
    "contextforge_jobs", "Async jobs by status", ["status"], registry=registry
)
index_requests_pending = Gauge(
    "contextforge_index_requests_pending", "Index requests not yet processed",
    registry=registry,
)
index_requests_oldest_age_seconds = Gauge(
    "contextforge_index_requests_oldest_age_seconds",
    "Age of the oldest unprocessed index request", registry=registry,
)
kb_documents_pending = Gauge(
    "contextforge_kb_documents_pending", "Knowledge-base documents awaiting processing",
    registry=registry,
)
web_pages_pending = Gauge(
    "contextforge_web_pages_pending", "Web pages awaiting processing", registry=registry
)
scheduler_tick_timestamp_seconds = Gauge(
    "contextforge_scheduler_tick_timestamp_seconds",
    "Unix time of the last scheduler metrics tick", registry=registry,
)
schema_version = Gauge(
    "contextforge_schema_version", "Applied schema migration version", registry=registry
)
# Declared here so the whole platform shares one registry; incremented by the
# MCP audit feature.
mcp_tool_calls_total = Counter(
    "contextforge_mcp_tool_calls_total", "MCP tool calls", ["tool", "outcome"],
    registry=registry,
)
mcp_audit_queue_depth = Gauge(
    "contextforge_mcp_audit_queue_depth",
    "MCP audit rows waiting to be written", registry=registry,
)

for _status in JOB_STATUSES:
    jobs_by_status.labels(status=_status).set(0)


def render_metrics() -> bytes:
    """Render the registry in the Prometheus text exposition format."""
    return generate_latest(registry)


# Job counts are scoped to tool = 'http' like the executor: 'org_reembed' rows
# have their own producer and their own status vocabulary.
_QUEUE_STATS_SQL = """
SELECT
    (SELECT COUNT(*) FROM jobs WHERE tool = 'http' AND status = 'pending')  AS jobs_pending,
    (SELECT COUNT(*) FROM jobs WHERE tool = 'http' AND status = 'running')  AS jobs_running,
    (SELECT COUNT(*) FROM jobs WHERE tool = 'http' AND status = 'done')     AS jobs_done,
    (SELECT COUNT(*) FROM jobs WHERE tool = 'http' AND status = 'error')    AS jobs_error,
    (SELECT COUNT(*) FROM jobs WHERE tool = 'http' AND status = 'dead')     AS jobs_dead,
    (SELECT COUNT(*) FROM index_requests WHERE processed_at IS NULL)
        AS index_requests_pending,
    (SELECT EXTRACT(EPOCH FROM (NOW() - MIN(requested_at)))
       FROM index_requests WHERE processed_at IS NULL)
        AS index_requests_oldest_age_seconds,
    (SELECT COUNT(*) FROM kb_documents WHERE status = 'pending') AS kb_documents_pending,
    (SELECT COUNT(*) FROM web_pages WHERE status = 'pending')    AS web_pages_pending
"""

_last_tick: Optional[float] = None


async def collect_queue_stats() -> dict[str, float]:
    """One round-trip snapshot of every processing queue. No per-org breakdown."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(_QUEUE_STATS_SQL)
    return {key: float(value or 0) for key, value in dict(row).items()}


def record_scheduler_tick(now: Optional[float] = None) -> None:
    """Record a scheduler heartbeat, for the gauge and for /api/health/details."""
    global _last_tick
    _last_tick = time.time() if now is None else now
    scheduler_tick_timestamp_seconds.set(_last_tick)


def last_scheduler_tick() -> Optional[float]:
    return _last_tick


async def refresh_metrics() -> None:
    """Repopulate every gauge from the database and beat the heartbeat.

    Every await happens first; the gauges are only touched once all I/O has
    succeeded, so a failure partway through (e.g. the schema-version lookup)
    leaves every gauge and the heartbeat at their previous values instead of
    applying a half-updated tick.
    """
    stats = await collect_queue_stats()
    pool = await get_pool()
    version = await current_version(pool)

    for status in JOB_STATUSES:
        jobs_by_status.labels(status=status).set(stats.get(f"jobs_{status}", 0.0))
    index_requests_pending.set(stats["index_requests_pending"])
    index_requests_oldest_age_seconds.set(stats["index_requests_oldest_age_seconds"])
    kb_documents_pending.set(stats["kb_documents_pending"])
    web_pages_pending.set(stats["web_pages_pending"])
    mcp_audit_queue_depth.set(mcp_audit.queue_depth())
    schema_version.set(version)
    record_scheduler_tick()
