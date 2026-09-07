"""APScheduler setup for periodic, per-organization indexing and git pulls."""
from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .db import get_pool
from .indexer.git_manager import pull_all_repos
from .indexer.indexer import run_index_repo, run_pending_index_requests, sync_repos_config
from .mcp.jobs import run_claimed_job
from .mcp.oauth_bridge import purge_expired_flows
from .org_config import get_org_config, iter_org_configs
from .vector_index import ensure_all_indexes

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
        await run_index_repo(org_id, repo, cfg.indexing)


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


async def _check_kb_documents() -> None:
    """Process knowledge-base documents left in the pending state."""
    from .kb.store import process_pending_documents

    await process_pending_documents()


async def _check_web_pages() -> None:
    """Process web pages left in the pending state."""
    from .web.store import process_pending_pages

    await process_pending_pages()


async def _check_web_sites() -> None:
    """Crawl web sites left in the pending state."""
    from .web.crawler import process_pending_sites

    await process_pending_sites()


async def _purge_expired_oauth_flows() -> None:
    """Delete expired OAuth bridge flows: they hold Keycloak tokens in clear text."""
    deleted = await purge_expired_flows()
    if deleted:
        logger.info("Purged %d expired OAuth bridge flow(s)", deleted)


_JOB_CLAIM_BATCH = 5
_JOB_STUCK_MINUTES = 10


async def _check_jobs() -> None:
    """Requeue jobs stuck in 'running', then claim and run the jobs that are due."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE jobs SET status = 'pending', updated_at = NOW() "
            "WHERE status = 'running' AND updated_at < NOW() - INTERVAL '10 minutes'"
        )
        rows = await conn.fetch(
            """
            UPDATE jobs
            SET status = 'running', attempts = attempts + 1, updated_at = NOW()
            WHERE id IN (
                SELECT id FROM jobs
                WHERE status = 'pending' AND next_attempt_at <= NOW()
                ORDER BY next_attempt_at
                LIMIT 5
                FOR UPDATE SKIP LOCKED
            )
            RETURNING id, params, attempts, max_attempts
            """
        )

    if not rows:
        return
    await asyncio.gather(
        *(run_claimed_job(dict(row)) for row in rows), return_exceptions=True
    )


async def start_scheduler() -> None:
    global _scheduler
    _scheduler = AsyncIOScheduler()

    # Check for pending index requests every 10 seconds.
    _scheduler.add_job(
        _check_index_requests, "interval", seconds=10, id="index_requests", replace_existing=True
    )

    # Claim and run due async jobs; also requeues jobs stranded by a crash.
    _scheduler.add_job(
        _check_jobs, "interval", seconds=5, id="jobs", replace_existing=True
    )

    # Safety-net for knowledge-base documents whose background task didn't run.
    _scheduler.add_job(
        _check_kb_documents, "interval", seconds=20, id="kb_documents", replace_existing=True
    )

    # Safety-net for web pages whose background fetch didn't run.
    _scheduler.add_job(
        _check_web_pages, "interval", seconds=20, id="web_pages", replace_existing=True
    )

    # Safety-net for site crawls whose background task didn't run.
    _scheduler.add_job(
        _check_web_sites, "interval", seconds=30, id="web_sites", replace_existing=True
    )

    # Reaper: expired OAuth bridge flows hold Keycloak tokens in clear text and
    # must not outlive their TTL. Global job (not per-organization).
    _scheduler.add_job(
        _purge_expired_oauth_flows, "interval", minutes=5, id="oauth_flow_reaper",
        replace_existing=True,
    )

    # Self-healing: rebuilds any HNSW index left INVALID by an interrupted build.
    _scheduler.add_job(
        ensure_all_indexes, "interval", hours=6, id="hnsw_indexes",
        replace_existing=True,
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
                await run_index_repo(org_id, repo, cfg.indexing)
