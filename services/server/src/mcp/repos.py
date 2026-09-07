"""MCP tools for repository search and navigation."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .server import mcp
from .permissions import requires_permission
from ..db import get_pool
from .. import repo_registry
from ..text_window import slice_lines

logger = logging.getLogger(__name__)


@mcp.tool()
@requires_permission("context-read")
async def repo_list() -> dict:
    """List the repositories of the current project in ContextForge, with indexing status.

    Use this to answer questions like "which repos are indexed?", "how many
    repositories do you see?", "is repo X indexed?", or to check indexing
    progress and errors. This is the authoritative list for this project's MCP endpoint — do not
    infer it from other sources.

    Returns:
        dict with list of repos, each including name, type, status, last_indexed_at, and total_chunks
    """
    from .context import resolve_org_id, require_project_id

    org_id = await resolve_org_id()
    project_id = await require_project_id()
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT name, type, url, path, branch, status, last_indexed_at, total_chunks, error_message "
            "FROM repos WHERE org_id=$1 AND project_id=$2 ORDER BY name",
            org_id,
            project_id,
        )
    repos = [dict(r) for r in rows]
    # Convert datetime to ISO string
    for r in repos:
        if r.get("last_indexed_at"):
            r["last_indexed_at"] = r["last_indexed_at"].isoformat()
    return {"status": "ok", "repos": repos, "count": len(repos)}


@mcp.tool()
@requires_permission("context-read")
async def repo_search(
    query: str,
    repos: Optional[list[str]] = None,
    limit: int = 10,
) -> dict:
    """Search across the current project's indexed repositories using semantic similarity.

    Finds code, functions, classes, and documentation relevant to the query.
    Works across all indexed repos or a subset. Prefer this over local file
    search when the answer may live in a repo that is not checked out locally,
    or when you need to search across the project's codebase.

    Args:
        query: Natural language or code search query
        repos: Optional list of repo names to search in (default: all repos)
        limit: Maximum number of results (default 10)

    Returns:
        dict with list of results, each with repo_name, file_path, content, chunk_type, score
    """
    from ..search import search_repo_chunks
    from .context import resolve_org_id, require_project_id

    org_id = await resolve_org_id()
    project_id = await require_project_id()

    try:
        results = await search_repo_chunks(org_id, query, repos=repos, limit=limit, project_id=project_id)
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": f"Search failed: {e}"}

    return {"status": "ok", "results": results, "count": len(results)}


@mcp.tool()
@requires_permission("context-read")
async def repo_symbols(
    query: str,
    repos: Optional[list[str]] = None,
    limit: int = 20,
) -> dict:
    """Look up function/class/method/type definitions by name across indexed repos.

    Use this instead of repo_search when you already know (or can guess) the
    identifier you're after — e.g. "find the definition of resolve_org_id" or
    "where is the Memory class". It's a lexical name lookup over parsed
    definitions, not semantic similarity, so it's precise for "go to
    definition"-style questions and won't return loosely related code the way
    repo_search can. Only covers tree-sitter-parseable languages (Python, JS/TS,
    Go, Java); other file types aren't indexed as symbols.

    Args:
        query: Symbol name or substring (e.g. "resolve_org_id", "Memory")
        repos: Optional list of repo names to search in (default: all repos)
        limit: Maximum number of results (default 20)

    Returns:
        dict with list of results, each with repo_name, file_path, chunk_type,
        name, a one-line signature preview, and start_line
    """
    from ..search import search_repo_symbols
    from .context import resolve_org_id, require_project_id

    org_id = await resolve_org_id()
    project_id = await require_project_id()

    try:
        results = await search_repo_symbols(org_id, query, repos=repos, limit=limit, project_id=project_id)
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": f"Symbol search failed: {e}"}

    return {"status": "ok", "results": results, "count": len(results)}


@mcp.tool()
@requires_permission("context-read")
async def repo_get_file(
    repo: str,
    path: str,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
) -> dict:
    """Read a file from an indexed repository, whole or by line range.

    Without a range this returns the entire file, which on a large class is
    tens of thousands of tokens. When you already know where to look — the line
    from repo_neighbors, the chunk from repo_search — pass the range and read a
    window around it instead: same answer, a fraction of the context.

    Args:
        repo: Repository name (as configured in runtime settings)
        path: File path relative to the repo root (e.g. "src/main.py")
        start_line: first line to return, 1-based (default: the start of the file)
        end_line: last line to return, inclusive (default: the end of the file)

    Returns:
        dict with the file `content`, the `start_line`/`end_line` actually
        returned, `total_lines`, and whether the content was `truncated`
    """
    from ..indexer.git_manager import get_repo_local_path
    from ..org_config import get_org_config
    from .context import resolve_org_id, require_project_id

    org_id = await resolve_org_id()
    project_id = await require_project_id()

    pool = await get_pool()
    async with pool.acquire() as conn:
        in_project = await conn.fetchval(
            "SELECT 1 FROM repos WHERE org_id=$1 AND project_id=$2 AND name=$3",
            org_id, project_id, repo,
        )
    if not in_project:
        return {"status": "error", "error": f"Repository '{repo}' not found"}

    cfg = await get_org_config(org_id)
    repo_cfg = next((r for r in cfg.repos if r.name == repo), None)
    if not repo_cfg:
        return {"status": "error", "error": f"Repository '{repo}' not found in runtime settings"}

    repo_path = get_repo_local_path(repo_cfg, org_id)
    file_path = Path(repo_path) / path.lstrip("/")

    if not file_path.exists():
        return {"status": "error", "error": f"File not found: {path}"}

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        window = slice_lines(content, start_line, end_line)
        return {
            "status": "ok",
            "repo": repo,
            "path": path,
            "content": window["content"],
            "start_line": window["start_line"],
            "end_line": window["end_line"],
            "total_lines": window["total_lines"],
            "truncated": window["truncated"],
            "size_bytes": file_path.stat().st_size,
        }
    except ValueError as e:
        return {"status": "error", "error": str(e)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
@requires_permission("admin")
async def repo_index(repo: Optional[str] = None) -> dict:
    """Trigger re-indexing of one or all repositories.

    Queues an indexing job that runs in the background.
    Use repo_list() to check indexing status.

    Args:
        repo: Repository name to index, or None to index all repos

    Returns:
        dict with status and list of repos queued for indexing
    """
    from .context import resolve_org_id, require_project_id

    org_id = await resolve_org_id()
    project_id = await require_project_id()
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO index_requests (org_id, project_id, repo_name) VALUES ($1, $2, $3)",
            org_id,
            project_id,
            repo,
        )
    return {
        "status": "ok",
        "message": f"Indexing queued for: {repo or 'all repos'}",
        "repo": repo,
    }


@mcp.tool()
@requires_permission("context-read")
async def repo_relationships(repo: Optional[str] = None) -> dict:
    """Discover semantic relationships between repositories or modules.

    Finds repos/files with overlapping concepts by comparing embedding centroids.

    Args:
        repo: Repository name to find relationships for, or None for all-pairs

    Returns:
        dict with list of related repo pairs and their similarity scores
    """
    from .context import resolve_org_id, require_project_id

    org_id = await resolve_org_id()
    project_id = await require_project_id()
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH centroids AS (
                SELECT repo_name,
                       avg(embedding) AS centroid,
                       count(*) AS chunk_count
                FROM repo_chunks
                WHERE org_id = $2 AND project_id = $3
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
            org_id,
            project_id,
        )

    relationships = [dict(r) for r in rows]
    return {
        "status": "ok",
        "relationships": relationships,
        "count": len(relationships),
    }


@mcp.tool()
@requires_permission("sources-write")
async def repo_add(
    name: str,
    type: str,
    url: Optional[str] = None,
    path: Optional[str] = None,
    branch: str = "main",
    language: Optional[str] = None,
    index: bool = True,
) -> dict:
    """Add a repository to the active project and start indexing it.

    Access tokens are never passed here: the server uses the credentials it
    already holds for the git host.

    Args:
        name: unique repository name within the organization.
        type: 'gitlab', 'github' or 'local'.
        url: clone URL, for gitlab/github repositories.
        path: filesystem path, for local repositories.
        branch: branch to index. Defaults to main.
        language: primary language, or None to detect it automatically.
        index: queue indexing right away. Defaults to True.

    Returns:
        dict with the registered repository and whether indexing was queued.
    """
    from .context import require_project_id, resolve_org_id

    org_id = await resolve_org_id()
    project_id = await require_project_id()
    try:
        repo = await repo_registry.add_repo(
            org_id=org_id,
            project_id=project_id,
            name=name,
            type=type,
            url=url,
            path=path,
            branch=branch,
            language=language,
        )
    except repo_registry.RepoAlreadyExistsError as exc:
        return {"status": "error", "error": str(exc)}
    except ValueError as exc:
        return {"status": "error", "error": str(exc)}

    if index:
        await repo_registry.queue_index(org_id, project_id, name)
    return {"status": "ok", "repo": repo, "indexing": index}
