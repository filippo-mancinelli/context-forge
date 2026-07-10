"""Recursive site crawler: discover and index every page under a root URL.

A ``web_sites`` row is a crawl root. Crawling seeds a queue from the root URL
plus the site's sitemap.xml (when present), then follows same-scope links
breadth-first: same host, path under the root's path. Each discovered page is
fetched once — links are extracted from the same response that gets embedded —
and stored as a ``web_pages`` row with ``site_id`` set, so search works over
crawled and standalone pages alike.

``exclude_patterns`` is a list of URL patterns skipped during crawling: a
pattern containing ``*`` is glob-matched against the full URL, otherwise it
matches as a case-insensitive substring. Pages already indexed that match a
newly added pattern are deleted at the start of the next crawl.

Like page processing, a crawl is claimed with an atomic ``pending → crawling``
transition so the API kick and the scheduler safety-net never race.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from fnmatch import fnmatch
from typing import Any, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

from ..db import get_pool
from . import store
from .store import FETCH_TIMEOUT_SECONDS, _USER_AGENT, normalize_url

logger = logging.getLogger(__name__)

DEFAULT_MAX_PAGES = 200
HARD_MAX_PAGES = 1000
CRAWL_CONCURRENCY = 5

# Update the site's pages_found counter every N processed pages.
_PROGRESS_EVERY = 5

# Never enqueue obvious binary/asset URLs.
_SKIP_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
    ".css", ".js", ".mjs", ".map", ".woff", ".woff2", ".ttf", ".eot",
    ".zip", ".gz", ".tar", ".rar", ".7z", ".exe", ".dmg", ".msi",
    ".mp3", ".mp4", ".webm", ".avi", ".mov", ".pdf",
)


def matches_exclusion(url: str, patterns: list[str]) -> bool:
    """True if the URL matches any exclusion pattern (glob if it has ``*``)."""
    low = url.lower()
    for raw in patterns or []:
        pat = (raw or "").strip().lower()
        if not pat:
            continue
        if "*" in pat:
            if fnmatch(low, pat) or fnmatch(low, f"*{pat}*"):
                return True
        elif pat in low:
            return True
    return False


def _canonical(url: str) -> str:
    """Normalize a URL for deduplication: drop fragment, default port, trailing slash."""
    parts = urlsplit(url)
    netloc = parts.netloc.lower()
    for scheme, port in (("http", ":80"), ("https", ":443")):
        if parts.scheme == scheme and netloc.endswith(port):
            netloc = netloc[: -len(port)]
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), netloc, path, parts.query, ""))


def _scope_of(root_url: str) -> tuple[str, str]:
    """Return (host, path_prefix) that defines which URLs belong to the site."""
    parts = urlsplit(_canonical(root_url))
    prefix = parts.path if parts.path != "/" else ""
    return parts.netloc, prefix


def in_scope(url: str, root_url: str) -> bool:
    """True if a URL lives on the same host, under the root's path."""
    host, prefix = _scope_of(root_url)
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or parts.netloc.lower() != host:
        return False
    if not prefix:
        return True
    path = parts.path or "/"
    return path == prefix or path.startswith(prefix + "/")


def _resolve_link(base_url: str, href: str) -> Optional[str]:
    """Resolve an href against its page; return a canonical URL or None."""
    href = (href or "").strip()
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return None
    try:
        absolute = urljoin(base_url, href)
        parts = urlsplit(absolute)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    if parts.path.lower().endswith(_SKIP_EXTENSIONS):
        return None
    return _canonical(absolute)


_SITEMAP_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)


async def _sitemap_urls(client: Any, root_url: str) -> list[str]:
    """Best-effort: collect page URLs from the host's sitemap.xml (one level of nesting)."""
    parts = urlsplit(root_url)
    candidates = [f"{parts.scheme}://{parts.netloc}/sitemap.xml"]
    urls: list[str] = []
    seen_maps: set[str] = set()
    while candidates and len(seen_maps) < 20:
        sitemap = candidates.pop(0)
        if sitemap in seen_maps:
            continue
        seen_maps.add(sitemap)
        try:
            resp = await client.get(sitemap)
            if resp.status_code >= 400:
                continue
            locs = _SITEMAP_LOC_RE.findall(resp.text)
        except Exception:  # noqa: BLE001 — sitemaps are optional
            continue
        for loc in locs:
            if loc.lower().endswith((".xml", ".xml.gz")):
                candidates.append(loc)
            else:
                urls.append(loc)
        if len(urls) >= HARD_MAX_PAGES:
            break
    return urls


async def add_site(
    org_id: int,
    root_url: str,
    max_pages: int = DEFAULT_MAX_PAGES,
    exclude_patterns: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Register (or re-queue) a crawl root. Returns the site record."""
    norm = _canonical(normalize_url(root_url))
    max_pages = max(1, min(int(max_pages or DEFAULT_MAX_PAGES), HARD_MAX_PAGES))
    patterns = [p.strip() for p in (exclude_patterns or []) if p and p.strip()]
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO web_sites (org_id, root_url, status, max_pages, exclude_patterns)
            VALUES ($1, $2, 'pending', $3, $4::jsonb)
            ON CONFLICT (org_id, root_url)
            DO UPDATE SET status='pending', error_message=NULL,
                          max_pages=EXCLUDED.max_pages,
                          exclude_patterns=EXCLUDED.exclude_patterns
            RETURNING id, root_url, status, max_pages, exclude_patterns
            """,
            org_id,
            norm,
            max_pages,
            json.dumps(patterns),
        )
    return site_row_to_dict(row)


def site_row_to_dict(row: Any) -> dict[str, Any]:
    d = dict(row)
    patterns = d.get("exclude_patterns")
    if isinstance(patterns, str):
        try:
            patterns = json.loads(patterns)
        except Exception:
            patterns = []
    d["exclude_patterns"] = patterns or []
    for key in ("created_at", "crawled_at"):
        if d.get(key):
            d[key] = d[key].isoformat()
    d["id"] = int(d["id"])
    return d


async def crawl_site(site_id: int) -> bool:
    """Crawl a site if it is claimable. Returns True if handled."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE web_sites
            SET status='crawling', error_message=NULL
            WHERE id=$1 AND status IN ('pending', 'error')
            RETURNING id, org_id, root_url, max_pages, exclude_patterns
            """,
            site_id,
        )
    if row is None:
        return False

    patterns = row["exclude_patterns"]
    if isinstance(patterns, str):
        try:
            patterns = json.loads(patterns)
        except Exception:
            patterns = []

    try:
        pages_found = await _run_crawl(
            site_id=int(row["id"]),
            org_id=int(row["org_id"]),
            root_url=row["root_url"],
            max_pages=int(row["max_pages"]),
            exclude_patterns=list(patterns or []),
        )
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE web_sites SET status='ready', pages_found=$2, "
                "error_message=NULL, crawled_at=NOW() WHERE id=$1",
                site_id,
                pages_found,
            )
    except Exception as e:  # noqa: BLE001
        message = str(e) or e.__class__.__name__
        logger.warning("WEB crawl failed for site=%s: %s", site_id, message)
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE web_sites SET status='error', error_message=$2, "
                "crawled_at=NOW() WHERE id=$1",
                site_id,
                message[:2000],
            )
    return True


async def _delete_excluded_pages(
    site_id: int, org_id: int, patterns: list[str]
) -> None:
    """Remove already-indexed site pages that match the current exclusions."""
    if not patterns:
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, url FROM web_pages WHERE site_id=$1 AND org_id=$2",
            site_id,
            org_id,
        )
        doomed = [int(r["id"]) for r in rows if matches_exclusion(r["url"], patterns)]
        if doomed:
            await conn.execute("DELETE FROM web_pages WHERE id = ANY($1)", doomed)
            logger.info(
                "WEB crawl site=%s removed %d pages matching exclusions",
                site_id, len(doomed),
            )


async def _run_crawl(
    *, site_id: int, org_id: int, root_url: str,
    max_pages: int, exclude_patterns: list[str],
) -> int:
    """BFS-crawl the site and index each page. Returns the number of pages found."""
    import httpx

    started = time.monotonic()
    root = _canonical(normalize_url(root_url))
    max_pages = max(1, min(max_pages, HARD_MAX_PAGES))

    await _delete_excluded_pages(site_id, org_id, exclude_patterns)

    def eligible(url: str) -> bool:
        return in_scope(url, root) and not matches_exclusion(url, exclude_patterns)

    queue: asyncio.Queue[str] = asyncio.Queue()
    seen: set[str] = set()
    processed = 0
    indexed = 0
    lock = asyncio.Lock()
    pool = await get_pool()

    async def enqueue(url: str) -> None:
        # Caller holds no lock — only mutate shared state under `lock`.
        async with lock:
            if url in seen or len(seen) >= max_pages:
                return
            seen.add(url)
        await queue.put(url)

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=FETCH_TIMEOUT_SECONDS,
        headers={"User-Agent": _USER_AGENT, "Accept": "text/html,*/*"},
    ) as client:
        await enqueue(root)
        for url in await _sitemap_urls(client, root):
            resolved = _resolve_link(root, url)
            if resolved and eligible(resolved):
                await enqueue(resolved)

        # Simple cooperative shutdown: a worker exits when the queue stays
        # empty and every other worker is idle too.
        _idle_workers = [False] * CRAWL_CONCURRENCY

        async def tracked_worker(slot: int) -> None:
            nonlocal processed, indexed
            while True:
                try:
                    url = queue.get_nowait()
                    _idle_workers[slot] = False
                except asyncio.QueueEmpty:
                    _idle_workers[slot] = True
                    if all(_idle_workers):
                        return
                    await asyncio.sleep(0.25)
                    continue

                links: list[str] = []
                record = await store.add_url(org_id, url, site_id=site_id)
                page_id = record["id"]
                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE web_pages SET status='processing' WHERE id=$1", page_id
                    )
                try:
                    title, text, hrefs, final_url = await store._fetch(url, client=client)
                    for href in hrefs:
                        resolved = _resolve_link(final_url, href)
                        if resolved and eligible(resolved):
                            links.append(resolved)
                    await store.embed_and_store(
                        page_id=page_id, org_id=org_id, url=url, title=title, text=text,
                    )
                    indexed += 1
                except Exception as e:  # noqa: BLE001
                    await store.mark_page_error(page_id, str(e) or e.__class__.__name__)

                processed += 1
                if processed % _PROGRESS_EVERY == 0:
                    async with pool.acquire() as conn:
                        await conn.execute(
                            "UPDATE web_sites SET pages_found=$2 WHERE id=$1",
                            site_id, processed,
                        )
                for link in links:
                    await enqueue(link)
                queue.task_done()

        await asyncio.gather(*(tracked_worker(i) for i in range(CRAWL_CONCURRENCY)))

    logger.info(
        "WEB crawl site=%s finished: %d pages (%d indexed) in %.1fs",
        site_id, processed, indexed, time.monotonic() - started,
    )
    return processed


async def process_pending_sites(limit: int = 2) -> None:
    """Crawl any sites left in the ``pending`` state (scheduler safety-net)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id FROM web_sites WHERE status='pending' ORDER BY created_at LIMIT $1",
            limit,
        )
    for row in rows:
        try:
            await crawl_site(int(row["id"]))
        except Exception as e:  # noqa: BLE001
            logger.error("WEB pending crawl error for site=%s: %s", row["id"], e)


async def delete_site(org_id: int, site_id: int) -> bool:
    """Delete a site and its pages/chunks (via cascade). Returns True if found."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        deleted = await conn.fetchval(
            "DELETE FROM web_sites WHERE id=$1 AND org_id=$2 RETURNING id",
            site_id,
            org_id,
        )
    return deleted is not None


async def update_site(
    org_id: int,
    site_id: int,
    max_pages: Optional[int] = None,
    exclude_patterns: Optional[list[str]] = None,
) -> Optional[dict[str, Any]]:
    """Update a site's crawl settings. Returns the updated record, or None."""
    sets: list[str] = []
    params: list[Any] = [site_id, org_id]
    if max_pages is not None:
        params.append(max(1, min(int(max_pages), HARD_MAX_PAGES)))
        sets.append(f"max_pages=${len(params)}")
    if exclude_patterns is not None:
        cleaned = [p.strip() for p in exclude_patterns if p and p.strip()]
        params.append(json.dumps(cleaned))
        sets.append(f"exclude_patterns=${len(params)}::jsonb")
    if not sets:
        return await get_site(org_id, site_id)

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE web_sites SET {', '.join(sets)}
            WHERE id=$1 AND org_id=$2
            RETURNING id, root_url, status, max_pages, exclude_patterns,
                      pages_found, error_message, created_at, crawled_at
            """,
            *params,
        )
    return site_row_to_dict(row) if row else None


async def get_site(org_id: int, site_id: int) -> Optional[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, root_url, status, max_pages, exclude_patterns,
                   pages_found, error_message, created_at, crawled_at
            FROM web_sites WHERE id=$1 AND org_id=$2
            """,
            site_id,
            org_id,
        )
    return site_row_to_dict(row) if row else None
