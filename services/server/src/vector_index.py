"""HNSW vector indexes per organization.

The ``embedding`` columns are untyped ``vector`` so each organization can pick
its own model; pgvector cannot index them directly, so every organization gets
a partial index on the typed cast for its own dimension.
"""
from __future__ import annotations

import logging

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
