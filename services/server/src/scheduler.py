"""APScheduler setup for periodic indexing and git pulls."""
from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import get_forge_config
from .indexer.git_manager import pull_all_repos
from .indexer.indexer import index_repo, run_pending_index_requests, sync_repos_config

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def _scheduled_refresh() -> None:
    """Pull latest changes and re-index all repos."""
    cfg = get_forge_config()
    logger.info("Scheduled refresh: pulling and re-indexing %d repos", len(cfg.repos))
    await pull_all_repos(cfg.repos)
    for repo in cfg.repos:
        await index_repo(repo)


async def _check_index_requests() -> None:
    """Process pending index requests (from UI or MCP tool)."""
    await run_pending_index_requests()


async def start_scheduler() -> None:
    global _scheduler
    cfg = get_forge_config()
    schedule = cfg.indexing.schedule or "0 */6 * * *"

    _scheduler = AsyncIOScheduler()

    # Parse cron schedule
    parts = schedule.split()
    if len(parts) == 5:
        trigger = CronTrigger(
            minute=parts[0], hour=parts[1], day=parts[2],
            month=parts[3], day_of_week=parts[4],
        )
    else:
        logger.warning("Invalid cron schedule '%s', defaulting to every 6h", schedule)
        trigger = CronTrigger(minute="0", hour="*/6")

    _scheduler.add_job(_scheduled_refresh, trigger, id="scheduled_refresh", replace_existing=True)

    # Check for pending index requests every 10 seconds
    _scheduler.add_job(_check_index_requests, "interval", seconds=10, id="index_requests", replace_existing=True)

    _scheduler.start()
    logger.info("Scheduler started (cron: %s)", schedule)


async def stop_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


async def initial_index() -> None:
    """On startup: sync config to DB, clone remotes, index unindexed repos."""
    await sync_repos_config()
    cfg = get_forge_config()

    if not cfg.indexing.auto:
        logger.info("Auto-indexing disabled, skipping initial index")
        return

    # Clone remote repos first
    await pull_all_repos(cfg.repos)

    from .db import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        pending = await conn.fetch(
            "SELECT name FROM repos WHERE status = 'pending'"
        )

    from .config import get_forge_config as gcfg
    config_repos = {r.name: r for r in gcfg().repos}

    for row in pending:
        repo = config_repos.get(row["name"])
        if repo:
            logger.info("Initial index for %s", repo.name)
            await index_repo(repo)
