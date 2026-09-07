"""ensure_org_indexes / ensure_all_indexes against a fake connection (no live DB)."""
import asyncio
from types import SimpleNamespace

from src import vector_index
from src.org_settings import OrgSettings


class FakeConn:
    """Records every statement; ``fetch`` answers the two catalog lookups.

    ``valid`` maps an index name to the answers of the pg_index validity query,
    consumed in order (the last one repeats): True, False, or None for "no row".
    """

    def __init__(self, stale=None, fail_on=(), valid=None):
        self.executed = []
        self.stale = stale or {}
        self.fail_on = fail_on
        self.valid = {k: list(v) for k, v in (valid or {}).items()}
        self.closed = False

    async def execute(self, sql, *args):
        self.executed.append(sql)
        if any(token in sql for token in self.fail_on):
            raise RuntimeError("boom")

    def _next_validity(self, name):
        answers = self.valid.get(name)
        if not answers:
            return True
        return answers.pop(0) if len(answers) > 1 else answers[0]

    async def fetch(self, sql, *args):
        if "indisvalid" in sql:
            answer = self._next_validity(args[0])
            return [] if answer is None else [{"indisvalid": answer, "nspname": "public"}]
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


def test_the_stale_dimension_goes_before_the_build_of_the_new_one(monkeypatch):
    # Both indexes on one table at once would double the memory of the build.
    conn = FakeConn(stale={"repo_chunks": ["repo_chunks_emb_hnsw_org42_d768"]})
    _patch_conn(monkeypatch, conn)

    asyncio.run(vector_index.ensure_org_indexes(42, 1536))

    drop = 'DROP INDEX CONCURRENTLY IF EXISTS "public"."repo_chunks_emb_hnsw_org42_d768"'
    create = next(
        s for s in conn.executed
        if s.startswith("CREATE INDEX CONCURRENTLY IF NOT EXISTS repo_chunks")
    )
    assert conn.executed.index(drop) < conn.executed.index(create)


def test_drop_org_indexes_removes_every_dimension_of_the_organization(monkeypatch):
    conn = FakeConn(stale={
        "repo_chunks": ["repo_chunks_emb_hnsw_org42_d1536"],
        "kb_chunks": ["kb_chunks_emb_hnsw_org42_d768"],
    })
    _patch_conn(monkeypatch, conn)

    dropped = asyncio.run(vector_index.drop_org_indexes(42))

    assert dropped == [
        '"public"."repo_chunks_emb_hnsw_org42_d1536"',
        '"public"."kb_chunks_emb_hnsw_org42_d768"',
    ]
    assert conn.executed == [f"DROP INDEX CONCURRENTLY IF EXISTS {n}" for n in dropped]
    assert conn.closed is True


def test_drop_org_indexes_keeps_the_index_already_at_the_target_dimension(monkeypatch):
    conn = FakeConn(stale={
        "repo_chunks": [
            "repo_chunks_emb_hnsw_org42_d1536",
            "repo_chunks_emb_hnsw_org42_d768",
        ],
    })
    _patch_conn(monkeypatch, conn)

    dropped = asyncio.run(vector_index.drop_org_indexes(42, keep_dims=1536))

    assert dropped == ['"public"."repo_chunks_emb_hnsw_org42_d768"']
    assert conn.executed == [f"DROP INDEX CONCURRENTLY IF EXISTS {n}" for n in dropped]


def test_drop_org_indexes_survives_a_broken_connection(monkeypatch):
    async def boom():
        raise RuntimeError("no database")

    monkeypatch.setattr(vector_index, "_maintenance_connection", boom)

    assert asyncio.run(vector_index.drop_org_indexes(42)) == []


def test_an_invalid_leftover_is_dropped_before_the_index_is_rebuilt(monkeypatch):
    name = "repo_chunks_emb_hnsw_org42_d1536"
    conn = FakeConn(valid={name: [False, True]})
    _patch_conn(monkeypatch, conn)

    created = asyncio.run(vector_index.ensure_org_indexes(42, 1536))

    drop = f'DROP INDEX CONCURRENTLY IF EXISTS "public"."{name}"'
    create = [s for s in conn.executed if s.startswith(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name}")]
    assert drop in conn.executed
    assert conn.executed.index(drop) < conn.executed.index(create[0])
    assert name in created


def test_a_build_that_leaves_the_index_invalid_is_not_reported_as_created(monkeypatch):
    name = "kb_chunks_emb_hnsw_org42_d1536"
    conn = FakeConn(valid={name: [None, False]})
    _patch_conn(monkeypatch, conn)

    created = asyncio.run(vector_index.ensure_org_indexes(42, 1536))

    assert name not in created
    assert created == [
        "repo_chunks_emb_hnsw_org42_d1536",
        "web_chunks_emb_hnsw_org42_d1536",
    ]
    # Nothing to drop: the pre-check found no index at all.
    assert f"DROP INDEX CONCURRENTLY IF EXISTS {name}" not in conn.executed


def test_hnsw_indexes_valid_reports_one_flag_per_table(monkeypatch):
    conn = FakeConn(valid={"kb_chunks_emb_hnsw_org42_d1536": [False]})
    _patch_conn(monkeypatch, conn)

    assert asyncio.run(vector_index.hnsw_indexes_valid(42, 1536)) == {
        "repo_chunks_emb_hnsw_org42_d1536": True,
        "kb_chunks_emb_hnsw_org42_d1536": False,
        "web_chunks_emb_hnsw_org42_d1536": True,
    }
    assert conn.closed is True


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
