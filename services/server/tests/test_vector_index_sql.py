"""Naming and DDL text of the per-organization HNSW indexes."""
import pytest

from src.config import Settings
from src.vector_index import (
    HNSW_EF_CONSTRUCTION,
    HNSW_EF_SEARCH,
    HNSW_ITERATIVE_SCAN,
    HNSW_M,
    PLAN_CACHE_MODE,
    HNSW_TABLES,
    MAINTENANCE_WORK_MEM,
    _like_pattern,
    _STALE_INDEX_SQL,
    create_index_sql,
    drop_index_sql,
    index_name,
    search_session_sql,
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


def test_the_index_lookup_is_restricted_to_the_search_path_schemas():
    assert "schemaname = ANY(current_schemas(false))" in _STALE_INDEX_SQL
    assert r"LIKE $2 ESCAPE '\'" in _STALE_INDEX_SQL


def test_the_like_pattern_escapes_every_literal_underscore():
    assert _like_pattern("repo_chunks", 42) == r"repo\_chunks\_emb\_hnsw\_org42\_d%"


def test_maintenance_work_mem_is_configurable_and_defaults_to_the_constant():
    assert Settings().hnsw_maintenance_work_mem == MAINTENANCE_WORK_MEM


def test_search_session_sql_renders_the_three_knobs_as_one_statement():
    assert (HNSW_ITERATIVE_SCAN, PLAN_CACHE_MODE) == ("relaxed_order", "force_custom_plan")
    assert search_session_sql() == (
        "SET LOCAL hnsw.ef_search = 100; "
        "SET LOCAL hnsw.iterative_scan = 'relaxed_order'; "
        "SET LOCAL plan_cache_mode = 'force_custom_plan'"
    )
