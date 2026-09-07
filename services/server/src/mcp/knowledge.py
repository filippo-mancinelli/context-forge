"""MCP tools for the knowledge base (uploaded documents)."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

from .server import mcp
from .permissions import requires_permission
from ..db import get_pool
from ..kb import fetch as kb_fetch
from ..kb import store as kb_store

logger = logging.getLogger(__name__)


@mcp.tool()
@requires_permission("context-read")
async def kb_search(
    query: str,
    limit: int = 10,
    document_ids: Optional[list[int]] = None,
) -> dict:
    """Search the knowledge base of uploaded documents of the current project
    using semantic similarity.

    Finds passages from user-uploaded documents (PDFs, Word/Excel/PowerPoint
    files, images processed with OCR, text, and more) relevant to the query,
    scoped to the current project.

    Args:
        query: Natural language search query
        limit: Maximum number of matching passages to return (default 10)
        document_ids: Optional list of document ids to restrict the search to

    Returns:
        dict with a list of results, each with document_id, title, filename,
        content, and a relevance score
    """
    from ..kb import store
    from .context import resolve_org_id, require_project_id

    org_id = await resolve_org_id()
    project_id = await require_project_id()
    try:
        results = await store.search_documents(
            org_id, project_id, query, limit=limit, document_ids=document_ids
        )
    except Exception as e:  # noqa: BLE001
        logger.error("kb_search failed: %s", e)
        return {"status": "error", "error": str(e)}
    return {"status": "ok", "results": results, "count": len(results)}


@mcp.tool()
@requires_permission("context-read")
async def kb_list(limit: int = 50) -> dict:
    """List documents in the knowledge base of the current project and their
    processing status.

    Args:
        limit: Maximum number of documents to return (default 50)

    Returns:
        dict with a list of documents (id, title, filename, status, total_chunks)
    """
    from .context import resolve_org_id, require_project_id

    org_id = await resolve_org_id()
    project_id = await require_project_id()
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, title, filename, extension, status, total_chunks,
                   char_count, error_message, uploaded_at
            FROM kb_documents
            WHERE org_id=$1 AND project_id=$2
            ORDER BY uploaded_at DESC
            LIMIT $3
            """,
            org_id,
            project_id,
            limit,
        )
    docs = []
    for r in rows:
        d = dict(r)
        if d.get("uploaded_at"):
            d["uploaded_at"] = d["uploaded_at"].isoformat()
        docs.append(d)
    return {"status": "ok", "documents": docs, "count": len(docs)}


@mcp.tool()
@requires_permission("context-read")
async def kb_get_document(document_id: int, max_chars: int = 20000) -> dict:
    """Retrieve the full extracted text of a knowledge-base document of the
    current project.

    Reassembles the document from its stored chunks.

    Args:
        document_id: The id of the document (from kb_list or kb_search)
        max_chars: Maximum number of characters to return (default 20000)

    Returns:
        dict with the document's title, filename, and extracted text content
    """
    from .context import resolve_org_id, require_project_id

    org_id = await resolve_org_id()
    project_id = await require_project_id()
    pool = await get_pool()
    async with pool.acquire() as conn:
        doc = await conn.fetchrow(
            "SELECT title, filename, status, total_chunks FROM kb_documents "
            "WHERE id=$1 AND org_id=$2 AND project_id=$3",
            document_id,
            org_id,
            project_id,
        )
        if doc is None:
            return {"status": "error", "error": f"Document {document_id} not found"}
        rows = await conn.fetch(
            "SELECT content FROM kb_chunks WHERE document_id=$1 ORDER BY chunk_index",
            document_id,
        )

    content = "\n\n".join(r["content"] for r in rows)
    truncated = len(content) > max_chars
    if truncated:
        content = content[:max_chars]
    return {
        "status": "ok",
        "document_id": document_id,
        "title": doc["title"],
        "filename": doc["filename"],
        "doc_status": doc["status"],
        "content": content,
        "truncated": truncated,
    }


# Reference ai task di elaborazione in corso: senza, il garbage collector può
# cancellare un task a metà.
_PROCESSING_TASKS: set = set()


def _schedule_processing(document_id: int) -> None:
    """Avvia estrazione ed embedding in background.

    Isolata dal tool perché il task va creato sul loop del server: nei test si
    sostituisce questa funzione invece di inseguire un task orfano.
    """
    task = asyncio.create_task(kb_store.process_document(document_id))
    _PROCESSING_TASKS.add(task)
    task.add_done_callback(_PROCESSING_TASKS.discard)


@mcp.tool()
@requires_permission("sources-write")
async def kb_add_url(url: str, title: Optional[str] = None) -> dict:
    """Add a document to the knowledge base by downloading it from a URL.

    The server fetches the file and runs it through the usual pipeline
    (extraction, chunking, embedding). The document starts as 'pending' and
    becomes 'ready': check kb_list to follow its progress. Only public http and
    https URLs are accepted.

    Args:
        url: direct link to the document (PDF, Office, Markdown, text, ...).
        title: optional title. Defaults to the downloaded file name.

    Returns:
        dict with the created document record.
    """
    from .context import require_project_id, resolve_org_id

    org_id = await resolve_org_id()
    project_id = await require_project_id()
    try:
        filename, data = await kb_fetch.fetch_document(url)
    except kb_fetch.FetchError as exc:
        return {"status": "error", "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — errori di rete del client HTTP
        return {"status": "error", "error": f"Could not fetch '{url}': {exc}"}

    if title:
        # Il titolo scelto sostituisce il nome, ma l'estensione decide il parser.
        filename = f"{title}{Path(filename).suffix}"

    record = await kb_store.save_upload(org_id, project_id, filename, data)
    # Stessa scelta della route REST: elaborazione subito, scheduler come rete
    # di sicurezza se il task muore.
    _schedule_processing(record["id"])
    return {"status": "ok", "document": record}
