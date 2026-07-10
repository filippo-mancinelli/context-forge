"""MCP tools for scraped web pages."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from .server import mcp
from ..db import get_pool

logger = logging.getLogger(__name__)

# Keep strong references to fire-and-forget processing tasks so they are not
# garbage-collected mid-run. The scheduler's pending-page safety-net would
# eventually pick the page up anyway, but this keeps the common path prompt.
_background_tasks: set[asyncio.Task] = set()


def _kick_processing(page_id: int) -> None:
    from ..web import store

    task = asyncio.create_task(store.process_page(page_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _kick_crawl(site_id: int) -> None:
    from ..web import crawler

    task = asyncio.create_task(crawler.crawl_site(site_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


@mcp.tool()
async def web_search(
    query: str,
    limit: int = 10,
    page_ids: Optional[list[int]] = None,
) -> dict:
    """Search scraped web pages using semantic similarity.

    Finds passages from URLs the user has added and indexed, relevant to the
    query.

    Args:
        query: Natural language search query
        limit: Maximum number of matching passages to return (default 10)
        page_ids: Optional list of page ids to restrict the search to

    Returns:
        dict with a list of results, each with page_id, title, url, content,
        and a relevance score
    """
    from ..web import store
    from .context import resolve_org_id

    org_id = await resolve_org_id()
    try:
        results = await store.search_pages(org_id, query, limit=limit, page_ids=page_ids)
    except Exception as e:  # noqa: BLE001
        logger.error("web_search failed: %s", e)
        return {"status": "error", "error": str(e)}
    return {"status": "ok", "results": results, "count": len(results)}


@mcp.tool()
async def web_list(limit: int = 50) -> dict:
    """List scraped web pages and their processing status.

    Args:
        limit: Maximum number of pages to return (default 50)

    Returns:
        dict with a list of pages (id, url, title, status, total_chunks)
    """
    from .context import resolve_org_id

    org_id = await resolve_org_id()
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, url, title, status, total_chunks, char_count,
                   error_message, created_at
            FROM web_pages
            WHERE org_id=$1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            org_id,
            limit,
        )
    pages = []
    for r in rows:
        d = dict(r)
        if d.get("created_at"):
            d["created_at"] = d["created_at"].isoformat()
        pages.append(d)
    return {"status": "ok", "pages": pages, "count": len(pages)}


@mcp.tool()
async def web_get_page(page_id: int, max_chars: int = 20000) -> dict:
    """Retrieve the full extracted text of a scraped web page.

    Reassembles the page from its stored chunks. Use this after web_search or
    web_list to read a page's whole content instead of isolated passages.

    Args:
        page_id: The id of the page (from web_list or web_search)
        max_chars: Maximum number of characters to return (default 20000)

    Returns:
        dict with the page's title, url, and extracted text content
    """
    from .context import resolve_org_id

    org_id = await resolve_org_id()
    pool = await get_pool()
    async with pool.acquire() as conn:
        page = await conn.fetchrow(
            "SELECT title, url, status, total_chunks FROM web_pages "
            "WHERE id=$1 AND org_id=$2",
            page_id,
            org_id,
        )
        if page is None:
            return {"status": "error", "error": f"Page {page_id} not found"}
        rows = await conn.fetch(
            "SELECT content FROM web_chunks WHERE page_id=$1 ORDER BY chunk_index",
            page_id,
        )

    content = "\n\n".join(r["content"] for r in rows)
    truncated = len(content) > max_chars
    if truncated:
        content = content[:max_chars]
    return {
        "status": "ok",
        "page_id": page_id,
        "title": page["title"],
        "url": page["url"],
        "page_status": page["status"],
        "content": content,
        "truncated": truncated,
    }


@mcp.tool()
async def web_add(urls: list[str]) -> dict:
    """Add one or more single URLs to be scraped and indexed for semantic search.

    Each URL is fetched, cleaned, and embedded in the background. Re-adding an
    existing URL re-fetches it. Use web_list to check processing status.
    To index a whole site or documentation tree (all sub-pages under a root
    URL), use web_crawl instead.

    Args:
        urls: The http(s) URLs to ingest

    Returns:
        dict with the created page records and any rejected URLs
    """
    from ..web import store
    from ..web.store import FetchError, normalize_url
    from .context import resolve_org_id

    org_id = await resolve_org_id()

    created: list[dict] = []
    rejected: list[dict] = []
    seen: set[str] = set()
    for raw in urls:
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
        record = await store.add_url(org_id, norm)
        created.append(record)
        _kick_processing(record["id"])

    if not created and rejected:
        return {"status": "error", "error": "No URLs could be accepted", "rejected": rejected}
    return {"status": "ok", "created": created, "rejected": rejected}


@mcp.tool()
async def web_crawl(
    url: str,
    max_pages: int = 200,
    exclude_patterns: Optional[list[str]] = None,
) -> dict:
    """Crawl a whole site (or doc tree) and index every page under the URL.

    Unlike web_add — which indexes a single page — this discovers and indexes
    all pages under the given root: it follows links on the same host whose
    path is under the root's path, plus the site's sitemap.xml. Use this when
    the user points at documentation or a site whose content spans many
    sub-pages (e.g. https://docs.example.com). Crawling runs in the background;
    use web_list_sites to check progress.

    Args:
        url: The root http(s) URL to crawl (e.g. https://docs.example.com)
        max_pages: Maximum number of pages to crawl (default 200, max 1000)
        exclude_patterns: URL patterns to skip — a pattern containing ``*`` is
            glob-matched against the full URL, otherwise it matches as a
            substring (e.g. ["/blog/", "*/changelog*"])

    Returns:
        dict with the created site record (crawling starts in the background)
    """
    from ..web import crawler
    from ..web.store import FetchError
    from .context import resolve_org_id

    org_id = await resolve_org_id()
    try:
        site = await crawler.add_site(org_id, url, max_pages, exclude_patterns)
    except FetchError as e:
        return {"status": "error", "error": str(e)}
    _kick_crawl(site["id"])
    return {"status": "ok", "site": site}


@mcp.tool()
async def web_list_sites(limit: int = 50) -> dict:
    """List crawled sites and their status (pages found, indexing progress).

    Args:
        limit: Maximum number of sites to return (default 50)

    Returns:
        dict with a list of sites (id, root_url, status, pages_found,
        exclude_patterns, page counts)
    """
    from .context import resolve_org_id

    org_id = await resolve_org_id()
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT s.id, s.root_url, s.status, s.max_pages, s.exclude_patterns,
                   s.pages_found, s.error_message, s.created_at, s.crawled_at,
                   COUNT(p.id)                                 AS total_pages,
                   COUNT(p.id) FILTER (WHERE p.status='ready') AS ready_pages
            FROM web_sites s
            LEFT JOIN web_pages p ON p.site_id = s.id
            WHERE s.org_id=$1
            GROUP BY s.id
            ORDER BY s.created_at DESC
            LIMIT $2
            """,
            org_id,
            limit,
        )
    from ..web.crawler import site_row_to_dict

    return {"status": "ok", "sites": [site_row_to_dict(r) for r in rows], "count": len(rows)}


@mcp.tool()
async def web_delete_site(site_id: int) -> dict:
    """Delete a crawled site along with all of its indexed pages.

    Args:
        site_id: The id of the site to delete (from web_list_sites)

    Returns:
        dict confirming the deletion
    """
    from ..web import crawler
    from .context import resolve_org_id

    org_id = await resolve_org_id()
    ok = await crawler.delete_site(org_id, site_id)
    if not ok:
        return {"status": "error", "error": f"Site {site_id} not found"}
    return {"status": "ok", "deleted": site_id}


@mcp.tool()
async def web_refetch(page_id: int) -> dict:
    """Re-fetch and re-embed a scraped web page.

    Useful when the page content has changed or a previous fetch failed.
    Use web_list to check processing status.

    Args:
        page_id: The id of the page to re-fetch (from web_list or web_search)

    Returns:
        dict with the queued page id
    """
    from .context import resolve_org_id

    org_id = await resolve_org_id()
    pool = await get_pool()
    async with pool.acquire() as conn:
        updated = await conn.fetchval(
            "UPDATE web_pages SET status='pending', error_message=NULL "
            "WHERE id=$1 AND org_id=$2 RETURNING id",
            page_id,
            org_id,
        )
    if updated is None:
        return {"status": "error", "error": f"Page {page_id} not found"}
    _kick_processing(page_id)
    return {"status": "queued", "id": page_id}


@mcp.tool()
async def web_delete(page_id: int) -> dict:
    """Delete a scraped web page and its indexed content.

    Args:
        page_id: The id of the page to delete (from web_list or web_search)

    Returns:
        dict confirming the deletion
    """
    from ..web import store
    from .context import resolve_org_id

    org_id = await resolve_org_id()
    ok = await store.delete_page(org_id, page_id)
    if not ok:
        return {"status": "error", "error": f"Page {page_id} not found"}
    return {"status": "ok", "deleted": page_id}
