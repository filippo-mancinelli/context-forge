"""MCP tools for code intelligence: references, explanation, annotations."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from .server import mcp
from ..db import get_pool

logger = logging.getLogger(__name__)


@mcp.tool()
async def repo_references(
    symbol: str,
    repo: Optional[str] = None,
    limit: int = 20,
) -> dict:
    """Find all references to a symbol (function, class, variable) across indexed repos.

    Searches chunk content and metadata for the given symbol name. Returns where
    the symbol is defined and where it is called/imported/referenced.

    Args:
        symbol: The symbol name to find references for (e.g. "authenticate", "UserModel")
        repo: Optional repo name to scope the search (default: all repos)
        limit: Maximum results (default 20)

    Returns:
        dict with list of references, each with repo_name, file_path, chunk_type,
        content, and score
    """
    from ..search import search_repo_chunks
    from .context import resolve_org_id

    org_id = await resolve_org_id()
    repos_list = [repo] if repo else None

    # Search with the symbol as both the semantic query and text query
    try:
        results = await search_repo_chunks(org_id, symbol, repos=repos_list, limit=limit)
    except Exception as e:
        return {"status": "error", "error": f"Search failed: {e}"}

    # Enrich results with line numbers from metadata
    enriched = []
    for r in results:
        meta = r.get("metadata") or {}
        item = {
            "repo_name": r["repo_name"],
            "file_path": r["file_path"],
            "chunk_type": r["chunk_type"],
            "content": r["content"],
            "score": r["score"],
        }
        if isinstance(meta, dict):
            item["name"] = meta.get("name")
            item["start_line"] = meta.get("start_line")
            item["end_line"] = meta.get("end_line")
        enriched.append(item)

    return {
        "status": "ok",
        "symbol": symbol,
        "references": enriched,
        "count": len(enriched),
    }


@mcp.tool()
async def code_explain(
    repo: str,
    file_path: str,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
) -> dict:
    """Explain what a piece of code does using the configured LLM.

    Reads code from an indexed repository and sends it to the language model
    for a concise explanation. Useful for understanding unfamiliar code,
    reviewing pull requests, or onboarding to a new codebase.

    Args:
        repo: Repository name (as configured)
        file_path: Path to the file relative to repo root
        start_line: Optional 1-based start line (inclusive). If omitted, explains the whole file.
        end_line: Optional 1-based end line (inclusive). If omitted with start_line, explains a single line.

    Returns:
        dict with the code snippet and its explanation
    """
    from ..indexer.git_manager import get_repo_local_path
    from ..org_config import get_org_config
    from .context import resolve_org_id

    org_id = await resolve_org_id()

    # Read the file
    cfg = await get_org_config(org_id)
    repo_cfg = next((r for r in cfg.repos if r.name == repo), None)
    if not repo_cfg:
        return {"status": "error", "error": f"Repository '{repo}' not found"}

    repo_path = get_repo_local_path(repo_cfg, org_id)
    full_path = Path(repo_path) / file_path.lstrip("/")

    if not full_path.exists():
        return {"status": "error", "error": f"File not found: {file_path}"}

    try:
        all_lines = full_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as e:
        return {"status": "error", "error": str(e)}

    # Slice the requested lines
    if start_line is not None:
        sl = max(1, start_line) - 1
        el = min(len(all_lines), end_line) if end_line else sl + 1
        code = "\n".join(all_lines[sl:el])
        line_info = f"lines {start_line}-{el}"
    else:
        code = "\n".join(all_lines)
        line_info = f"full file ({len(all_lines)} lines)"

    # Get file extension for language hint
    ext = full_path.suffix.lstrip(".") if full_path.suffix else "text"

    # Call the LLM
    explanation = await _explain_with_llm(code, ext)

    return {
        "status": "ok",
        "repo": repo,
        "file": file_path,
        "lines": line_info,
        "code": code,
        "explanation": explanation,
    }


@mcp.tool()
async def repo_annotate(
    repo: str,
    file_path: str,
    note: str,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
) -> dict:
    """Add a persistent annotation/note on a file or code chunk.

    Annotations are visible to all members of the organization and persist
    across re-indexing. Use this to document design decisions, flag technical
    debt, or leave review notes that agents and teammates can discover.

    Args:
        repo: Repository name
        file_path: File path relative to repo root
        note: The annotation text (Markdown supported)
        start_line: Optional start line to scope the annotation
        end_line: Optional end line to scope the annotation

    Returns:
        dict with the created annotation
    """
    from .context import resolve_org_id

    org_id = await resolve_org_id()
    pool = await get_pool()

    async with pool.acquire() as conn:
        # Verify repo exists in this org
        exists = await conn.fetchval(
            "SELECT 1 FROM repos WHERE org_id=$1 AND name=$2", org_id, repo
        )
        if not exists:
            return {"status": "error", "error": f"Repository '{repo}' not found"}

        row = await conn.fetchrow(
            """INSERT INTO chunk_annotations (org_id, repo_name, file_path, start_line, end_line, note)
               VALUES ($1, $2, $3, $4, $5, $6)
               RETURNING id, created_at""",
            org_id, repo, file_path, start_line, end_line, note,
        )

    return {
        "status": "ok",
        "annotation": {
            "id": row["id"],
            "repo": repo,
            "file_path": file_path,
            "start_line": start_line,
            "end_line": end_line,
            "note": note,
            "created_at": row["created_at"].isoformat(),
        },
    }


@mcp.tool()
async def repo_annotations(
    repo: str,
    file_path: Optional[str] = None,
    limit: int = 30,
) -> dict:
    """List annotations for a repository, optionally filtered by file.

    Args:
        repo: Repository name
        file_path: Optional file path to filter annotations
        limit: Maximum annotations to return (default 30)

    Returns:
        dict with list of annotations
    """
    from .context import resolve_org_id

    org_id = await resolve_org_id()
    pool = await get_pool()

    query = """SELECT id, repo_name, file_path, start_line, end_line, note, created_at
               FROM chunk_annotations
               WHERE org_id=$1 AND repo_name=$2"""
    params: list = [org_id, repo]

    if file_path:
        query += " AND file_path=$3"
        params.append(file_path)
        query += " ORDER BY start_line NULLS LAST, created_at DESC LIMIT $4"
        params.append(limit)
    else:
        query += " ORDER BY created_at DESC LIMIT $3"
        params.append(limit)

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)

    annotations = [
        {
            "id": r["id"],
            "repo": r["repo_name"],
            "file_path": r["file_path"],
            "start_line": r["start_line"],
            "end_line": r["end_line"],
            "note": r["note"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]
    return {"status": "ok", "annotations": annotations, "count": len(annotations)}


async def _explain_with_llm(code: str, language: str) -> str:
    """Send code to the configured LLM for explanation."""
    from ..config import get_settings

    settings = get_settings()
    provider = settings.llm_provider or "openai"

    prompt = (
        f"Explain the following {language} code concisely. "
        f"Focus on what it does, its purpose, and any notable patterns or edge cases. "
        f"Keep the explanation under 3 paragraphs.\n\n```{language}\n{code}\n```"
    )

    try:
        if provider == "openai":
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.openai_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": settings.llm_model or "gpt-4o-mini",
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 400,
                        "temperature": 0.3,
                    },
                    timeout=30.0,
                )
            if resp.status_code == 200:
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
            return f"LLM error: {resp.status_code}"

        elif provider == "anthropic":
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": settings.anthropic_api_key,
                        "anthropic-version": "2023-06-01",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": settings.llm_model or "claude-3-haiku-20240307",
                        "max_tokens": 400,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                    timeout=30.0,
                )
            if resp.status_code == 200:
                data = resp.json()
                return data["content"][0]["text"].strip()
            return f"LLM error: {resp.status_code}"

        elif provider == "deepseek":
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://api.deepseek.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.deepseek_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": settings.llm_model or "deepseek-chat",
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 400,
                        "temperature": 0.3,
                    },
                    timeout=30.0,
                )
            if resp.status_code == 200:
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
            return f"LLM error: {resp.status_code}"

        return f"Unsupported provider: {provider}"
    except Exception as e:
        logger.error("code_explain LLM call failed: %s", e)
        return f"Explanation unavailable: {e}"
