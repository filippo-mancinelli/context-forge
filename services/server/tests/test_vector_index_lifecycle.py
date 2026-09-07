"""ensure_org_indexes / ensure_all_indexes against a fake connection (no live DB)."""
import asyncio
from types import SimpleNamespace

from src import vector_index
from src.org_settings import OrgSettings


class FakeConn:
    """Records every statement; ``fetch`` answers the pg_indexes lookup."""

    def __init__(self, stale=None, fail_on=()):
        self.executed = []
        self.stale = stale or {}
        self.fail_on = fail_on
        self.closed = False

    async def execute(self, sql, *args):
        self.executed.append(sql)
        if any(token in sql for token in self.fail_on):
            raise RuntimeError("boom")

    async def fetch(self, sql, *args):
        table = args[0]
        return [
            {"schemaname": "public", "indexname": name}
            for name in self.stale.get(table, [])
        ]

    async def close(self):
        self.closed = True


def _patch_conn(monkeypatch, conn):
    async def fake_connection():
        return conn

    monkeypatch.setattr(vector_index, "_maintenance_connection", fake_connection)


def test_creates_one_index_per_table_after_raising_work_mem(monkeypatch):
    conn = FakeConn()
    _patch_conn(monkeypatch, conn)

    created = asyncio.run(vector_index.ensure_org_indexes(42, 1536))

    assert created == [
        "repo_chunks_emb_hnsw_org42_d1536",
        "kb_chunks_emb_hnsw_org42_d1536",
        "web_chunks_emb_hnsw_org42_d1536",
    ]
    assert conn.executed[0] == "SET maintenance_work_mem = '256MB'"
    creates = [s for s in conn.executed if s.startswith("CREATE INDEX CONCURRENTLY")]
    assert len(creates) == 3
    assert "(embedding::vector(1536)) vector_cosine_ops" in creates[0]
    assert creates[0].endswith("WHERE org_id = 42")


def test_maintenance_work_mem_is_read_from_the_settings(monkeypatch):
    conn = FakeConn()
    _patch_conn(monkeypatch, conn)
    monkeypatch.setattr(
        vector_index,
        "get_settings",
        lambda: SimpleNamespace(hnsw_maintenance_work_mem="1GB"),
    )

    asyncio.run(vector_index.ensure_org_indexes(42, 1536))

    assert conn.executed[0] == "SET maintenance_work_mem = '1GB'"


def test_the_maintenance_connection_is_closed_on_success(monkeypatch):
    conn = FakeConn()
    _patch_conn(monkeypatch, conn)

    asyncio.run(vector_index.ensure_org_indexes(42, 1536))

    assert conn.closed is True


def test_the_maintenance_connection_is_closed_when_a_statement_explodes(monkeypatch):
    conn = FakeConn(fail_on=("SET maintenance_work_mem",))
    _patch_conn(monkeypatch, conn)

    assert asyncio.run(vector_index.ensure_org_indexes(42, 1536)) == []
    assert conn.closed is True


def test_drops_only_the_stale_dimension_of_the_same_org(monkeypatch):
    conn = FakeConn(stale={"repo_chunks": [
        "repo_chunks_emb_hnsw_org42_d1536",
        "repo_chunks_emb_hnsw_org42_d768",
    ]})
    _patch_conn(monkeypatch, conn)

    asyncio.run(vector_index.ensure_org_indexes(42, 1536))

    drops = [s for s in conn.executed if s.startswith("DROP INDEX CONCURRENTLY")]
    assert drops == [
        'DROP INDEX CONCURRENTLY IF EXISTS "public"."repo_chunks_emb_hnsw_org42_d768"'
    ]


def test_a_failing_build_is_swallowed_and_the_others_still_run(monkeypatch):
    conn = FakeConn(fail_on=("kb_chunks_emb_hnsw_org7_d1536",))
    _patch_conn(monkeypatch, conn)

    created = asyncio.run(vector_index.ensure_org_indexes(7, 1536))

    assert created == [
        "repo_chunks_emb_hnsw_org7_d1536",
        "web_chunks_emb_hnsw_org7_d1536",
    ]


def test_a_broken_connection_never_reaches_the_caller(monkeypatch):
    async def boom():
        raise RuntimeError("no database")

    monkeypatch.setattr(vector_index, "_maintenance_connection", boom)

    assert asyncio.run(vector_index.ensure_org_indexes(1, 1536)) == []


def test_ensure_all_indexes_uses_each_organization_dimension(monkeypatch):
    calls = []

    async def fake_all_org_ids():
        return [1, 2]

    async def fake_get_org_settings(org_id):
        return OrgSettings(embeddings_dims=1536 if org_id == 1 else 768)

    async def fake_ensure(org_id, dims):
        calls.append((org_id, dims))
        return []

    monkeypatch.setattr(vector_index, "all_org_ids", fake_all_org_ids)
    monkeypatch.setattr(vector_index, "get_org_settings", fake_get_org_settings)
    monkeypatch.setattr(vector_index, "ensure_org_indexes", fake_ensure)

    asyncio.run(vector_index.ensure_all_indexes())

    assert calls == [(1, 1536), (2, 768)]
