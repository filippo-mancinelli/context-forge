"""MCP tools for repository search and navigation."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from .server import mcp
from ..db import get_pool

logger = logging.getLogger(__name__)


@mcp.tool()
async def repo_list() -> dict:
    """List all configured repositories and their indexing status.

    Returns:
        dict with list of repos, each including name, type, status, last_indexed_at, and total_chunks
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT name, type, url, path, branch, status, last_indexed_at, total_chunks, error_message "
            "FROM repos ORDER BY name"
        )
    repos = [dict(r) for r in rows]
    # Convert datetime to ISO string
    for r in repos:
        if r.get("last_indexed_at"):
            r["last_indexed_at"] = r["last_indexed_at"].isoformat()
    return {"status": "ok", "repos": repos, "count": len(repos)}


@mcp.tool()
async def repo_search(
    query: str,
    repos: Optional[list[str]] = None,
    limit: int = 10,
) -> dict:
    """Search across indexed repositories using semantic similarity.

    Finds code, functions, classes, and documentation relevant to the query.
    Works across all indexed repos or a subset.

    Args:
        query: Natural language or code search query
        repos: Optional list of repo names to search in (default: all repos)
        limit: Maximum number of results (default 10)

    Returns:
        dict with list of results, each with repo_name, file_path, content, chunk_type, score
    """
    from ..indexer.embedder import embed_text

    try:
        embedding = await embed_text(query)
    except Exception as e:
        return {"status": "error", "error": f"Embedding failed: {e}"}

    pool = await get_pool()
    async with pool.acquire() as conn:
        if repos:
            rows = await conn.fetch(
                """
                SELECT repo_name, file_path, chunk_type, content, metadata,
                       1 - (embedding <=> $1::vector) AS score
                FROM repo_chunks
                WHERE repo_name = ANY($2)
                ORDER BY embedding <=> $1::vector
                LIMIT $3
                """,
                embedding,
                repos,
                limit,
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
                embedding,
                limit,
            )

    results = []
    for r in rows:
        meta = r["metadata"]
        if isinstance(meta, str):
            meta = json.loads(meta)
        results.append({
            "repo_name": r["repo_name"],
            "file_path": r["file_path"],
            "chunk_type": r["chunk_type"],
            "content": r["content"],
            "metadata": meta,
            "score": round(float(r["score"]), 4),
        })
    return {"status": "ok", "results": results, "count": len(results)}


@mcp.tool()
async def repo_get_file(repo: str, path: str) -> dict:
    """Read the full content of a file from an indexed repository.

    Args:
        repo: Repository name (as configured in context-forge.yml)
        path: File path relative to the repo root (e.g. "src/main.py")

    Returns:
        dict with file content and metadata, or error if not found
    """
    from ..config import get_forge_config
    from ..indexer.git_manager import get_repo_local_path

    cfg = get_forge_config()
    repo_cfg = next((r for r in cfg.repos if r.name == repo), None)
    if not repo_cfg:
        return {"status": "error", "error": f"Repository '{repo}' not found in config"}

    repo_path = get_repo_local_path(repo_cfg)
    file_path = Path(repo_path) / path.lstrip("/")

    if not file_path.exists():
        return {"status": "error", "error": f"File not found: {path}"}

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        return {
            "status": "ok",
            "repo": repo,
            "path": path,
            "content": content,
            "size_bytes": file_path.stat().st_size,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
async def repo_index(repo: Optional[str] = None) -> dict:
    """Trigger re-indexing of one or all repositories.

    Queues an indexing job that runs in the background.
    Use repo_list() to check indexing status.

    Args:
        repo: Repository name to index, or None to index all repos

    Returns:
        dict with status and list of repos queued for indexing
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO index_requests (repo_name) VALUES ($1)",
            repo,
        )
    return {
        "status": "ok",
        "message": f"Indexing queued for: {repo or 'all repos'}",
        "repo": repo,
    }


@mcp.tool()
async def repo_relationships(repo: Optional[str] = None) -> dict:
    """Discover semantic relationships between repositories or modules.

    Finds repos/files with overlapping concepts by comparing embedding centroids.

    Args:
        repo: Repository name to find relationships for, or None for all-pairs

    Returns:
        dict with list of related repo pairs and their similarity scores
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH centroids AS (
                SELECT repo_name,
                       avg(embedding) AS centroid,
                       count(*) AS chunk_count
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
            LIMIT 20
            """,
            repo,
        )

    relationships = [dict(r) for r in rows]
    return {
        "status": "ok",
        "relationships": relationships,
        "count": len(relationships),
    }
