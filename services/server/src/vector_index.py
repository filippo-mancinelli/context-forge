"""HNSW vector indexes per organization.

The ``embedding`` columns are untyped ``vector`` so each organization can pick
its own model; pgvector cannot index them directly, so every organization gets
a partial index on the typed cast for its own dimension.
"""
from __future__ import annotations

import logging

from .db import get_pool
from .org_config import all_org_ids
from .org_settings import get_org_settings

logger = logging.getLogger(__name__)

# Tables whose ``embedding`` column is searched by cosine distance.
HNSW_TABLES: tuple[str, ...] = ("repo_chunks", "kb_chunks", "web_chunks")

HNSW_M = 16
HNSW_EF_CONSTRUCTION = 64
HNSW_EF_SEARCH = 100
MAINTENANCE_WORK_MEM = "256MB"


def index_name(table: str, org_id: int, dims: int) -> str:
    return f"{table}_emb_hnsw_org{int(org_id)}_d{int(dims)}"


def vector_expr(dims: int, column: str = "embedding") -> str:
    """Typed cast shared by the index definition and every ORDER BY."""
    return f"({column}::vector({int(dims)}))"


def create_index_sql(table: str, org_id: int, dims: int) -> str:
    if table not in HNSW_TABLES:
        raise ValueError(f"unknown vector table: {table}")
    return (
        f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {index_name(table, org_id, dims)} "
        f"ON {table} USING hnsw ({vector_expr(dims)} vector_cosine_ops) "
        f"WITH (m = {HNSW_M}, ef_construction = {HNSW_EF_CONSTRUCTION}) "
        f"WHERE org_id = {int(org_id)}"
    )


def drop_index_sql(name: str) -> str:
    return f"DROP INDEX CONCURRENTLY IF EXISTS {name}"


_STALE_INDEX_SQL = (
    "SELECT indexname FROM pg_indexes WHERE tablename = $1 AND indexname LIKE $2"
)


async def _stale_index_names(conn, table: str, org_id: int, keep: str) -> list[str]:
    rows = await conn.fetch(
        _STALE_INDEX_SQL, table, f"{table}_emb_hnsw_org{int(org_id)}_d%"
    )
    return [r["indexname"] for r in rows if r["indexname"] != keep]


async def ensure_org_indexes(org_id: int, dims: int) -> list[str]:
    """Create the org's HNSW indexes and drop those of another dimension."""
    created: list[str] = []
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            # CONCURRENTLY needs autocommit: never open a transaction here.
            await conn.execute(f"SET maintenance_work_mem = '{MAINTENANCE_WORK_MEM}'")
            for table in HNSW_TABLES:
                name = index_name(table, org_id, dims)
                try:
                    await conn.execute(create_index_sql(table, org_id, dims))
                    created.append(name)
                except Exception:
                    logger.exception("HNSW index build failed: %s", name)
                try:
                    for stale in await _stale_index_names(conn, table, org_id, name):
                        await conn.execute(drop_index_sql(stale))
                except Exception:
                    logger.exception("HNSW stale index cleanup failed: %s", name)
    except Exception:
        logger.exception("HNSW index maintenance failed (org=%s)", org_id)
    return created


async def ensure_all_indexes() -> None:
    """Refresh the HNSW indexes of every organization."""
    try:
        org_ids = await all_org_ids()
    except Exception:
        logger.exception("HNSW index maintenance: cannot list organizations")
        return
    for org_id in org_ids:
        try:
            dims = int((await get_org_settings(org_id)).embeddings_dims)
        except Exception:
            logger.exception("HNSW index maintenance: no settings (org=%s)", org_id)
            continue
        await ensure_org_indexes(org_id, dims)
