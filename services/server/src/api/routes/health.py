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
    # The connectivity probe alone decides database.ok/latency_ms: it must
    # not be overwritten by a later failure in the queue/schema-version
    # check below, or a reachable database gets misreported as unreachable.
    database = {"ok": True, "latency_ms": 0}
    pool = None
    try:
        pool = await get_pool()
        started = time.perf_counter()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        database["latency_ms"] = int(round((time.perf_counter() - started) * 1000))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Health details database probe failed: %s", exc)
        database = {"ok": False, "latency_ms": 0}

    # Queue depths and schema version are a second, independent check: a
    # failure here (or a database that was already unreachable above) leaves
    # them at their zero defaults and degrades status via stats_ok, without
    # touching the database result computed above.
    stats: dict[str, float] = {}
    schema = 0
    stats_ok = False
    if database["ok"]:
        try:
            stats = await collect_queue_stats()
            schema = await current_version(pool)
            stats_ok = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Health details queue/schema check failed: %s", exc)

    tick = last_scheduler_tick()
    tick_age = None if not tick else time.time() - tick
    scheduler = {
        "running": is_scheduler_running(),
        "last_tick_at": None
        if not tick
        else datetime.fromtimestamp(tick, tz=timezone.utc).isoformat(),
    }

    # Compared against the threshold as a raw float, before truncation to an
    # int for display, so e.g. 3600.5s correctly degrades instead of reading
    # as the healthy "3600" it would round down to.
    index_requests_oldest_age_seconds = float(
        stats.get("index_requests_oldest_age_seconds", 0)
    )
    queues = {
        "jobs_pending": int(stats.get("jobs_pending", 0)),
        "jobs_running": int(stats.get("jobs_running", 0)),
        "jobs_dead": int(stats.get("jobs_dead", 0)),
        "index_requests_pending": int(stats.get("index_requests_pending", 0)),
        "index_requests_oldest_age_seconds": int(index_requests_oldest_age_seconds),
        "kb_documents_pending": int(stats.get("kb_documents_pending", 0)),
        "web_pages_pending": int(stats.get("web_pages_pending", 0)),
    }

    degraded = (
        not database["ok"]
        or not stats_ok
        or tick_age is None
        or tick_age > STALE_TICK_SECONDS
        or index_requests_oldest_age_seconds > MAX_INDEX_QUEUE_AGE_SECONDS
    )
    return {
        "status": "degraded" if degraded else "ok",
        "database": database,
        "schema_version": schema,
        "scheduler": scheduler,
        "queues": queues,
    }
