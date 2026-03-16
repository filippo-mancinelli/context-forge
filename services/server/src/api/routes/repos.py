"""REST API routes for repository management."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from ...db import get_pool
from ...indexer.indexer import index_repo, sync_repos_config

router = APIRouter(prefix="/repos", tags=["repos"])


class RepoOut(BaseModel):
    name: str
    type: str
    url: Optional[str] = None
    path: Optional[str] = None
    branch: str
    language: str
    status: str
    last_indexed_at: Optional[str] = None
    total_chunks: int
    error_message: Optional[str] = None


@router.get("", response_model=list[RepoOut])
async def list_repos():
    """List all repos and their indexing status."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT name, type, url, path, branch, language, status, "
            "last_indexed_at, total_chunks, error_message FROM repos ORDER BY name"
        )
    result = []
    for r in rows:
        d = dict(r)
        if d.get("last_indexed_at"):
            d["last_indexed_at"] = d["last_indexed_at"].isoformat()
        result.append(RepoOut(**d))
    return result


@router.post("/{repo_name}/index")
async def trigger_index(repo_name: str, background_tasks: BackgroundTasks):
    """Queue a repo for re-indexing."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval("SELECT name FROM repos WHERE name=$1", repo_name)
    if not exists:
        raise HTTPException(status_code=404, detail=f"Repo '{repo_name}' not found")

    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO index_requests (repo_name) VALUES ($1)",
            repo_name,
        )
    return {"status": "queued", "repo": repo_name}


@router.post("/index-all")
async def trigger_index_all():
    """Queue all repos for re-indexing."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO index_requests (repo_name) VALUES (NULL)")
    return {"status": "queued", "message": "All repos queued for indexing"}


@router.post("/sync-config")
async def sync_config():
    """Reload context-forge.yml and sync repos to DB."""
    from ...config import reload_forge_config
    reload_forge_config()
    await sync_repos_config()
    return {"status": "ok", "message": "Config synced"}


@router.get("/{repo_name}/files")
async def list_files(repo_name: str, path: str = ""):
    """List files in a repo directory."""
    import os
    from pathlib import Path
    from ...config import get_forge_config
    from ...indexer.git_manager import get_repo_local_path

    cfg = get_forge_config()
    repo_cfg = next((r for r in cfg.repos if r.name == repo_name), None)
    if not repo_cfg:
        raise HTTPException(status_code=404, detail=f"Repo '{repo_name}' not found")

    repo_path = Path(get_repo_local_path(repo_cfg))
    target = repo_path / path.lstrip("/") if path else repo_path

    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found")

    entries = []
    for entry in sorted(target.iterdir(), key=lambda e: (e.is_file(), e.name)):
        entries.append({
            "name": entry.name,
            "type": "file" if entry.is_file() else "directory",
            "size": entry.stat().st_size if entry.is_file() else None,
            "path": str(entry.relative_to(repo_path)),
        })
    return {"path": path, "entries": entries}
