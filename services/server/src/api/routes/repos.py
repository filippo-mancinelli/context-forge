"""REST API routes for repository management."""
from __future__ import annotations

import json
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


class RepoSearchRequest(BaseModel):
    query: str
    repos: Optional[list[str]] = None
    limit: int = 20


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


@router.post("/search")
async def search_repos(req: RepoSearchRequest):
    """Search indexed repository chunks by semantic similarity."""
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
                WHERE repo_name = ANY($2)
                ORDER BY embedding <=> $1::vector
                LIMIT $3
                """,
                embedding_str,
                req.repos,
                req.limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT repo_name, file_path, chunk_type, content, metadata,
                       1 - (embedding <=> $1::vector) AS score
                FROM repo_chunks
                ORDER BY embedding <=> $1::vector
                LIMIT $2
                """,
                embedding_str,
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
async def list_relationships(repo: Optional[str] = None):
    """Get semantic relationships between repositories."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH centroids AS (
                SELECT repo_name, avg(embedding) AS centroid, count(*) AS chunk_count
                FROM repo_chunks
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
        )
    return {"relationships": [dict(r) for r in rows], "count": len(rows)}


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


@router.get("/{repo_name}/stats")
async def repo_stats(repo_name: str):
    """Get repository-level analytics for drill-down view."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        repo_row = await conn.fetchrow(
            """
            SELECT name, type, url, path, branch, language, status, last_indexed_at, total_chunks, error_message
            FROM repos
            WHERE name=$1
            """,
            repo_name,
        )
        if not repo_row:
            raise HTTPException(status_code=404, detail=f"Repo '{repo_name}' not found")

        chunk_types_rows = await conn.fetch(
            """
            SELECT chunk_type, count(*) AS count
            FROM repo_chunks
            WHERE repo_name=$1
            GROUP BY chunk_type
            ORDER BY count DESC
            """,
            repo_name,
        )

        ext_rows = await conn.fetch(
            """
            SELECT
                lower(split_part(file_path, '.', array_length(string_to_array(file_path, '.'), 1))) AS extension,
                count(*) AS count
            FROM repo_chunks
            WHERE repo_name=$1 AND position('.' in file_path) > 0
            GROUP BY extension
            ORDER BY count DESC
            LIMIT 8
            """,
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
