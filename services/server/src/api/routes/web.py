"""REST API routes for web pages (URL scraping & search)."""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from ...db import get_pool
from ...web import crawler, store
from ...web.store import FetchError, normalize_url
from ..deps import ActiveOrg, get_active_org, require_role

router = APIRouter(prefix="/web", tags=["web-pages"])


class WebPageOut(BaseModel):
    id: int
    url: str
    title: Optional[str] = None
    status: str
    site_id: Optional[int] = None
    total_chunks: int = 0
    char_count: int = 0
    error_message: Optional[str] = None
    metadata: dict = {}
    created_at: Optional[str] = None
    fetched_at: Optional[str] = None


def _row_to_out(row) -> WebPageOut:
    d = dict(row)
    meta = d.get("metadata")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    d["metadata"] = meta or {}
    for key in ("created_at", "fetched_at"):
        if d.get(key):
            d[key] = d[key].isoformat()
    d.pop("org_id", None)
    d.pop("content_hash", None)
    return WebPageOut(**d)


_PAGE_COLUMNS = (
    "id, url, title, status, site_id, total_chunks, char_count, error_message, "
    "metadata, created_at, fetched_at"
)


@router.get("/pages", response_model=list[WebPageOut])
async def list_pages(org: ActiveOrg = Depends(get_active_org)):
    """List all web pages for the active organization."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {_PAGE_COLUMNS} FROM web_pages WHERE org_id=$1 ORDER BY created_at DESC",
            org.org_id,
        )
    return [_row_to_out(r) for r in rows]


@router.get("/pages/{page_id}/chunks")
async def get_page_chunks(
    page_id: int, limit: int = 50, org: ActiveOrg = Depends(get_active_org)
):
    """Return a page's extracted text chunks (for previewing content)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM web_pages WHERE id=$1 AND org_id=$2", page_id, org.org_id
        )
        if not exists:
            raise HTTPException(status_code=404, detail="Page not found")
        rows = await conn.fetch(
            "SELECT chunk_index, content FROM web_chunks "
            "WHERE page_id=$1 ORDER BY chunk_index LIMIT $2",
            page_id,
            limit,
        )
    return {
        "page_id": page_id,
        "chunks": [{"chunk_index": r["chunk_index"], "content": r["content"]} for r in rows],
        "count": len(rows),
    }


class WebAddRequest(BaseModel):
    # Accept a single URL or a newline/comma-separated list of URLs.
    urls: list[str]


@router.post("/pages", status_code=201)
async def add_pages(
    req: WebAddRequest,
    background_tasks: BackgroundTasks,
    org: ActiveOrg = Depends(require_role("member")),
):
    """Add one or more URLs. Each is fetched + embedded in the background."""
    if not req.urls:
        raise HTTPException(status_code=400, detail="No URLs provided")

    created: list[dict] = []
    rejected: list[dict] = []
    seen: set[str] = set()
    for raw in req.urls:
        raw = (raw or "").strip()
        if not raw:
            continue
        try:
            norm = normalize_url(raw)
        except FetchError as e:
            rejected.append({"url": raw, "reason": str(e)})
            continue
        if norm in seen:
            continue
        seen.add(norm)
        record = await store.add_url(org.org_id, norm)
        created.append(record)
        background_tasks.add_task(store.process_page, record["id"])

    if not created and rejected:
        raise HTTPException(
            status_code=400,
            detail={"message": "No URLs could be accepted", "rejected": rejected},
        )
    return {"status": "ok", "created": created, "rejected": rejected}


@router.post("/pages/{page_id}/refetch")
async def refetch_page(
    page_id: int,
    background_tasks: BackgroundTasks,
    org: ActiveOrg = Depends(require_role("member")),
):
    """Re-fetch and re-embed a page (e.g. after a change or a failure)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        updated = await conn.fetchval(
            "UPDATE web_pages SET status='pending', error_message=NULL "
            "WHERE id=$1 AND org_id=$2 RETURNING id",
            page_id,
            org.org_id,
        )
    if updated is None:
        raise HTTPException(status_code=404, detail="Page not found")
    background_tasks.add_task(store.process_page, page_id)
    return {"status": "queued", "id": page_id}


@router.delete("/pages/{page_id}")
async def delete_page(page_id: int, org: ActiveOrg = Depends(require_role("member"))):
    """Delete a page and its chunks."""
    ok = await store.delete_page(org.org_id, page_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Page not found")
    return {"status": "ok", "deleted": page_id}


# --------------------------------------------------------------------------- #
# Sites: crawl roots that index a whole documentation tree, not a single page
# --------------------------------------------------------------------------- #


class WebSiteOut(BaseModel):
    id: int
    root_url: str
    status: str
    max_pages: int
    exclude_patterns: list[str] = []
    pages_found: int = 0
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    crawled_at: Optional[str] = None
    # Aggregates over the site's pages, for the UI summary.
    total_pages: int = 0
    ready_pages: int = 0
    error_pages: int = 0
    total_chunks: int = 0


_SITE_LIST_SQL = """
SELECT s.id, s.root_url, s.status, s.max_pages, s.exclude_patterns,
       s.pages_found, s.error_message, s.created_at, s.crawled_at,
       COUNT(p.id)                                   AS total_pages,
       COUNT(p.id) FILTER (WHERE p.status='ready')   AS ready_pages,
       COUNT(p.id) FILTER (WHERE p.status='error')   AS error_pages,
       COALESCE(SUM(p.total_chunks), 0)              AS total_chunks
FROM web_sites s
LEFT JOIN web_pages p ON p.site_id = s.id
WHERE s.org_id = $1
GROUP BY s.id
ORDER BY s.created_at DESC
"""


def _site_row_to_out(row) -> WebSiteOut:
    d = crawler.site_row_to_dict(row)
    return WebSiteOut(**d)


@router.get("/sites", response_model=list[WebSiteOut])
async def list_sites(org: ActiveOrg = Depends(get_active_org)):
    """List crawled sites with per-site page/chunk aggregates."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(_SITE_LIST_SQL, org.org_id)
    return [_site_row_to_out(r) for r in rows]


class WebSiteCreateRequest(BaseModel):
    root_url: str
    max_pages: int = crawler.DEFAULT_MAX_PAGES
    exclude_patterns: list[str] = []


@router.post("/sites", status_code=201)
async def add_site(
    req: WebSiteCreateRequest,
    background_tasks: BackgroundTasks,
    org: ActiveOrg = Depends(require_role("member")),
):
    """Register a crawl root and start crawling it in the background.

    The crawler follows same-scope links (same host, under the root's path)
    and the site's sitemap, skipping any URL that matches exclude_patterns.
    """
    try:
        site = await crawler.add_site(
            org.org_id, req.root_url, req.max_pages, req.exclude_patterns
        )
    except FetchError as e:
        raise HTTPException(status_code=400, detail=str(e))
    background_tasks.add_task(crawler.crawl_site, site["id"])
    return {"status": "ok", "site": site}


class WebSiteUpdateRequest(BaseModel):
    max_pages: Optional[int] = None
    exclude_patterns: Optional[list[str]] = None


@router.patch("/sites/{site_id}", response_model=WebSiteOut)
async def update_site(
    site_id: int,
    req: WebSiteUpdateRequest,
    org: ActiveOrg = Depends(require_role("member")),
):
    """Update a site's crawl settings (applied on the next crawl)."""
    site = await crawler.update_site(
        org.org_id, site_id, max_pages=req.max_pages, exclude_patterns=req.exclude_patterns
    )
    if site is None:
        raise HTTPException(status_code=404, detail="Site not found")
    return WebSiteOut(**site)


@router.post("/sites/{site_id}/recrawl")
async def recrawl_site(
    site_id: int,
    background_tasks: BackgroundTasks,
    org: ActiveOrg = Depends(require_role("member")),
):
    """Re-crawl a site: refresh existing pages, discover new ones, drop excluded ones."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        updated = await conn.fetchval(
            "UPDATE web_sites SET status='pending', error_message=NULL "
            "WHERE id=$1 AND org_id=$2 AND status <> 'crawling' RETURNING id",
            site_id,
            org.org_id,
        )
    if updated is None:
        raise HTTPException(status_code=404, detail="Site not found or already crawling")
    background_tasks.add_task(crawler.crawl_site, site_id)
    return {"status": "queued", "id": site_id}


@router.get("/sites/{site_id}/pages", response_model=list[WebPageOut])
async def list_site_pages(site_id: int, org: ActiveOrg = Depends(get_active_org)):
    """List the pages discovered by a site crawl."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM web_sites WHERE id=$1 AND org_id=$2", site_id, org.org_id
        )
        if not exists:
            raise HTTPException(status_code=404, detail="Site not found")
        rows = await conn.fetch(
            f"SELECT {_PAGE_COLUMNS} FROM web_pages WHERE site_id=$1 AND org_id=$2 "
            "ORDER BY url",
            site_id,
            org.org_id,
        )
    return [_row_to_out(r) for r in rows]


@router.delete("/sites/{site_id}")
async def delete_site(site_id: int, org: ActiveOrg = Depends(require_role("member"))):
    """Delete a site and all of its pages and chunks."""
    ok = await crawler.delete_site(org.org_id, site_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Site not found")
    return {"status": "ok", "deleted": site_id}


class WebSearchRequest(BaseModel):
    query: str
    limit: int = 10
    page_ids: Optional[list[int]] = None


@router.post("/search")
async def search_web(req: WebSearchRequest, org: ActiveOrg = Depends(get_active_org)):
    """Semantic search across the active organization's scraped web pages."""
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Query must not be empty")
    try:
        results = await store.search_pages(
            org.org_id, req.query.strip(), limit=req.limit, page_ids=req.page_ids
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {e}")
    return {"results": results, "count": len(results)}
