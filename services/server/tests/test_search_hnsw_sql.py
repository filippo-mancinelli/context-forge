"""Vector SQL must match the HNSW index expression and raise ef_search."""
import asyncio

import pytest

from src import search
from src.org_settings import OrgSettings
from src.vector_index import search_session_sql


class FakeConn:
    def __init__(self):
        self.executed = []
        self.fetched = []

    async def execute(self, sql, *args):
        self.executed.append(sql)

    async def fetch(self, sql, *args):
        self.fetched.append(sql)
        return []

    def transaction(self):
        class _Tx:
            async def __aenter__(self):
                return None

            async def __aexit__(self, *exc):
                return False

        return _Tx()


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def _patch(monkeypatch, dims=1536, hybrid=True):
    conn = FakeConn()

    async def fake_pool():
        return FakePool(conn)

    async def fake_embed_text(text, org_id):
        return [0.1] * dims

    async def fake_org_settings(org_id):
        return OrgSettings(embeddings_dims=dims)

    monkeypatch.setattr(search, "get_pool", fake_pool)
    monkeypatch.setattr(search, "embed_text", fake_embed_text)
    monkeypatch.setattr(search, "get_org_settings", fake_org_settings)
    monkeypatch.setattr(search, "hybrid_enabled", lambda: hybrid)
    return conn


@pytest.mark.parametrize("hybrid", [True, False])
def test_repo_search_orders_by_the_indexed_expression(monkeypatch, hybrid):
    conn = _patch(monkeypatch, hybrid=hybrid)

    asyncio.run(search.search_repo_chunks(42, "q", project_id=7))

    sql = conn.fetched[0]
    assert "ORDER BY (embedding::vector(1536)) <=> $1::vector(1536)" in sql
    assert "WHERE org_id = $2" in sql
    assert "embedding <=> $1::vector)" not in sql


@pytest.mark.parametrize("hybrid", [True, False])
def test_kb_and_web_search_use_the_aliased_typed_cast(monkeypatch, hybrid):
    conn = _patch(monkeypatch, dims=768, hybrid=hybrid)

    asyncio.run(search.search_kb_chunks(42, "q", project_id=7))
    asyncio.run(search.search_web_chunks(42, "q", project_id=7))

    for sql in conn.fetched:
        assert "ORDER BY (c.embedding::vector(768)) <=> $1::vector(768)" in sql
        assert "WHERE c.org_id = $2" in sql


def test_every_vector_search_sets_ef_search_in_a_transaction(monkeypatch):
    conn = _patch(monkeypatch)

    asyncio.run(search.search_repo_chunks(42, "q", project_id=7))
    asyncio.run(search.search_kb_chunks(42, "q", project_id=7))
    asyncio.run(search.search_web_chunks(42, "q", project_id=7))

    assert conn.executed == [search_session_sql()] * 3
    assert "SET LOCAL hnsw.ef_search = 100" in conn.executed[0]
    assert "SET LOCAL hnsw.iterative_scan = 'relaxed_order'" in conn.executed[0]
    assert "SET LOCAL plan_cache_mode = 'force_custom_plan'" in conn.executed[0]


def test_hybrid_sql_keeps_the_lexical_half_and_the_parameter_layout(monkeypatch):
    sql = search._repo_hybrid_sql(1536)
    assert "websearch_to_tsquery('english', $4)" in sql
    assert "ts_rank_cd(c.content_tsv, tsq.query)" in sql
    assert "LIMIT $6" in sql
    assert "($7::bigint IS NULL OR project_id = $7)" in sql
