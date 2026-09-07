"""REST API routes for async job management."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ...db import get_pool
from ..deps import ActiveProject, get_active_project

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
async def list_jobs(limit: int = 50, status: str = None, org: ActiveProject = Depends(get_active_project)):
    """List recent async jobs for the active project.

    Every job is created with an org and a project (the startup backfill
    attributes pre-existing jobs to their organization's default project),
    so listing is scoped to the active project only."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if status:
            rows = await conn.fetch(
                "SELECT id, tool, status, error_message, created_at, updated_at "
                "FROM jobs WHERE status=$1 AND project_id=$2 "
                "ORDER BY created_at DESC LIMIT $3",
                status, org.project_id, limit,
            )
        else:
            rows = await conn.fetch(
                "SELECT id, tool, status, error_message, created_at, updated_at "
                "FROM jobs WHERE project_id=$1 "
                "ORDER BY created_at DESC LIMIT $2",
                org.project_id, limit,
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
async def get_job(job_id: str, org: ActiveProject = Depends(get_active_project)):
    """Get a specific job's status and result (scoped to the active project)."""
    import json
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, tool, params, status, result, error_message, created_at, updated_at "
            "FROM jobs WHERE id=$1 AND project_id=$2",
            job_id, org.project_id,
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
