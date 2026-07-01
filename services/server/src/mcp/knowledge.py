"""MCP tools for the knowledge base (uploaded documents)."""
from __future__ import annotations

import json
import logging
from typing import Optional

from .server import mcp
from ..db import get_pool

logger = logging.getLogger(__name__)


@mcp.tool()
async def kb_search(
    query: str,
    limit: int = 10,
    document_ids: Optional[list[int]] = None,
) -> dict:
    """Search the knowledge base of uploaded documents using semantic similarity.

    Finds passages from user-uploaded documents (PDFs, Word/Excel/PowerPoint
    files, images processed with OCR, text, and more) relevant to the query.

    Args:
        query: Natural language search query
        limit: Maximum number of matching passages to return (default 10)
        document_ids: Optional list of document ids to restrict the search to

    Returns:
        dict with a list of results, each with document_id, title, filename,
        content, and a relevance score
    """
    from ..kb import store
    from .context import resolve_org_id

    org_id = await resolve_org_id()
    try:
        results = await store.search_documents(
            org_id, query, limit=limit, document_ids=document_ids
        )
    except Exception as e:  # noqa: BLE001
        logger.error("kb_search failed: %s", e)
        return {"status": "error", "error": str(e)}
    return {"status": "ok", "results": results, "count": len(results)}


@mcp.tool()
async def kb_list(limit: int = 50) -> dict:
    """List documents in the knowledge base and their processing status.

    Args:
        limit: Maximum number of documents to return (default 50)

    Returns:
        dict with a list of documents (id, title, filename, status, total_chunks)
    """
    from .context import resolve_org_id

    org_id = await resolve_org_id()
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, title, filename, extension, status, total_chunks,
                   char_count, error_message, uploaded_at
            FROM kb_documents
            WHERE org_id=$1
            ORDER BY uploaded_at DESC
            LIMIT $2
            """,
            org_id,
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
async def kb_get_document(document_id: int, max_chars: int = 20000) -> dict:
    """Retrieve the full extracted text of a knowledge-base document.

    Reassembles the document from its stored chunks.

    Args:
        document_id: The id of the document (from kb_list or kb_search)
        max_chars: Maximum number of characters to return (default 20000)

    Returns:
        dict with the document's title, filename, and extracted text content
    """
    from .context import resolve_org_id

    org_id = await resolve_org_id()
    pool = await get_pool()
    async with pool.acquire() as conn:
        doc = await conn.fetchrow(
            "SELECT title, filename, status, total_chunks FROM kb_documents "
            "WHERE id=$1 AND org_id=$2",
            document_id,
            org_id,
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
