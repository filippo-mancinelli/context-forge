"""Tool MCP sul grafo dei simboli: mappa del repository e vicinato di un simbolo.

Rispondono alle due domande che la ricerca semantica non copre — «da dove parto»
e «chi usa questa cosa» — leggendo il grafo costruito in indicizzazione invece di
far riesplorare il repository un file alla volta.
"""
from __future__ import annotations

import logging
from typing import Optional

from .server import mcp
from .permissions import requires_permission
from .context import require_project_id, resolve_org_id
from ..indexer import symbols as symbols_mod

logger = logging.getLogger(__name__)

# Un nome definito in decine di file (un `Config`, un `handler`) collegherebbe
# tutto con tutto senza dire nulla: si esclude dagli archi.
MAX_DEFINING_FILES = 20
MAX_EDGES = 20_000


async def _edges_and_defs(project_id: int, repos: Optional[list[str]]):
    from ..db import get_pool

    pool = await get_pool()
    repo_filter = "AND r.repo_name = ANY($2::text[])" if repos else ""
    async with pool.acquire() as conn:
        args = [project_id] + ([repos] if repos else [])
        edge_rows = await conn.fetch(
            f"""
            WITH useful AS (
                SELECT name
                FROM repo_symbols
                WHERE project_id = $1 AND kind = 'def'
                GROUP BY name
                HAVING COUNT(DISTINCT file_path) <= {MAX_DEFINING_FILES}
            )
            SELECT r.file_path AS src_file, d.file_path AS dst_file,
                   SUM(r.occurrences) AS weight
            FROM repo_symbols r
            JOIN useful u ON u.name = r.name
            JOIN repo_symbols d
              ON d.project_id = r.project_id AND d.name = r.name AND d.kind = 'def'
            WHERE r.project_id = $1 AND r.kind = 'ref' {repo_filter}
            GROUP BY 1, 2
            LIMIT {MAX_EDGES}
            """,
            *args,
        )
        def_rows = await conn.fetch(
            f"""
            SELECT file_path, name, node_type AS kind, repo_name
            FROM repo_symbols r
            WHERE project_id = $1 AND kind = 'def' {repo_filter.replace('r.repo_name', 'repo_name')}
            """,
            *args,
        )
    return [dict(r) for r in edge_rows], [dict(r) for r in def_rows]


@mcp.tool()
@requires_permission("context-read")
async def repo_map(
    query: Optional[str] = None,
    repos: Optional[list[str]] = None,
    limit: int = 25,
) -> dict:
    """Rank the files of the active project's repos, most relevant first.

    Answers "where do I start" without reading the repository: the project's
    indexed symbols form a graph where a file points at the files defining the
    names it uses, and the ranking is a PageRank over it. With a query, the
    ranking restarts from the files defining (or named after) the terms you
    pass, so it is the relevance for *that* question, not a fixed popularity
    chart. Each entry lists what the file defines, so you can go straight to
    repo_get_file or repo_symbols instead of searching.

    Use it as the first call on an unfamiliar codebase, before repo_search.

    Args:
        query: what you are looking for; identifiers work best ("availableSlots",
            "AssignmentScheduler"), plain questions are accepted.
        repos: restrict to these repo names (default: every repo of the project).
        limit: how many files to return (default 25).

    Returns:
        dict with `files`: file_path, score, and the symbols each file defines.
    """
    try:
        org_id = await resolve_org_id()
        project_id = await require_project_id()
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}
    if org_id is None:
        return {"status": "error", "error": "No active organization"}

    try:
        edge_rows, def_rows = await _edges_and_defs(project_id, repos)
    except Exception as e:  # noqa: BLE001
        logger.error("repo_map failed: %s", e)
        return {"status": "error", "error": str(e)}

    if not edge_rows and not def_rows:
        return {
            "status": "ok",
            "files": [],
            "note": "No symbol graph for this project yet: re-index the repos to build it.",
        }

    repo_by_file = {r["file_path"]: r.get("repo_name") for r in def_rows}
    ranked = symbols_mod.rank_files(edge_rows, def_rows, query=query, limit=limit)
    for entry in ranked:
        entry["repo_name"] = repo_by_file.get(entry["file_path"])
    return {"status": "ok", "query": query, "count": len(ranked), "files": ranked}


@mcp.tool()
@requires_permission("context-read")
async def repo_neighbors(
    symbol: str,
    repos: Optional[list[str]] = None,
    limit: int = 30,
) -> dict:
    """Show where a symbol is defined and which files use it, plus its context.

    This is the "who calls this" question, answered from the graph rather than
    by grepping: you get the defining file(s), the files that reference the
    symbol with how many times, and the other symbols defined next to it — the
    one-hop neighbourhood. Cheaper and more complete than a semantic search when
    you already have a name.

    Args:
        symbol: exact symbol name (case-insensitive), e.g. "availableSlots".
        repos: restrict to these repo names (default: every repo of the project).
        limit: max entries per section (default 30).

    Returns:
        dict with `defined_in`, `referenced_by`, and `siblings`.
    """
    try:
        org_id = await resolve_org_id()
        project_id = await require_project_id()
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}
    if org_id is None:
        return {"status": "error", "error": "No active organization"}
    if not symbol or not symbol.strip():
        return {"status": "error", "error": "symbol must not be empty"}

    from ..db import get_pool

    name = symbol.strip()
    pool = await get_pool()
    repo_filter = "AND repo_name = ANY($3::text[])" if repos else ""
    args = [project_id, name] + ([repos] if repos else [])
    try:
        async with pool.acquire() as conn:
            defined = await conn.fetch(
                f"""
                SELECT repo_name, file_path, node_type, line
                FROM repo_symbols
                WHERE project_id=$1 AND kind='def' AND lower(name)=lower($2) {repo_filter}
                ORDER BY repo_name, file_path
                LIMIT {int(limit)}
                """,
                *args,
            )
            referenced = await conn.fetch(
                f"""
                SELECT repo_name, file_path, occurrences, line
                FROM repo_symbols
                WHERE project_id=$1 AND kind='ref' AND lower(name)=lower($2) {repo_filter}
                ORDER BY occurrences DESC, file_path
                LIMIT {int(limit)}
                """,
                *args,
            )
            siblings = []
            if defined:
                files = [r["file_path"] for r in defined]
                siblings = await conn.fetch(
                    f"""
                    SELECT file_path, name, node_type
                    FROM repo_symbols
                    WHERE project_id=$1 AND kind='def' AND file_path = ANY($2::text[])
                      AND lower(name) <> lower($3)
                    ORDER BY file_path, name
                    LIMIT {int(limit)}
                    """,
                    project_id, files, name,
                )
    except Exception as e:  # noqa: BLE001
        logger.error("repo_neighbors failed: %s", e)
        return {"status": "error", "error": str(e)}

    return {
        "status": "ok",
        "symbol": name,
        "defined_in": [dict(r) for r in defined],
        "referenced_by": [dict(r) for r in referenced],
        "siblings": [dict(r) for r in siblings],
    }
