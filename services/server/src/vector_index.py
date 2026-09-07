"""HNSW vector indexes per organization.

The ``embedding`` columns are untyped ``vector`` so each organization can pick
its own model; pgvector cannot index them directly, so every organization gets
a partial index on the typed cast for its own dimension.
"""
from __future__ import annotations

import logging

import asyncpg

from .config import get_settings
from .org_config import all_org_ids
from .org_settings import get_org_settings

logger = logging.getLogger(__name__)

# Tables whose ``embedding`` column is searched by cosine distance.
HNSW_TABLES: tuple[str, ...] = ("repo_chunks", "kb_chunks", "web_chunks")

HNSW_M = 16
HNSW_EF_CONSTRUCTION = 64
HNSW_EF_SEARCH = 100
MAINTENANCE_WORK_MEM = "256MB"


async def _maintenance_connection():
    """Dedicated connection: no command timeout, autocommit, outside the pool."""
    return await asyncpg.connect(get_settings().database_url)


def _maintenance_work_mem() -> str:
    configured = getattr(get_settings(), "hnsw_maintenance_work_mem", "")
    return configured or MAINTENANCE_WORK_MEM


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


# Only the schemas on the current search_path, and the index names of this org
# alone: the '_' of the naming scheme are literal, not LIKE wildcards.
_STALE_INDEX_SQL = (
    "SELECT schemaname, indexname FROM pg_indexes "
    "WHERE schemaname = ANY(current_schemas(false)) AND tablename = $1 "
    "AND indexname LIKE $2 ESCAPE '\\'"
)


# An interrupted CONCURRENTLY build leaves an INVALID index that CREATE INDEX
# IF NOT EXISTS would then skip forever: indisvalid is the only success signal.
_INDEX_VALID_SQL = (
    "SELECT i.indisvalid FROM pg_index i "
    "JOIN pg_class c ON c.oid = i.indexrelid "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE c.relname = $1 AND n.nspname = ANY(current_schemas(false))"
)


async def _index_validity(conn, name: str) -> bool | None:
    """``indisvalid`` of the index, or None when it does not exist."""
    rows = await conn.fetch(_INDEX_VALID_SQL, name)
    return bool(rows[0]["indisvalid"]) if rows else None


def _like_pattern(table: str, org_id: int) -> str:
    """Index-name pattern for one organization, with every literal '_' escaped."""
    return f"{table}_emb_hnsw_org{int(org_id)}_d".replace("_", r"\_") + "%"


def _qualified(schemaname: str, indexname: str) -> str:
    return f'"{schemaname}"."{indexname}"'


async def _org_index_rows(conn, table: str, org_id: int) -> list[tuple[str, str]]:
    rows = await conn.fetch(_STALE_INDEX_SQL, table, _like_pattern(table, org_id))
    return [(r["schemaname"], r["indexname"]) for r in rows]


async def _stale_index_names(conn, table: str, org_id: int, keep: str) -> list[str]:
    """Schema-qualified names of the org's indexes for another dimension."""
    return [
        _qualified(schema, name)
        for schema, name in await _org_index_rows(conn, table, org_id)
        if name != keep
    ]


async def _drop_stale_indexes(conn, table: str, org_id: int, keep: str) -> None:
    try:
        stale = await _stale_index_names(conn, table, org_id, keep)
    except Exception:
        logger.exception("HNSW stale index lookup failed: %s", table)
        return
    for target in stale:
        try:
            await conn.execute(drop_index_sql(target))
        except Exception:
            logger.exception("HNSW stale index drop failed: %s", target)


async def _close(conn, org_id: int) -> None:
    try:
        await conn.close()
    except Exception:
        logger.exception("HNSW maintenance connection close failed (org=%s)", org_id)


async def _build_index(conn, table: str, org_id: int, dims: int) -> bool:
    """Rebuild an invalid leftover, create the index, report whether it is valid."""
    name = index_name(table, org_id, dims)
    try:
        if await _index_validity(conn, name) is False:
            logger.warning("Dropping INVALID HNSW index before rebuild: %s", name)
            await conn.execute(drop_index_sql(name))
        await conn.execute(create_index_sql(table, org_id, dims))
        if await _index_validity(conn, name):
            return True
        logger.warning("HNSW index is not valid after the build: %s", name)
    except Exception:
        logger.exception("HNSW index build failed: %s", name)
    return False


async def ensure_org_indexes(org_id: int, dims: int) -> list[str]:
    """Create the org's HNSW indexes and drop those of another dimension.

    Returns the names that are valid after the run: a build that was cancelled
    or left the index INVALID is reported as missing, not as created.
    """
    created: list[str] = []
    try:
        conn = await _maintenance_connection()
    except Exception:
        logger.exception("HNSW index maintenance: no connection (org=%s)", org_id)
        return created
    try:
        # CONCURRENTLY needs autocommit: never open a transaction here.
        await conn.execute(f"SET maintenance_work_mem = '{_maintenance_work_mem()}'")
        for table in HNSW_TABLES:
            name = index_name(table, org_id, dims)
            # Stale first: two dimensions indexed at once double the build cost.
            await _drop_stale_indexes(conn, table, org_id, name)
            if await _build_index(conn, table, org_id, dims):
                created.append(name)
    except Exception:
        logger.exception("HNSW index maintenance failed (org=%s)", org_id)
    finally:
        await _close(conn, org_id)
    return created


async def drop_org_indexes(org_id: int) -> list[str]:
    """Drop the org's HNSW indexes of every dimension; returns what was dropped.

    A re-embed to a new dimension has to run first: the typed cast of the old
    index rejects a vector of the new width on the very first UPDATE.
    """
    dropped: list[str] = []
    try:
        conn = await _maintenance_connection()
    except Exception:
        logger.exception("HNSW index drop: no connection (org=%s)", org_id)
        return dropped
    try:
        for table in HNSW_TABLES:
            try:
                rows = await _org_index_rows(conn, table, org_id)
            except Exception:
                logger.exception("HNSW index lookup failed: %s", table)
                continue
            for schemaname, indexname in rows:
                target = _qualified(schemaname, indexname)
                try:
                    await conn.execute(drop_index_sql(target))
                    dropped.append(target)
                except Exception:
                    logger.exception("HNSW index drop failed: %s", target)
    finally:
        await _close(conn, org_id)
    return dropped


async def hnsw_indexes_valid(org_id: int, dims: int) -> dict[str, bool]:
    """Per-table validity of the org's HNSW indexes; a missing one counts false."""
    result: dict[str, bool] = {}
    try:
        conn = await _maintenance_connection()
    except Exception:
        logger.exception("HNSW index check: no connection (org=%s)", org_id)
        return result
    try:
        for table in HNSW_TABLES:
            name = index_name(table, org_id, dims)
            result[name] = bool(await _index_validity(conn, name))
    except Exception:
        logger.exception("HNSW index check failed (org=%s)", org_id)
    finally:
        await _close(conn, org_id)
    return result


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
