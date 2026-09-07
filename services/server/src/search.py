"""Hybrid retrieval: dense vector similarity fused with lexical full-text search.

Pure vector similarity misses exact-token matches — identifiers, error strings,
config keys, rare symbols — which are exactly what coding agents tend to search
for. This module combines the existing pgvector cosine ranking with a
PostgreSQL full-text ranking over a generated ``content_tsv`` column, fusing the
two result lists with Reciprocal Rank Fusion (RRF).

All repository and knowledge-base search paths funnel through here so the
behaviour (and the ``SEARCH_HYBRID`` toggle) stays consistent across the MCP
tools, the REST API, and the agent chat.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any, Optional

from .config import get_settings
from .db import get_pool
from .indexer.embedder import embed_text
from .org_settings import get_org_settings
from .vector_index import search_session_sql, vector_expr

logger = logging.getLogger(__name__)

# Reciprocal Rank Fusion constant. 60 is the value from the original RRF paper
# (Cormack et al., 2009) and the de-facto default; it damps the influence of
# deep ranks so the two lists blend smoothly rather than either dominating.
RRF_K = 60

# Candidates pulled from each ranker before fusing. A pool wider than the final
# ``limit`` lets a chunk that is mediocre in one ranker but strong in the other
# still surface in the fused result.
CANDIDATE_POOL = 50

# Text-search configuration used for both the generated column (see db.py) and
# query parsing. Must match the column definition.
TSQUERY_CONFIG = "english"


def _vector_to_pg(embedding: list[float]) -> str:
    """Convert an embedding list to a pgvector literal string."""
    return "[" + ",".join(f"{float(v):.10f}" for v in embedding) + "]"


def hybrid_enabled() -> bool:
    return bool(getattr(get_settings(), "search_hybrid", True))


def _parse_metadata(value: Any) -> dict:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:  # noqa: BLE001
            return {}
    return value or {}


def _normalize_scores(rows: list[dict[str, Any]]) -> None:
    """Scale fused scores into (0, 1] in place so the top hit reads ~1.0.

    Raw RRF scores are tiny (≈1/RRF_K) and not comparable across queries. Scaling
    to the batch maximum keeps the ``score`` field interpretable for the UI and
    API consumers without affecting ordering.
    """
    if not rows:
        return
    top = max(r["score"] for r in rows)
    if top <= 0:
        return
    for r in rows:
        r["score"] = round(r["score"] / top, 4)


# --------------------------------------------------------------------------- #
# Repository chunk search
# --------------------------------------------------------------------------- #
# Both branches keep a stable parameter layout so the optional repo filter never
# needs dynamic placeholder renumbering:
#   $1 embedding $2 org_id $3 candidate pool $4 query $5 repos (text[] or NULL) $6 limit $7 project (bigint or NULL)
@lru_cache(maxsize=8)
def _repo_hybrid_sql(dims: int) -> str:
    d = int(dims)
    expr = vector_expr(d)
    return f"""
WITH tsq AS (
    SELECT websearch_to_tsquery('{TSQUERY_CONFIG}', $4) AS query
),
vec AS (
    SELECT id, ROW_NUMBER() OVER (ORDER BY {expr} <=> $1::vector({d})) AS rank
    FROM repo_chunks
    WHERE org_id = $2 AND ($7::bigint IS NULL OR project_id = $7)
      AND ($5::text[] IS NULL OR repo_name = ANY($5))
    ORDER BY {expr} <=> $1::vector({d})
    LIMIT $3
),
kw AS (
    SELECT c.id, ROW_NUMBER() OVER (ORDER BY ts_rank_cd(c.content_tsv, tsq.query) DESC) AS rank
    FROM repo_chunks c, tsq
    WHERE c.org_id = $2 AND ($7::bigint IS NULL OR c.project_id = $7)
      AND ($5::text[] IS NULL OR c.repo_name = ANY($5))
      AND c.content_tsv @@ tsq.query
    ORDER BY ts_rank_cd(c.content_tsv, tsq.query) DESC
    LIMIT $3
),
fused AS (
    SELECT COALESCE(v.id, k.id) AS id,
           COALESCE(1.0 / ({RRF_K} + v.rank), 0)
         + COALESCE(1.0 / ({RRF_K} + k.rank), 0) AS score
    FROM vec v
    FULL OUTER JOIN kw k ON v.id = k.id
)
SELECT c.repo_name, c.file_path, c.chunk_type, c.content, c.metadata, f.score
FROM fused f
JOIN repo_chunks c ON c.id = f.id
ORDER BY f.score DESC
LIMIT $6
"""


@lru_cache(maxsize=8)
def _repo_vector_sql(dims: int) -> str:
    d = int(dims)
    expr = vector_expr(d)
    return f"""
SELECT repo_name, file_path, chunk_type, content, metadata,
       1 - ({expr} <=> $1::vector({d})) AS score
FROM repo_chunks
WHERE org_id = $2 AND ($5::bigint IS NULL OR project_id = $5)
  AND ($3::text[] IS NULL OR repo_name = ANY($3))
ORDER BY {expr} <=> $1::vector({d})
LIMIT $4
"""


async def search_repo_chunks(
    org_id: int,
    query: str,
    repos: Optional[list[str]] = None,
    limit: int = 10,
    project_id: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Search indexed repository chunks for an organization.

    Uses hybrid (vector + full-text) retrieval when enabled, otherwise falls back
    to vector-only cosine similarity. Returns dicts with ``repo_name``,
    ``file_path``, ``chunk_type``, ``content``, ``metadata`` and a ``score``.
    """
    if project_id is None:
        raise ValueError("project_id is required for scoped search")
    dims = int((await get_org_settings(org_id)).embeddings_dims)
    embedding_str = _vector_to_pg(await embed_text(query, org_id))
    pool = await get_pool()
    hybrid = hybrid_enabled()

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(search_session_sql())
            if hybrid:
                rows = await conn.fetch(
                    _repo_hybrid_sql(dims), embedding_str, org_id, CANDIDATE_POOL,
                    query, repos, limit, project_id,
                )
            else:
                rows = await conn.fetch(
                    _repo_vector_sql(dims), embedding_str, org_id, repos, limit, project_id
                )

    results = [
        {
            "repo_name": r["repo_name"],
            "file_path": r["file_path"],
            "chunk_type": r["chunk_type"],
            "content": r["content"],
            "metadata": _parse_metadata(r["metadata"]),
            "score": float(r["score"]),
        }
        for r in rows
    ]
    if hybrid:
        _normalize_scores(results)
    else:
        for r in results:
            r["score"] = round(r["score"], 4)
    return results


# --------------------------------------------------------------------------- #
# Repository symbol search: exact-name lookup over parsed definitions
# --------------------------------------------------------------------------- #
# Populated by tree-sitter chunking (see indexer.py): a "symbol" chunk is a
# top-level function/class/method/type definition whose name tree-sitter could
# extract, stored as chunk_type + metadata->>'name'. Unlike the semantic search
# above, this is a lexical name lookup — the right tool when the caller already
# knows (or half-remembers) an identifier and wants its definition, not
# something merely related in meaning. No embedding call needed.
SYMBOL_CHUNK_TYPES = (
    "function_definition", "class_definition", "decorated_definition",
    "function_declaration", "class_declaration", "arrow_function", "method_definition",
    "interface_declaration", "type_alias_declaration",
    "method_declaration", "type_declaration",
)

_SYMBOL_SEARCH_SQL = """
SELECT repo_name, file_path, chunk_type, metadata, content
FROM repo_chunks
WHERE org_id = $1
  AND ($7::bigint IS NULL OR project_id = $7)
  AND chunk_type = ANY($2::text[])
  AND metadata->>'name' IS NOT NULL
  AND metadata->>'name' ILIKE $3
  AND ($4::text[] IS NULL OR repo_name = ANY($4))
ORDER BY
  (lower(metadata->>'name') = lower($5)) DESC,
  (lower(metadata->>'name') LIKE lower($5) || '%') DESC,
  length(metadata->>'name') ASC,
  repo_name, file_path
LIMIT $6
"""


def _symbol_preview(content: str, max_chars: int = 240) -> str:
    """First non-blank line of a symbol's content (its signature), truncated."""
    stripped = content.strip()
    first_line = stripped.splitlines()[0] if stripped else ""
    return first_line if len(first_line) <= max_chars else first_line[: max_chars - 1] + "…"


async def search_repo_symbols(
    org_id: int,
    query: str,
    repos: Optional[list[str]] = None,
    symbol_types: Optional[list[str]] = None,
    limit: int = 20,
    project_id: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Look up function/class/method/type definitions by name.

    A lexical name search, not semantic similarity: matches substrings of
    ``query`` against parsed definition names, ranked exact match first, then
    prefix match, then shortest name. Returns dicts with repo_name, file_path,
    chunk_type, name, a one-line signature preview, and start_line (when the
    parser recorded one).
    """
    if project_id is None:
        raise ValueError("project_id is required for scoped search")
    types = list(symbol_types) if symbol_types else list(SYMBOL_CHUNK_TYPES)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            _SYMBOL_SEARCH_SQL, org_id, types, f"%{query}%", repos, query, limit, project_id
        )

    results = []
    for r in rows:
        meta = _parse_metadata(r["metadata"])
        results.append({
            "repo_name": r["repo_name"],
            "file_path": r["file_path"],
            "chunk_type": r["chunk_type"],
            "name": meta.get("name"),
            "start_line": meta.get("start_line"),
            "signature": _symbol_preview(r["content"]),
        })
    return results


# --------------------------------------------------------------------------- #
# Web-page chunk search
# --------------------------------------------------------------------------- #
#   $1 embedding  $2 org_id  $3 candidate pool  $4 query text
#   $5 page_ids (bigint[] or NULL)  $6 limit  $7 project (bigint or NULL)
@lru_cache(maxsize=8)
def _web_hybrid_sql(dims: int) -> str:
    d = int(dims)
    expr = vector_expr(d, "c.embedding")
    return f"""
WITH tsq AS (
    SELECT websearch_to_tsquery('{TSQUERY_CONFIG}', $4) AS query
),
vec AS (
    SELECT c.id, ROW_NUMBER() OVER (ORDER BY {expr} <=> $1::vector({d})) AS rank
    FROM web_chunks c
    WHERE c.org_id = $2 AND ($7::bigint IS NULL OR c.project_id = $7)
      AND ($5::bigint[] IS NULL OR c.page_id = ANY($5))
    ORDER BY {expr} <=> $1::vector({d})
    LIMIT $3
),
kw AS (
    SELECT c.id, ROW_NUMBER() OVER (ORDER BY ts_rank_cd(c.content_tsv, tsq.query) DESC) AS rank
    FROM web_chunks c, tsq
    WHERE c.org_id = $2 AND ($7::bigint IS NULL OR c.project_id = $7)
      AND ($5::bigint[] IS NULL OR c.page_id = ANY($5))
      AND c.content_tsv @@ tsq.query
    ORDER BY ts_rank_cd(c.content_tsv, tsq.query) DESC
    LIMIT $3
),
fused AS (
    SELECT COALESCE(v.id, k.id) AS id,
           COALESCE(1.0 / ({RRF_K} + v.rank), 0)
         + COALESCE(1.0 / ({RRF_K} + k.rank), 0) AS score
    FROM vec v
    FULL OUTER JOIN kw k ON v.id = k.id
)
SELECT c.page_id, c.chunk_index, c.content, c.metadata,
       p.title, p.url, f.score
FROM fused f
JOIN web_chunks c ON c.id = f.id
JOIN web_pages p ON p.id = c.page_id
ORDER BY f.score DESC
LIMIT $6
"""


@lru_cache(maxsize=8)
def _web_vector_sql(dims: int) -> str:
    d = int(dims)
    expr = vector_expr(d, "c.embedding")
    return f"""
SELECT c.page_id, c.chunk_index, c.content, c.metadata,
       p.title, p.url,
       1 - ({expr} <=> $1::vector({d})) AS score
FROM web_chunks c
JOIN web_pages p ON p.id = c.page_id
WHERE c.org_id = $2 AND ($5::bigint IS NULL OR c.project_id = $5)
  AND ($3::bigint[] IS NULL OR c.page_id = ANY($3))
ORDER BY {expr} <=> $1::vector({d})
LIMIT $4
"""


async def search_web_chunks(
    org_id: int,
    query: str,
    page_ids: Optional[list[int]] = None,
    limit: int = 10,
    project_id: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Search scraped web-page chunks for a project (hybrid or vector-only)."""
    if project_id is None:
        raise ValueError("project_id is required for scoped search")
    dims = int((await get_org_settings(org_id)).embeddings_dims)
    embedding_str = _vector_to_pg(await embed_text(query, org_id))
    pool = await get_pool()
    hybrid = hybrid_enabled()

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(search_session_sql())
            if hybrid:
                rows = await conn.fetch(
                    _web_hybrid_sql(dims), embedding_str, org_id, CANDIDATE_POOL,
                    query, page_ids, limit, project_id,
                )
            else:
                rows = await conn.fetch(
                    _web_vector_sql(dims), embedding_str, org_id, page_ids, limit, project_id
                )

    results = [
        {
            "page_id": int(r["page_id"]),
            "title": r["title"],
            "url": r["url"],
            "chunk_index": r["chunk_index"],
            "content": r["content"],
            "metadata": _parse_metadata(r["metadata"]),
            "score": float(r["score"]),
        }
        for r in rows
    ]
    if hybrid:
        _normalize_scores(results)
    else:
        for r in results:
            r["score"] = round(r["score"], 4)
    return results


# --------------------------------------------------------------------------- #
# Knowledge-base chunk search
# --------------------------------------------------------------------------- #
#   $1 embedding  $2 org_id  $3 candidate pool  $4 query text
#   $5 document_ids (bigint[] or NULL)  $6 limit  $7 project (bigint or NULL)
@lru_cache(maxsize=8)
def _kb_hybrid_sql(dims: int) -> str:
    d = int(dims)
    expr = vector_expr(d, "c.embedding")
    return f"""
WITH tsq AS (
    SELECT websearch_to_tsquery('{TSQUERY_CONFIG}', $4) AS query
),
vec AS (
    SELECT c.id, ROW_NUMBER() OVER (ORDER BY {expr} <=> $1::vector({d})) AS rank
    FROM kb_chunks c
    WHERE c.org_id = $2 AND ($7::bigint IS NULL OR c.project_id = $7)
      AND ($5::bigint[] IS NULL OR c.document_id = ANY($5))
    ORDER BY {expr} <=> $1::vector({d})
    LIMIT $3
),
kw AS (
    SELECT c.id, ROW_NUMBER() OVER (ORDER BY ts_rank_cd(c.content_tsv, tsq.query) DESC) AS rank
    FROM kb_chunks c, tsq
    WHERE c.org_id = $2 AND ($7::bigint IS NULL OR c.project_id = $7)
      AND ($5::bigint[] IS NULL OR c.document_id = ANY($5))
      AND c.content_tsv @@ tsq.query
    ORDER BY ts_rank_cd(c.content_tsv, tsq.query) DESC
    LIMIT $3
),
fused AS (
    SELECT COALESCE(v.id, k.id) AS id,
           COALESCE(1.0 / ({RRF_K} + v.rank), 0)
         + COALESCE(1.0 / ({RRF_K} + k.rank), 0) AS score
    FROM vec v
    FULL OUTER JOIN kw k ON v.id = k.id
)
SELECT c.document_id, c.chunk_index, c.content, c.metadata,
       d.title, d.filename, d.extension, f.score
FROM fused f
JOIN kb_chunks c ON c.id = f.id
JOIN kb_documents d ON d.id = c.document_id
ORDER BY f.score DESC
LIMIT $6
"""


@lru_cache(maxsize=8)
def _kb_vector_sql(dims: int) -> str:
    d = int(dims)
    expr = vector_expr(d, "c.embedding")
    return f"""
SELECT c.document_id, c.chunk_index, c.content, c.metadata,
       d.title, d.filename, d.extension,
       1 - ({expr} <=> $1::vector({d})) AS score
FROM kb_chunks c
JOIN kb_documents d ON d.id = c.document_id
WHERE c.org_id = $2 AND ($5::bigint IS NULL OR c.project_id = $5)
  AND ($3::bigint[] IS NULL OR c.document_id = ANY($3))
ORDER BY {expr} <=> $1::vector({d})
LIMIT $4
"""


async def search_kb_chunks(
    org_id: int,
    query: str,
    document_ids: Optional[list[int]] = None,
    limit: int = 10,
    project_id: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Search knowledge-base chunks for an organization (hybrid or vector-only)."""
    if project_id is None:
        raise ValueError("project_id is required for scoped search")
    dims = int((await get_org_settings(org_id)).embeddings_dims)
    embedding_str = _vector_to_pg(await embed_text(query, org_id))
    pool = await get_pool()
    hybrid = hybrid_enabled()

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(search_session_sql())
            if hybrid:
                rows = await conn.fetch(
                    _kb_hybrid_sql(dims), embedding_str, org_id, CANDIDATE_POOL,
                    query, document_ids, limit, project_id,
                )
            else:
                rows = await conn.fetch(
                    _kb_vector_sql(dims), embedding_str, org_id, document_ids, limit, project_id
                )

    results = [
        {
            "document_id": int(r["document_id"]),
            "title": r["title"],
            "filename": r["filename"],
            "extension": r["extension"],
            "chunk_index": r["chunk_index"],
            "content": r["content"],
            "metadata": _parse_metadata(r["metadata"]),
            "score": float(r["score"]),
        }
        for r in rows
    ]
    if hybrid:
        _normalize_scores(results)
    else:
        for r in results:
            r["score"] = round(r["score"], 4)
    return results
