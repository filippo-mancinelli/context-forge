"""REST API routes for CI/CD context (live from GitHub Actions / GitLab CI)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ...ci import service
from ...ci.service import CiError
from ..deps import ActiveProject, get_active_project

router = APIRouter(prefix="/ci", tags=["ci"])


@router.get("/{repo_name}/runs")
async def recent_runs(repo_name: str, limit: int = 10, project: ActiveProject = Depends(get_active_project)):
    try:
        runs = await service.recent_runs(project.org_id, project.project_id, repo_name, limit=limit)
    except CiError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"CI provider request failed: {e}")
    return {"repo": repo_name, "runs": runs, "count": len(runs)}


@router.get("/{repo_name}/failure")
async def failure_detail(
    repo_name: str, run_id: Optional[int] = None, project: ActiveProject = Depends(get_active_project)
):
    try:
        return await service.failure_detail(project.org_id, project.project_id, repo_name, run_id=run_id)
    except CiError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"CI provider request failed: {e}")
