"""Naming and DDL text of the per-organization HNSW indexes."""
import pytest

from src.vector_index import (
    HNSW_EF_CONSTRUCTION,
    HNSW_EF_SEARCH,
    HNSW_M,
    HNSW_TABLES,
    MAINTENANCE_WORK_MEM,
    create_index_sql,
    drop_index_sql,
    index_name,
    vector_expr,
)


def test_index_name_follows_the_table_org_dims_pattern():
    assert index_name("repo_chunks", 42, 1536) == "repo_chunks_emb_hnsw_org42_d1536"
    assert index_name("kb_chunks", 1, 768) == "kb_chunks_emb_hnsw_org1_d768"


def test_vector_expr_is_the_typed_cast_used_by_index_and_queries():
    assert vector_expr(1536) == "(embedding::vector(1536))"
    assert vector_expr(1024, "c.embedding") == "(c.embedding::vector(1024))"


def test_create_index_sql_is_concurrent_partial_and_typed():
    assert create_index_sql("repo_chunks", 42, 1536) == (
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS repo_chunks_emb_hnsw_org42_d1536 "
        "ON repo_chunks USING hnsw ((embedding::vector(1536)) vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64) "
        "WHERE org_id = 42"
    )


def test_create_index_sql_rejects_a_table_outside_the_allowlist():
    # The table name is interpolated into DDL: only the three known tables pass.
    with pytest.raises(ValueError):
        create_index_sql("organizations", 1, 1536)


def test_drop_index_sql_is_concurrent_and_idempotent():
    assert drop_index_sql("kb_chunks_emb_hnsw_org1_d768") == (
        "DROP INDEX CONCURRENTLY IF EXISTS kb_chunks_emb_hnsw_org1_d768"
    )


def test_constants():
    assert HNSW_TABLES == ("repo_chunks", "kb_chunks", "web_chunks")
    assert (HNSW_M, HNSW_EF_CONSTRUCTION, HNSW_EF_SEARCH) == (16, 64, 100)
    assert MAINTENANCE_WORK_MEM == "256MB"
