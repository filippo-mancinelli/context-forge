"""APScheduler setup for periodic, per-organization indexing and git pulls."""
from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .indexer.git_manager import pull_all_repos
from .indexer.indexer import index_repo, run_pending_index_requests, sync_repos_config
from .org_config import get_org_config, iter_org_configs

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None

_DEFAULT_CRON = "0 */6 * * *"
_JOB_PREFIX = "refresh_org_"


async def _scheduled_refresh_org(org_id: int) -> None:
    """Pull latest changes and re-index a single organization's repos."""
    cfg = await get_org_config(org_id)
    if not cfg.indexing.auto:
        return
    logger.info("Scheduled refresh: org=%s repos=%d", org_id, len(cfg.repos))
    await pull_all_repos(cfg.repos, org_id)
    for repo in cfg.repos:
        await index_repo(org_id, repo, cfg.indexing)


def _cron_trigger(schedule: str) -> CronTrigger:
    parts = (schedule or _DEFAULT_CRON).split()
    if len(parts) == 5:
        return CronTrigger(
            minute=parts[0], hour=parts[1], day=parts[2], month=parts[3], day_of_week=parts[4]
        )
    logger.warning("Invalid cron schedule '%s', defaulting to every 6h", schedule)
    return CronTrigger(minute="0", hour="*/6")


async def sync_scheduler_jobs() -> None:
    """(Re)register one refresh job per organization from its own schedule.

    Idempotent: stale per-org jobs are removed and current ones replaced. Safe to
    call on startup and after any organization's indexing config changes.
    """
    if _scheduler is None:
        return

    configs = await iter_org_configs()
    wanted: set[str] = set()
    for org_id, cfg in configs:
        job_id = f"{_JOB_PREFIX}{org_id}"
        wanted.add(job_id)
        _scheduler.add_job(
            _scheduled_refresh_org,
            _cron_trigger(cfg.indexing.schedule),
            args=[org_id],
            id=job_id,
            replace_existing=True,
        )

    # Drop jobs for organizations that no longer exist.
    for job in _scheduler.get_jobs():
        if job.id.startswith(_JOB_PREFIX) and job.id not in wanted:
            _scheduler.remove_job(job.id)


async def _check_index_requests() -> None:
    """Process pending index requests (from UI or MCP tool)."""
    await run_pending_index_requests()


async def start_scheduler() -> None:
    global _scheduler
    _scheduler = AsyncIOScheduler()

    # Check for pending index requests every 10 seconds.
    _scheduler.add_job(
        _check_index_requests, "interval", seconds=10, id="index_requests", replace_existing=True
    )

    _scheduler.start()
    await sync_scheduler_jobs()
    logger.info("Scheduler started (per-organization refresh jobs)")


async def stop_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


async def initial_index() -> None:
    """On startup: sync per-org config to DB, clone remotes, index pending repos."""
    await sync_repos_config()

    from .db import get_pool

    pool = await get_pool()
    for org_id, cfg in await iter_org_configs():
        if not cfg.indexing.auto:
            logger.info("Auto-indexing disabled for org=%s, skipping", org_id)
            continue

        await pull_all_repos(cfg.repos, org_id)

        async with pool.acquire() as conn:
            pending = await conn.fetch(
                "SELECT name FROM repos WHERE org_id=$1 AND status='pending'", org_id
            )

        config_repos = {r.name: r for r in cfg.repos}
        for row in pending:
            repo = config_repos.get(row["name"])
            if repo:
                logger.info("Initial index for org=%s repo=%s", org_id, repo.name)
                await index_repo(org_id, repo, cfg.indexing)
