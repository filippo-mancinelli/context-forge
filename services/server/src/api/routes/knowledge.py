"""REST API routes for the knowledge base (document upload & search)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    UploadFile,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ...db import get_pool
from ...kb import store
from ...kb.extract import SUPPORTED_EXTENSIONS, is_supported
from ..deps import ActiveOrg, get_active_org, require_role

router = APIRouter(prefix="/kb", tags=["knowledge-base"])

# Reject absurdly large uploads outright (per file). 100 MB default.
MAX_UPLOAD_BYTES = 100 * 1024 * 1024


class KbDocumentOut(BaseModel):
    id: int
    title: str
    filename: str
    content_type: Optional[str] = None
    extension: Optional[str] = None
    size_bytes: int = 0
    status: str
    total_chunks: int = 0
    char_count: int = 0
    error_message: Optional[str] = None
    metadata: dict = {}
    uploaded_at: Optional[str] = None
    processed_at: Optional[str] = None


def _row_to_out(row) -> KbDocumentOut:
    d = dict(row)
    meta = d.get("metadata")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    d["metadata"] = meta or {}
    for key in ("uploaded_at", "processed_at"):
        if d.get(key):
            d[key] = d[key].isoformat()
    d.pop("stored_path", None)
    d.pop("org_id", None)
    d.pop("sha256", None)
    return KbDocumentOut(**d)


_DOC_COLUMNS = (
    "id, title, filename, content_type, extension, size_bytes, status, "
    "total_chunks, char_count, error_message, metadata, uploaded_at, processed_at"
)


@router.get("/formats")
async def supported_formats(_: ActiveOrg = Depends(get_active_org)):
    """List the file extensions the knowledge base can ingest."""
    return {"extensions": sorted(SUPPORTED_EXTENSIONS)}


@router.get("/documents", response_model=list[KbDocumentOut])
async def list_documents(org: ActiveOrg = Depends(get_active_org)):
    """List all knowledge-base documents for the active organization."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {_DOC_COLUMNS} FROM kb_documents WHERE org_id=$1 ORDER BY uploaded_at DESC",
            org.org_id,
        )
    return [_row_to_out(r) for r in rows]


@router.get("/documents/{doc_id}", response_model=KbDocumentOut)
async def get_document(doc_id: int, org: ActiveOrg = Depends(get_active_org)):
    """Get a single knowledge-base document's metadata and status."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_DOC_COLUMNS} FROM kb_documents WHERE id=$1 AND org_id=$2",
            doc_id,
            org.org_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return _row_to_out(row)


@router.get("/documents/{doc_id}/chunks")
async def get_document_chunks(
    doc_id: int, limit: int = 50, org: ActiveOrg = Depends(get_active_org)
):
    """Return a document's extracted text chunks (for previewing content)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM kb_documents WHERE id=$1 AND org_id=$2", doc_id, org.org_id
        )
        if not exists:
            raise HTTPException(status_code=404, detail="Document not found")
        rows = await conn.fetch(
            "SELECT chunk_index, content FROM kb_chunks "
            "WHERE document_id=$1 ORDER BY chunk_index LIMIT $2",
            doc_id,
            limit,
        )
    return {
        "document_id": doc_id,
        "chunks": [{"chunk_index": r["chunk_index"], "content": r["content"]} for r in rows],
        "count": len(rows),
    }


@router.get("/documents/{doc_id}/download")
async def download_document(doc_id: int, org: ActiveOrg = Depends(get_active_org)):
    """Download the original uploaded file."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT filename, content_type, stored_path FROM kb_documents WHERE id=$1 AND org_id=$2",
            doc_id,
            org.org_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Document not found")
    stored = row["stored_path"]
    if not stored or not Path(stored).exists():
        raise HTTPException(status_code=404, detail="Stored file is missing")
    return FileResponse(
        stored,
        media_type=row["content_type"] or "application/octet-stream",
        filename=row["filename"],
    )


@router.post("/documents", status_code=201)
async def upload_documents(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    org: ActiveOrg = Depends(require_role("member")),
):
    """Upload one or more documents to the knowledge base.

    Files are stored immediately and processed (text extraction + embedding) in
    the background. Poll ``GET /kb/documents`` to watch each document's status
    transition ``pending → processing → ready``/``error``.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    created: list[dict] = []
    rejected: list[dict] = []

    for upload in files:
        filename = upload.filename or "upload"
        data = await upload.read()
        if len(data) == 0:
            rejected.append({"filename": filename, "reason": "Empty file"})
            continue
        if len(data) > MAX_UPLOAD_BYTES:
            rejected.append({
                "filename": filename,
                "reason": f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit",
            })
            continue
        if not is_supported(filename):
            rejected.append({"filename": filename, "reason": "Unsupported file type"})
            continue

        record = await store.save_upload(org.org_id, filename, data)
        created.append(record)
        # Kick off immediate processing; the scheduler is a safety-net for the rest.
        background_tasks.add_task(store.process_document, record["id"])

    if not created and rejected:
        raise HTTPException(
            status_code=400,
            detail={"message": "No files could be accepted", "rejected": rejected},
        )

    return {"status": "ok", "created": created, "rejected": rejected}


@router.post("/documents/{doc_id}/reprocess")
async def reprocess_document(
    doc_id: int,
    background_tasks: BackgroundTasks,
    org: ActiveOrg = Depends(require_role("member")),
):
    """Re-run extraction and embedding for a document (e.g. after a failure)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        updated = await conn.fetchval(
            "UPDATE kb_documents SET status='pending', error_message=NULL "
            "WHERE id=$1 AND org_id=$2 RETURNING id",
            doc_id,
            org.org_id,
        )
    if updated is None:
        raise HTTPException(status_code=404, detail="Document not found")
    background_tasks.add_task(store.process_document, doc_id)
    return {"status": "queued", "id": doc_id}


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: int, org: ActiveOrg = Depends(require_role("member"))):
    """Delete a document, its chunks, and its stored file."""
    ok = await store.delete_document(org.org_id, doc_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"status": "ok", "deleted": doc_id}


class KbSearchRequest(BaseModel):
    query: str
    limit: int = 10
    document_ids: Optional[list[int]] = None


@router.post("/search")
async def search_kb(req: KbSearchRequest, org: ActiveOrg = Depends(get_active_org)):
    """Semantic search across the active organization's knowledge base."""
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Query must not be empty")
    try:
        results = await store.search_documents(
            org.org_id, req.query.strip(), limit=req.limit, document_ids=req.document_ids
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {e}")
    return {"results": results, "count": len(results)}
