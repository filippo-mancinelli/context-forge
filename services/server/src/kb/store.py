"""Knowledge-base document processing: storage, extraction, embedding, search.

An uploaded document is persisted to disk and recorded in ``kb_documents`` with
status ``pending``. Processing (extract → chunk → embed → store) then transitions
it to ``ready`` (or ``error``). Processing is idempotent and safe to run
concurrently: a document is claimed with an atomic ``pending → processing``
status transition so the background kick and the scheduler safety-net never
double-process the same row.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Optional

from ..config import get_settings
from ..db import get_pool
from ..indexer.embedder import embed_batch
from ..indexer.indexer import _sliding_window_chunks, _vector_to_pg

logger = logging.getLogger(__name__)

# Cap the total characters embedded per document to keep costs/latency bounded
# for very large files. ~1M chars ≈ a large book.
MAX_CHARS = 1_000_000

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def get_kb_data_dir() -> Path:
    path = Path(get_settings().kb_data_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _org_dir(org_id: int) -> Path:
    d = get_kb_data_dir() / str(org_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_filename(filename: str) -> str:
    name = Path(filename).name or "upload"
    cleaned = _SAFE_NAME.sub("_", name).strip("._") or "upload"
    return cleaned[:200]


async def save_upload(org_id: int, filename: str, data: bytes) -> dict[str, Any]:
    """Persist raw upload bytes to disk and create a ``pending`` document row.

    Returns the created document record (as a plain dict).
    """
    from ..kb.extract import SUPPORTED_EXTENSIONS

    ext = Path(filename).suffix.lower()
    sha = hashlib.sha256(data).hexdigest()
    safe = _safe_filename(filename)
    title = Path(filename).stem or filename

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO kb_documents
                (org_id, title, filename, content_type, extension, size_bytes,
                 sha256, status, metadata)
            VALUES ($1, $2, $3, $4, $5, $6, $7, 'pending', '{}'::jsonb)
            RETURNING id
            """,
            org_id,
            title,
            filename,
            _guess_content_type(ext),
            ext,
            len(data),
            sha,
        )
        doc_id = int(row["id"])

    # Store the file as "<id>__<safe-name>" so names never collide.
    stored_path = _org_dir(org_id) / f"{doc_id}__{safe}"
    stored_path.write_bytes(data)

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE kb_documents SET stored_path=$2 WHERE id=$1",
            doc_id,
            str(stored_path),
        )

    supported = ext in SUPPORTED_EXTENSIONS
    return {
        "id": doc_id,
        "title": title,
        "filename": filename,
        "extension": ext,
        "size_bytes": len(data),
        "status": "pending",
        "supported": supported,
    }


def _guess_content_type(ext: str) -> str:
    import mimetypes

    return mimetypes.types_map.get(ext, "application/octet-stream")


async def process_document(doc_id: int) -> bool:
    """Process a single document if it is claimable. Returns True if handled.

    Claims the row via an atomic ``pending → processing`` transition, so callers
    racing on the same document are naturally serialized.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE kb_documents
            SET status='processing', error_message=NULL
            WHERE id=$1 AND status IN ('pending', 'error')
            RETURNING id, org_id, filename, stored_path, metadata
            """,
            doc_id,
        )
    if row is None:
        return False  # already processing/ready, or gone

    await _run_extraction(
        doc_id=int(row["id"]),
        org_id=int(row["org_id"]),
        filename=row["filename"],
        stored_path=row["stored_path"],
        existing_meta=row["metadata"],
    )
    return True


async def _run_extraction(
    *,
    doc_id: int,
    org_id: int,
    filename: str,
    stored_path: Optional[str],
    existing_meta: Any,
) -> None:
    from .extract import ExtractionError, extract_text

    pool = await get_pool()
    started = time.monotonic()
    try:
        if not stored_path or not Path(stored_path).exists():
            raise ExtractionError("Stored file is missing on disk.")

        # Extraction is CPU/IO bound — keep the event loop responsive.
        import asyncio

        loop = asyncio.get_running_loop()
        extracted = await loop.run_in_executor(
            None, lambda: extract_text(stored_path, filename=filename)
        )

        text = extracted.text.strip()
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS]
        if not text:
            raise ExtractionError("No text could be extracted from this file.")

        # Chunk with the org's indexing settings (reuses the repo chunker).
        from ..org_config import get_org_config

        indexing = (await get_org_config(org_id)).indexing
        raw_chunks = _sliding_window_chunks(text, indexing.chunk_size, indexing.chunk_overlap)
        chunk_texts = [c["content"] for c in raw_chunks if c["content"].strip()]

        if not chunk_texts:
            raise ExtractionError("Document produced no usable text chunks.")

        logger.info(
            "KB embedding doc=%s org=%s chunks=%d chars=%d",
            doc_id, org_id, len(chunk_texts), len(text),
        )

        embeddings: list[list[float]] = []
        batch_size = 20
        for i in range(0, len(chunk_texts), batch_size):
            embeddings.extend(await embed_batch(chunk_texts[i:i + batch_size]))

        base_meta = existing_meta if isinstance(existing_meta, dict) else {}
        if isinstance(existing_meta, str):
            try:
                base_meta = json.loads(existing_meta)
            except Exception:
                base_meta = {}
        doc_meta = {**base_meta, **extracted.metadata}

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM kb_chunks WHERE document_id=$1", doc_id)
                await conn.executemany(
                    """
                    INSERT INTO kb_chunks
                        (org_id, document_id, chunk_index, content, metadata, embedding)
                    VALUES ($1, $2, $3, $4, $5::jsonb, $6::vector)
                    """,
                    [
                        (
                            org_id,
                            doc_id,
                            idx,
                            chunk_texts[idx],
                            json.dumps({"chunk_index": idx}),
                            _vector_to_pg(embeddings[idx]),
                        )
                        for idx in range(len(chunk_texts))
                    ],
                )
                await conn.execute(
                    """
                    UPDATE kb_documents
                    SET status='ready', total_chunks=$2, char_count=$3,
                        error_message=NULL, metadata=$4::jsonb, processed_at=NOW()
                    WHERE id=$1
                    """,
                    doc_id,
                    len(chunk_texts),
                    len(text),
                    json.dumps(doc_meta),
                )
        logger.info(
            "KB processed doc=%s chunks=%d elapsed=%.1fs",
            doc_id, len(chunk_texts), time.monotonic() - started,
        )

    except Exception as e:  # noqa: BLE001
        message = str(e) or e.__class__.__name__
        logger.warning("KB processing failed for doc=%s: %s", doc_id, message)
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE kb_documents SET status='error', error_message=$2, processed_at=NOW() WHERE id=$1",
                doc_id,
                message[:2000],
            )


async def process_pending_documents(limit: int = 5) -> None:
    """Process any documents left in the ``pending`` state (scheduler safety-net)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id FROM kb_documents WHERE status='pending' ORDER BY uploaded_at LIMIT $1",
            limit,
        )
    for row in rows:
        try:
            await process_document(int(row["id"]))
        except Exception as e:  # noqa: BLE001
            logger.error("KB pending processing error for doc=%s: %s", row["id"], e)


async def reset_stale_processing() -> None:
    """On startup, requeue documents stuck in ``processing`` from a prior crash."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE kb_documents SET status='pending' WHERE status='processing'"
        )


async def delete_document(org_id: int, doc_id: int) -> bool:
    """Delete a document (row + chunks via cascade) and its file. Returns True if found."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT stored_path FROM kb_documents WHERE id=$1 AND org_id=$2",
            doc_id,
            org_id,
        )
        if row is None:
            return False
        await conn.execute(
            "DELETE FROM kb_documents WHERE id=$1 AND org_id=$2", doc_id, org_id
        )

    stored = row["stored_path"]
    if stored:
        try:
            Path(stored).unlink(missing_ok=True)
        except Exception:
            pass
    return True


async def search_documents(
    org_id: int,
    query: str,
    limit: int = 10,
    document_ids: Optional[list[int]] = None,
) -> list[dict[str, Any]]:
    """Search a tenant's knowledge-base chunks (hybrid vector + full-text)."""
    from ..search import search_kb_chunks

    return await search_kb_chunks(
        org_id, query, document_ids=document_ids, limit=limit
    )
