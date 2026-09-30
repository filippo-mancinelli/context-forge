"""MCP tools for repository search and navigation."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .server import mcp
from .permissions import PermissionDenied, requires_permission
from .context import get_current_user_id
from .project_access import selection_rights
from ..catalog import repos as repo_catalog
from ..catalog import selections
from ..db import get_pool
from ..text_window import slice_lines

logger = logging.getLogger(__name__)


@mcp.tool()
@requires_permission("context-read")
async def repo_list() -> dict:
    """List the repositories of the current project in ContextForge, with indexing status.

    Use this to answer questions like "which repos are indexed?", "how many
    repositories do you see?", "is repo X indexed?", or to check indexing
    progress and errors. This is the authoritative list for this project's MCP endpoint — do not
    infer it from other sources. Repositories come from the organization catalog: when the one you
    need is missing, find it with catalog_list(kind='repos') and add it with resource_select.

    Returns:
        dict with list of repos, each including name, type, status, last_indexed_at, and total_chunks
    """
    from .context import resolve_org_id, require_project_id

    org_id = await resolve_org_id()
    project_id = await require_project_id()
    keys = ("name", "type", "url", "path", "branch", "status", "last_indexed_at", "total_chunks", "error_message")
    repos = [{k: r.get(k) for k in keys} for r in await repo_catalog.list_project_repos(org_id, project_id)]
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
        repo: Repository name (from repo_list)
        path: File path relative to the repo root (e.g. "src/main.py")
        start_line: first line to return, 1-based (default: the start of the file)
        end_line: last line to return, inclusive (default: the end of the file)

    Returns:
        dict with the file `content`, the `start_line`/`end_line` actually
        returned, `total_lines`, and whether the content was `truncated`
    """
    from ..indexer.git_manager import get_repo_local_path
    from .context import resolve_org_id, require_project_id

    org_id = await resolve_org_id()
    project_id = await require_project_id()

    try:
        record = await repo_catalog.resolve_project_repo(org_id, project_id, repo)
    except (repo_catalog.RepoNotFoundError, repo_catalog.RepoNotAvailableError) as e:
        return {"status": "error", "error": str(e)}

    repo_path = get_repo_local_path(record, org_id)
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
    repo_id = None
    if repo:
        try:
            repo_id = (await repo_catalog.resolve_project_repo(org_id, project_id, repo)).id
        except (repo_catalog.RepoNotFoundError, repo_catalog.RepoNotAvailableError) as e:
            return {"status": "error", "error": str(e)}
    await repo_catalog.queue_index(org_id, repo_id, project_id)
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
                SELECT r.name AS repo_name,
                       avg(c.embedding) AS centroid,
                       count(*) AS chunk_count
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
    """Add a repository to the active project, registering it in the organization catalog if needed.

    When the catalog already has the same URL and branch, that repository is
    selected instead of registering a copy, and its existing index is reused.
    Access tokens are never passed here: the server uses the credentials it
    already holds for the git host.

    Args:
        name: repository name, unique within the organization; used only when a new repository is registered.
        type: 'gitlab', 'github' or 'local'.
        url: clone URL, for gitlab/github repositories.
        path: filesystem path, for local repositories.
        branch: branch to index. Defaults to main.
        language: primary language, or None to detect it automatically.
        index: queue indexing when the repository has no index yet. Defaults to True.

    Returns:
        dict with the repository, whether it was newly registered, and whether indexing was queued.
    """
    from .context import require_project_id, resolve_org_id

    org_id = await resolve_org_id()
    project_id = await require_project_id()
    # Registrare equivale a selezionare: valgono gli stessi diritti di resource_select.
    can_select, can_select_restricted = await selection_rights(org_id, project_id)
    if not can_select:
        raise PermissionDenied("Adding a repository to this project requires the member role on this project")
    try:
        repo = await repo_catalog.find_repo(org_id, url, branch) if url else None
        created = repo is None
        if created:
            repo = await repo_catalog.create_repo(org_id, {
                "name": name, "type": type, "url": url, "path": path,
                "branch": branch, "language": language or "auto",
            })
        await selections.select_resource(
            org_id, project_id, "repos", repo["id"], get_current_user_id(), can_select_restricted
        )
    except (repo_catalog.RepoConflictError, ValueError) as exc:
        return {"status": "error", "error": str(exc)}
    except selections.RestrictedResourceError as exc:
        raise PermissionDenied(f"'{exc}' is restricted: only an organization admin can add it to a project")
    except selections.ResourceNotFoundError as exc:
        return {"status": "error", "error": str(exc)}

    queued = index and (created or repo.get("status") != "indexed")
    if queued:
        await repo_catalog.queue_index(org_id, repo["id"], project_id)
    return {
        "status": "ok",
        "repo": {k: repo.get(k) for k in ("name", "type", "url", "path", "branch", "status")},
        "created": created,
        "indexing": queued,
    }
