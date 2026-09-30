"""REST API dei repository selezionati dal progetto attivo.

Il catalogo (``/api/catalog/repos``) registra, modifica e cancella i repository;
qui si leggono quelli scelti dal progetto, si cerca nel loro indice e se ne
chiede la reindicizzazione.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...catalog import repos as repo_catalog
from ...config import RepoRecord
from ...db import get_pool
from ..deps import ActiveProject, get_active_project, require_project_role

router = APIRouter(prefix="/repos", tags=["repos"])


async def _project_repo(org: ActiveProject, repo_name: str) -> RepoRecord:
    try:
        return await repo_catalog.resolve_project_repo(org.org_id, org.project_id, repo_name)
    except (repo_catalog.RepoNotFoundError, repo_catalog.RepoNotAvailableError) as e:
        raise HTTPException(status_code=404, detail=str(e))


class RepoOut(BaseModel):
    id: int
    name: str
    type: str
    url: Optional[str] = None
    path: Optional[str] = None
    branch: str
    language: str
    description: Optional[str] = None
    restricted: bool = False
    status: str
    last_indexed_at: Optional[str] = None
    total_chunks: int
    error_message: Optional[str] = None


class RepoSearchRequest(BaseModel):
    query: str
    repos: Optional[list[str]] = None
    limit: int = 20


@router.get("", response_model=list[RepoOut])
async def list_repos(org: ActiveProject = Depends(get_active_project)):
    """Repositories selected by the active project and their indexing status."""
    return [RepoOut(**r) for r in await repo_catalog.list_project_repos(org.org_id, org.project_id)]


@router.post("/search")
async def search_repos(req: RepoSearchRequest, org: ActiveProject = Depends(get_active_project)):
    """Search indexed repository chunks (hybrid vector + full-text, project-scoped)."""
    from ...search import search_repo_chunks

    try:
        results = await search_repo_chunks(
            org.org_id, req.query, repos=req.repos, limit=req.limit, project_id=org.project_id
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {e}")

    return {"results": results, "count": len(results)}


class RepoSymbolSearchRequest(BaseModel):
    query: str
    repos: Optional[list[str]] = None
    limit: int = 20


@router.post("/symbols")
async def search_symbols(req: RepoSymbolSearchRequest, org: ActiveProject = Depends(get_active_project)):
    """Look up function/class/method/type definitions by name (lexical, not semantic)."""
    from ...search import search_repo_symbols

    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Query must not be empty")
    try:
        results = await search_repo_symbols(
            org.org_id, req.query.strip(), repos=req.repos, limit=req.limit, project_id=org.project_id
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Symbol search failed: {e}")

    return {"results": results, "count": len(results)}


@router.get("/relationships")
async def list_relationships(repo: Optional[str] = None, org: ActiveProject = Depends(get_active_project)):
    """Semantic relationships between the repositories selected by the project."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH centroids AS (
                SELECT r.name AS repo_name, avg(c.embedding) AS centroid, count(*) AS chunk_count
                FROM repo_chunks c
                JOIN project_repos pr ON pr.repo_id = c.repo_id AND pr.project_id = $3
                JOIN repos r ON r.id = c.repo_id
                WHERE c.org_id = $2
                GROUP BY r.name
            )
            SELECT
                a.repo_name AS repo_a,
                b.repo_name AS repo_b,
                round((1 - (a.centroid <=> b.centroid))::numeric, 4) AS similarity,
                a.chunk_count AS chunks_a,
                b.chunk_count AS chunks_b
            FROM centroids a
            CROSS JOIN centroids b
            WHERE a.repo_name < b.repo_name
              AND ($1::text IS NULL OR a.repo_name = $1 OR b.repo_name = $1)
            ORDER BY similarity DESC
            LIMIT 25
            """,
            repo,
            org.org_id,
            org.project_id,
        )
    return {"relationships": [dict(r) for r in rows], "count": len(rows)}


@router.post("/index-all")
async def trigger_index_all(org: ActiveProject = Depends(require_project_role("member"))):
    """Queue every repository selected by the active project for re-indexing."""
    await repo_catalog.queue_index(org.org_id, None, org.project_id)
    return {"status": "queued", "message": "All repos queued for indexing"}


@router.post("/{repo_name}/index")
async def trigger_index(repo_name: str, org: ActiveProject = Depends(require_project_role("member"))):
    """Queue a repository selected by the project for re-indexing."""
    repo = await _project_repo(org, repo_name)
    await repo_catalog.queue_index(org.org_id, repo.id, org.project_id)
    return {"status": "queued", "repo": repo.name}


@router.post("/{repo_name}/cancel-index")
async def cancel_index(repo_name: str, org: ActiveProject = Depends(require_project_role("member"))):
    """Stop a running index run (or clear a stale 'indexing' status)."""
    repo = await _project_repo(org, repo_name)
    cancelled = await repo_catalog.cancel_index(org.org_id, repo.id)
    return {"status": "cancelled" if cancelled else "reset", "repo": repo.name}


@router.get("/{repo_name}/files")
async def list_files(repo_name: str, path: str = "", org: ActiveProject = Depends(get_active_project)):
    """List files in a repo directory."""
    from ...indexer.git_manager import get_repo_local_path

    repo = await _project_repo(org, repo_name)
    repo_path = Path(get_repo_local_path(repo, org.org_id))

    # A repo can be indexed while its working tree is not cached on this server
    # (e.g. the clone lives on ephemeral storage that was cleared). Surface an
    # empty, "unavailable" listing so the detail page still renders its analytics.
    if not repo_path.exists():
        return {"path": "", "entries": [], "available": False}

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
    return {"path": path, "entries": entries, "available": True}


@router.get("/{repo_name}/stats")
async def repo_stats(repo_name: str, org: ActiveProject = Depends(get_active_project)):
    """Repository-level analytics for the drill-down view."""
    repo = await _project_repo(org, repo_name)
    pool = await get_pool()
    async with pool.acquire() as conn:
        repo_row = await conn.fetchrow(
            """
            SELECT name, type, url, path, branch, language, status, last_indexed_at, total_chunks, error_message
            FROM repos
            WHERE id = $1
            """,
            repo.id,
        )
        chunk_types_rows = await conn.fetch(
            """
            SELECT chunk_type, count(*) AS count
            FROM repo_chunks
            WHERE repo_id = $1
            GROUP BY chunk_type
            ORDER BY count DESC
            """,
            repo.id,
        )
        ext_rows = await conn.fetch(
            """
            SELECT
                lower(split_part(file_path, '.', array_length(string_to_array(file_path, '.'), 1))) AS extension,
                count(*) AS count
            FROM repo_chunks
            WHERE repo_id = $1 AND position('.' in file_path) > 0
            GROUP BY extension
            ORDER BY count DESC
            LIMIT 8
            """,
            repo.id,
        )

    repo_data = dict(repo_row)
    if repo_data.get("last_indexed_at"):
        repo_data["last_indexed_at"] = repo_data["last_indexed_at"].isoformat()

    return {
        "repo": repo_data,
        "chunk_types": [dict(r) for r in chunk_types_rows],
        "by_extension": [
            {"extension": f".{r['extension']}" if r["extension"] else "(none)", "count": r["count"]}
            for r in ext_rows
        ],
    }
