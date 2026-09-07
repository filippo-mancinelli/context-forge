"""Detailed health for organization admins and owners."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from ...db import get_pool
from ...metrics import collect_queue_stats, last_scheduler_tick
from ...migrations.runner import current_version
from ...scheduler import is_scheduler_running
from ..deps import ActiveOrg, require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["health"])

STALE_TICK_SECONDS = 60
MAX_INDEX_QUEUE_AGE_SECONDS = 3600


@router.get("/details")
async def health_details(org: ActiveOrg = Depends(require_role("admin"))) -> dict:
    """Database, schema version, scheduler heartbeat and queue depths."""
    database = {"ok": True, "latency_ms": 0}
    stats: dict[str, float] = {}
    schema = 0
    try:
        pool = await get_pool()
        started = time.perf_counter()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        database["latency_ms"] = int(round((time.perf_counter() - started) * 1000))
        stats = await collect_queue_stats()
        schema = await current_version(pool)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Health details database check failed: %s", exc)
        database = {"ok": False, "latency_ms": 0}

    tick = last_scheduler_tick()
    tick_age = None if not tick else time.time() - tick
    scheduler = {
        "running": is_scheduler_running(),
        "last_tick_at": None
        if not tick
        else datetime.fromtimestamp(tick, tz=timezone.utc).isoformat(),
    }

    queues = {
        "jobs_pending": int(stats.get("jobs_pending", 0)),
        "jobs_running": int(stats.get("jobs_running", 0)),
        "jobs_dead": int(stats.get("jobs_dead", 0)),
        "index_requests_pending": int(stats.get("index_requests_pending", 0)),
        "index_requests_oldest_age_seconds": int(
            stats.get("index_requests_oldest_age_seconds", 0)
        ),
        "kb_documents_pending": int(stats.get("kb_documents_pending", 0)),
        "web_pages_pending": int(stats.get("web_pages_pending", 0)),
    }

    degraded = (
        not database["ok"]
        or tick_age is None
        or tick_age > STALE_TICK_SECONDS
        or queues["index_requests_oldest_age_seconds"] > MAX_INDEX_QUEUE_AGE_SECONDS
    )
    return {
        "status": "degraded" if degraded else "ok",
        "database": database,
        "schema_version": schema,
        "scheduler": scheduler,
        "queues": queues,
    }
