"""Prometheus metrics for context-forge. One registry, no per-organization labels."""
from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, generate_latest

from .mcp.jobs import JOB_STATUSES

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

for _status in JOB_STATUSES:
    jobs_by_status.labels(status=_status).set(0)


def render_metrics() -> bytes:
    """Render the registry in the Prometheus text exposition format."""
    return generate_latest(registry)
