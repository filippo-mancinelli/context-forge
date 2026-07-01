"""REST API routes for repository management."""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from ...db import get_pool
from ...indexer.indexer import index_repo, sync_repos_config
from ...org_config import get_org_config, persist_org_config
from ..deps import ActiveOrg, get_active_org, require_role

router = APIRouter(prefix="/repos", tags=["repos"])


async def _assert_repo_in_org(repo_name: str, org_id: int) -> None:
    """Raise 404 if the repo does not belong to the active organization."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM repos WHERE org_id = $1 AND name = $2", org_id, repo_name
        )
    if not exists:
        raise HTTPException(status_code=404, detail=f"Repo '{repo_name}' not found")


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


class RepoSearchRequest(BaseModel):
    query: str
    repos: Optional[list[str]] = None
    limit: int = 20


@router.get("", response_model=list[RepoOut])
async def list_repos(org: ActiveOrg = Depends(get_active_org)):
    """List repos visible to the active organization and their indexing status."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT name, type, url, path, branch, language, status, "
            "last_indexed_at, total_chunks, error_message FROM repos "
            "WHERE org_id = $1 ORDER BY name",
            org.org_id,
        )
    result = []
    for r in rows:
        d = dict(r)
        if d.get("last_indexed_at"):
            d["last_indexed_at"] = d["last_indexed_at"].isoformat()
        result.append(RepoOut(**d))
    return result


@router.post("/search")
async def search_repos(req: RepoSearchRequest, org: ActiveOrg = Depends(get_active_org)):
    """Search indexed repository chunks by semantic similarity (org-scoped)."""
    from ...indexer.embedder import embed_text

    def _vector_to_pg(embedding: list[float]) -> str:
        return "[" + ",".join(f"{float(v):.10f}" for v in embedding) + "]"

    try:
        embedding = await embed_text(req.query)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Embedding failed: {e}")

    embedding_str = _vector_to_pg(embedding)
    pool = await get_pool()

    async with pool.acquire() as conn:
        if req.repos:
            rows = await conn.fetch(
                """
                SELECT repo_name, file_path, chunk_type, content, metadata,
                       1 - (embedding <=> $1::vector) AS score
                FROM repo_chunks
                WHERE org_id = $2 AND repo_name = ANY($3)
                ORDER BY embedding <=> $1::vector
                LIMIT $4
                """,
                embedding_str,
                org.org_id,
                req.repos,
                req.limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT repo_name, file_path, chunk_type, content, metadata,
                       1 - (embedding <=> $1::vector) AS score
                FROM repo_chunks
                WHERE org_id = $2
                ORDER BY embedding <=> $1::vector
                LIMIT $3
                """,
                embedding_str,
                org.org_id,
                req.limit,
            )

    results = []
    for row in rows:
        metadata = row["metadata"]
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        results.append(
            {
                "repo_name": row["repo_name"],
                "file_path": row["file_path"],
                "chunk_type": row["chunk_type"],
                "content": row["content"],
                "metadata": metadata,
                "score": round(float(row["score"]), 4),
            }
        )
    return {"results": results, "count": len(results)}


@router.get("/relationships")
async def list_relationships(repo: Optional[str] = None, org: ActiveOrg = Depends(get_active_org)):
    """Get semantic relationships between repositories (org-scoped)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH centroids AS (
                SELECT repo_name, avg(embedding) AS centroid, count(*) AS chunk_count
                FROM repo_chunks
                WHERE org_id = $2
                GROUP BY repo_name
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
        )
    return {"relationships": [dict(r) for r in rows], "count": len(rows)}


@router.post("/{repo_name}/index")
async def trigger_index(
    repo_name: str,
    background_tasks: BackgroundTasks,
    org: ActiveOrg = Depends(require_role("member")),
):
    """Queue a repo for re-indexing."""
    await _assert_repo_in_org(repo_name, org.org_id)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO index_requests (org_id, repo_name) VALUES ($1, $2)",
            org.org_id,
            repo_name,
        )
    return {"status": "queued", "repo": repo_name}


@router.post("/index-all")
async def trigger_index_all(org: ActiveOrg = Depends(require_role("member"))):
    """Queue all of the active organization's repos for re-indexing."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO index_requests (org_id, repo_name) VALUES ($1, NULL)", org.org_id
        )
    return {"status": "queued", "message": "All repos queued for indexing"}


@router.get("/{repo_name}/files")
async def list_files(repo_name: str, path: str = "", org: ActiveOrg = Depends(get_active_org)):
    """List files in a repo directory."""
    import os
    from pathlib import Path
    from ...indexer.git_manager import get_repo_local_path

    await _assert_repo_in_org(repo_name, org.org_id)
    cfg = await get_org_config(org.org_id)
    repo_cfg = next((r for r in cfg.repos if r.name == repo_name), None)
    if not repo_cfg:
        raise HTTPException(status_code=404, detail=f"Repo '{repo_name}' not found")

    repo_path = Path(get_repo_local_path(repo_cfg, org.org_id))

    # A repo can be indexed while its working tree is not cached on this server
    # (e.g. the clone lives on ephemeral storage that was cleared, or indexing
    # ran on a different worker). Surface an empty, "unavailable" listing rather
    # than a hard 404 so the repo detail page still renders its analytics.
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
async def repo_stats(repo_name: str, org: ActiveOrg = Depends(get_active_org)):
    """Get repository-level analytics for drill-down view."""
    await _assert_repo_in_org(repo_name, org.org_id)
    pool = await get_pool()
    async with pool.acquire() as conn:
        repo_row = await conn.fetchrow(
            """
            SELECT name, type, url, path, branch, language, status, last_indexed_at, total_chunks, error_message
            FROM repos
            WHERE org_id=$1 AND name=$2
            """,
            org.org_id,
            repo_name,
        )
        if not repo_row:
            raise HTTPException(status_code=404, detail=f"Repo '{repo_name}' not found")

        chunk_types_rows = await conn.fetch(
            """
            SELECT chunk_type, count(*) AS count
            FROM repo_chunks
            WHERE org_id=$1 AND repo_name=$2
            GROUP BY chunk_type
            ORDER BY count DESC
            """,
            org.org_id,
            repo_name,
        )

        ext_rows = await conn.fetch(
            """
            SELECT
                lower(split_part(file_path, '.', array_length(string_to_array(file_path, '.'), 1))) AS extension,
                count(*) AS count
            FROM repo_chunks
            WHERE org_id=$1 AND repo_name=$2 AND position('.' in file_path) > 0
            GROUP BY extension
            ORDER BY count DESC
            LIMIT 8
            """,
            org.org_id,
            repo_name,
        )

    repo_data = dict(repo_row)
    if repo_data.get("last_indexed_at"):
        repo_data["last_indexed_at"] = repo_data["last_indexed_at"].isoformat()

    chunk_types = [dict(r) for r in chunk_types_rows]
    by_extension = [
        {"extension": f".{r['extension']}" if r["extension"] else "(none)", "count": r["count"]}
        for r in ext_rows
    ]

    return {
        "repo": repo_data,
        "chunk_types": chunk_types,
        "by_extension": by_extension,
    }


class CreateRepoRequest(BaseModel):
    name: str
    type: str  # 'local', 'github', 'gitlab'
    url: Optional[str] = None
    path: Optional[str] = None
    branch: str = "main"
    language: Optional[str] = None


@router.post("")
async def create_repo(req: CreateRepoRequest, org: ActiveOrg = Depends(require_role("member"))):
    """Add a new repository to the active organization."""
    from ...config import RepoConfig

    cfg = await get_org_config(org.org_id)

    # Repo names are unique within an organization (but reusable across orgs).
    if any(r.name == req.name for r in cfg.repos):
        raise HTTPException(status_code=400, detail=f"Repository '{req.name}' already exists")

    cfg.repos.append(
        RepoConfig(
            name=req.name,
            type=req.type,
            url=req.url,
            path=req.path,
            branch=req.branch,
            language=req.language or "auto",
        )
    )
    await persist_org_config(org.org_id, cfg)
    await sync_repos_config(org.org_id)

    return {"status": "ok", "repo": {"name": req.name, "type": req.type}}


@router.put("/{repo_name}")
async def update_repo(repo_name: str, req: CreateRepoRequest, org: ActiveOrg = Depends(require_role("member"))):
    """Update an existing repository configuration."""
    from ...config import RepoConfig

    await _assert_repo_in_org(repo_name, org.org_id)
    cfg = await get_org_config(org.org_id)

    repo_idx = next((i for i, r in enumerate(cfg.repos) if r.name == repo_name), None)
    if repo_idx is None:
        raise HTTPException(status_code=404, detail=f"Repository '{repo_name}' not found")

    # Can't rename to another existing repo in the same org
    if req.name != repo_name and any(r.name == req.name for r in cfg.repos):
        raise HTTPException(status_code=400, detail=f"Repository '{req.name}' already exists")

    cfg.repos[repo_idx] = RepoConfig(
        name=req.name,
        type=req.type,
        url=req.url,
        path=req.path,
        branch=req.branch,
        language=req.language or cfg.repos[repo_idx].language,
    )
    await persist_org_config(org.org_id, cfg)

    # If renamed, drop the old repo row (and its chunks) for this org.
    if req.name != repo_name:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM repo_chunks WHERE org_id=$1 AND repo_name=$2", org.org_id, repo_name
            )
            await conn.execute(
                "DELETE FROM repos WHERE org_id=$1 AND name=$2", org.org_id, repo_name
            )
    await sync_repos_config(org.org_id)

    return {"status": "ok", "repo": {"name": req.name, "type": req.type}}


@router.delete("/{repo_name}")
async def delete_repo(repo_name: str, org: ActiveOrg = Depends(require_role("member"))):
    """Remove a repository from the active organization's configuration."""
    from ...indexer.git_manager import get_repo_local_path
    import shutil
    from pathlib import Path

    await _assert_repo_in_org(repo_name, org.org_id)
    cfg = await get_org_config(org.org_id)

    repo_idx = next((i for i, r in enumerate(cfg.repos) if r.name == repo_name), None)
    if repo_idx is None:
        raise HTTPException(status_code=404, detail=f"Repository '{repo_name}' not found")

    repo = cfg.repos[repo_idx]
    cfg.repos.pop(repo_idx)
    await persist_org_config(org.org_id, cfg)

    # Clean up cached repo data for this organization.
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM repo_chunks WHERE org_id=$1 AND repo_name=$2", org.org_id, repo_name
        )
        await conn.execute(
            "DELETE FROM repos WHERE org_id=$1 AND name=$2", org.org_id, repo_name
        )

    # Try to remove the cloned repo directory if it exists.
    if repo.type in ("github", "gitlab") and repo.url:
        cache_dir = Path(get_repo_local_path(repo, org.org_id))
        if cache_dir.exists():
            try:
                shutil.rmtree(cache_dir)
            except Exception:
                pass  # Ignore cleanup errors

    return {"status": "ok", "message": f"Repository '{repo_name}' removed"}
