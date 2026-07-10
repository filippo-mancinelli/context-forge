"""Web-page scraping: fetch a URL, extract readable text, embed, search.

Mirrors the knowledge-base pipeline (``kb/store.py``): a URL is recorded in
``web_pages`` with status ``pending``, then processing (fetch → extract → chunk →
embed → store) transitions it to ``ready`` (or ``error``). Processing is claimed
with an atomic ``pending → processing`` transition so the background kick and the
scheduler safety-net never double-process the same row.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Optional
from urllib.parse import urlparse

from ..db import get_pool
from ..indexer.embedder import embed_batch
from ..indexer.indexer import _sliding_window_chunks, _vector_to_pg

logger = logging.getLogger(__name__)

# Cap the total characters embedded per page to keep costs/latency bounded.
MAX_CHARS = 1_000_000

# Reject absurdly large downloads outright.
MAX_FETCH_BYTES = 10 * 1024 * 1024

FETCH_TIMEOUT_SECONDS = 30.0

# A realistic UA — many sites 403 the default httpx agent.
_USER_AGENT = (
    "Mozilla/5.0 (compatible; context-forge/1.0; +https://github.com/context-forge)"
)


class FetchError(Exception):
    """Raised when a URL cannot be fetched or yields no readable text."""


def normalize_url(url: str) -> str:
    """Trim, default to https, and validate that it is a real http(s) URL."""
    raw = (url or "").strip()
    if not raw:
        raise FetchError("URL must not be empty.")
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise FetchError("Only http and https URLs are supported.")
    if not parsed.netloc:
        raise FetchError("URL is missing a host.")
    return raw


def _extract_readable(html: str, content_type: str) -> tuple[Optional[str], str, list[str]]:
    """Return (title, readable_text, hrefs) from an HTML (or plain-text) document."""
    if "html" not in content_type and "xml" not in content_type:
        # Plain text / markdown / json served directly.
        return None, html, []

    links: list[str] = []
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        # Collect links before stripping nav/header/footer — that is where most
        # site navigation (and thus crawlable structure) lives.
        links = [a["href"] for a in soup.find_all("a", href=True)]
        for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "form", "svg"]):
            tag.decompose()
        title = None
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        # Prefer <main>/<article> when present — usually the real content.
        root = soup.find("main") or soup.find("article") or soup.body or soup
        text = root.get_text(separator="\n")
    except ImportError:
        import re

        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        title = m.group(1).strip() if m else None
        links = re.findall(r"<a[^>]+href=[\"']([^\"'#][^\"']*)[\"']", html, re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", html)

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return title, "\n".join(lines).strip(), links


async def _fetch(url: str, client: Any = None) -> tuple[Optional[str], str, list[str], str]:
    """Download a URL; return (title, readable_text, hrefs, final_url). Raises FetchError."""
    import httpx

    async def _get(c) -> Any:
        return await c.get(url)

    try:
        if client is not None:
            resp = await _get(client)
        else:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=FETCH_TIMEOUT_SECONDS,
                headers={"User-Agent": _USER_AGENT, "Accept": "text/html,*/*"},
            ) as own_client:
                resp = await _get(own_client)
    except httpx.HTTPError as e:
        raise FetchError(f"Could not fetch the page: {e}") from e

    if resp.status_code >= 400:
        raise FetchError(f"The server returned HTTP {resp.status_code}.")

    content_type = (resp.headers.get("content-type") or "").lower()
    if content_type and not any(
        t in content_type for t in ("html", "xml", "text", "json", "markdown")
    ):
        raise FetchError(f"Unsupported content type: {content_type}.")
    if len(resp.content) > MAX_FETCH_BYTES:
        raise FetchError("The page is too large to ingest.")

    title, text, links = _extract_readable(resp.text, content_type)
    if not text.strip():
        raise FetchError("No readable text could be extracted from this page.")
    return title, text, links, str(resp.url)


async def add_url(org_id: int, url: str, site_id: Optional[int] = None) -> dict[str, Any]:
    """Register a URL as a ``pending`` web page. Returns the created record.

    Re-adding an existing URL resets it to ``pending`` so it will be re-fetched.
    A page discovered by a site crawl adopts that site (site_id).
    """
    norm = normalize_url(url)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO web_pages (org_id, url, status, metadata, site_id)
            VALUES ($1, $2, 'pending', '{}'::jsonb, $3)
            ON CONFLICT (org_id, url)
            DO UPDATE SET status='pending', error_message=NULL,
                          site_id=COALESCE(EXCLUDED.site_id, web_pages.site_id)
            RETURNING id, url, title, status
            """,
            org_id,
            norm,
            site_id,
        )
    return {
        "id": int(row["id"]),
        "url": row["url"],
        "title": row["title"],
        "status": row["status"],
    }


async def process_page(page_id: int) -> bool:
    """Fetch + embed a page if it is claimable. Returns True if handled."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE web_pages
            SET status='processing', error_message=NULL
            WHERE id=$1 AND status IN ('pending', 'error')
            RETURNING id, org_id, url, metadata
            """,
            page_id,
        )
    if row is None:
        return False

    await _run_fetch(
        page_id=int(row["id"]),
        org_id=int(row["org_id"]),
        url=row["url"],
        existing_meta=row["metadata"],
    )
    return True


async def embed_and_store(
    *, page_id: int, org_id: int, url: str, title: Optional[str], text: str,
    existing_meta: Any = None,
) -> None:
    """Chunk, embed, and persist a page's extracted text; mark it ``ready``.

    Raises on failure — callers decide how to record the error state.
    """
    pool = await get_pool()
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]

    from ..org_config import get_org_config

    indexing = (await get_org_config(org_id)).indexing
    raw_chunks = _sliding_window_chunks(text, indexing.chunk_size, indexing.chunk_overlap)
    chunk_texts = [c["content"] for c in raw_chunks if c["content"].strip()]
    if not chunk_texts:
        raise FetchError("The page produced no usable text chunks.")

    logger.info(
        "WEB embedding page=%s org=%s chunks=%d chars=%d",
        page_id, org_id, len(chunk_texts), len(text),
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
    content_hash = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM web_chunks WHERE page_id=$1", page_id)
            await conn.executemany(
                """
                INSERT INTO web_chunks
                    (org_id, page_id, chunk_index, content, metadata, embedding)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6::vector)
                """,
                [
                    (
                        org_id,
                        page_id,
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
                UPDATE web_pages
                SET status='ready', title=$2, total_chunks=$3, char_count=$4,
                    content_hash=$5, error_message=NULL, metadata=$6::jsonb,
                    fetched_at=NOW()
                WHERE id=$1
                """,
                page_id,
                title or url,
                len(chunk_texts),
                len(text),
                content_hash,
                json.dumps(base_meta),
            )


async def mark_page_error(page_id: int, message: str) -> None:
    """Record a processing failure on a page."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE web_pages SET status='error', error_message=$2, fetched_at=NOW() WHERE id=$1",
            page_id,
            message[:2000],
        )


async def _run_fetch(*, page_id: int, org_id: int, url: str, existing_meta: Any) -> None:
    started = time.monotonic()
    try:
        title, text, _links, _final = await _fetch(url)
        await embed_and_store(
            page_id=page_id, org_id=org_id, url=url, title=title, text=text,
            existing_meta=existing_meta,
        )
        logger.info(
            "WEB processed page=%s elapsed=%.1fs", page_id, time.monotonic() - started
        )
    except Exception as e:  # noqa: BLE001
        message = str(e) or e.__class__.__name__
        logger.warning("WEB processing failed for page=%s: %s", page_id, message)
        await mark_page_error(page_id, message)


async def process_pending_pages(limit: int = 5) -> None:
    """Process any pages left in the ``pending`` state (scheduler safety-net)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id FROM web_pages WHERE status='pending' ORDER BY created_at LIMIT $1",
            limit,
        )
    for row in rows:
        try:
            await process_page(int(row["id"]))
        except Exception as e:  # noqa: BLE001
            logger.error("WEB pending processing error for page=%s: %s", row["id"], e)


async def reset_stale_processing() -> None:
    """On startup, requeue pages/sites stuck mid-processing from a prior crash."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE web_pages SET status='pending' WHERE status='processing'"
        )
        await conn.execute(
            "UPDATE web_sites SET status='pending' WHERE status='crawling'"
        )


async def delete_page(org_id: int, page_id: int) -> bool:
    """Delete a page and its chunks (via cascade). Returns True if found."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        deleted = await conn.fetchval(
            "DELETE FROM web_pages WHERE id=$1 AND org_id=$2 RETURNING id",
            page_id,
            org_id,
        )
    return deleted is not None


async def search_pages(
    org_id: int,
    query: str,
    limit: int = 10,
    page_ids: Optional[list[int]] = None,
) -> list[dict[str, Any]]:
    """Search a tenant's web-page chunks (hybrid vector + full-text)."""
    from ..search import search_web_chunks

    return await search_web_chunks(org_id, query, page_ids=page_ids, limit=limit)
