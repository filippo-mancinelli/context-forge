"""REST API routes for async job management."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ...db import get_pool
from ..deps import ActiveOrg, get_active_org

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
async def list_jobs(limit: int = 50, status: str = None, org: ActiveOrg = Depends(get_active_org)):
    """List recent async jobs for the active organization.

    Jobs without an organization (e.g. created by unauthenticated MCP calls)
    are included so existing behaviour is preserved."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if status:
            rows = await conn.fetch(
                "SELECT id, tool, status, error_message, created_at, updated_at "
                "FROM jobs WHERE status=$1 AND (org_id=$2 OR org_id IS NULL) "
                "ORDER BY created_at DESC LIMIT $3",
                status, org.org_id, limit,
            )
        else:
            rows = await conn.fetch(
                "SELECT id, tool, status, error_message, created_at, updated_at "
                "FROM jobs WHERE (org_id=$1 OR org_id IS NULL) "
                "ORDER BY created_at DESC LIMIT $2",
                org.org_id, limit,
            )
    jobs = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        d["created_at"] = d["created_at"].isoformat()
        d["updated_at"] = d["updated_at"].isoformat()
        jobs.append(d)
    return {"jobs": jobs, "count": len(jobs)}


@router.get("/{job_id}")
async def get_job(job_id: str, org: ActiveOrg = Depends(get_active_org)):
    """Get a specific job's status and result (scoped to the active org)."""
    import json
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, tool, params, status, result, error_message, created_at, updated_at "
            "FROM jobs WHERE id=$1 AND (org_id=$2 OR org_id IS NULL)",
            job_id, org.org_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    d = dict(row)
    d["id"] = str(d["id"])
    d["created_at"] = d["created_at"].isoformat()
    d["updated_at"] = d["updated_at"].isoformat()
    if d.get("result") and isinstance(d["result"], str):
        d["result"] = json.loads(d["result"])
    return d
