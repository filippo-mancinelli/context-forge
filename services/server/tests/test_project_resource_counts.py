import asyncio

from src import projects


class FakeConn:
    """Ritorna un conteggio per tabella, in base al nome che compare nella query."""

    def __init__(self, counts):
        self.counts = counts
        self.queries = []

    async def fetchval(self, query, *args):
        self.queries.append(query)
        for table, value in self.counts.items():
            if f"FROM {table} " in query or query.rstrip().endswith(f"FROM {table}"):
                return value
        return 0


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


def _patch(monkeypatch, conn):
    async def fake_pool():
        return FakePool(conn)

    monkeypatch.setattr(projects, "get_pool", fake_pool)


def test_empty_project_returns_no_entries(monkeypatch):
    conn = FakeConn({})
    _patch(monkeypatch, conn)
    assert asyncio.run(projects.count_project_resources(7)) == {}


def test_only_non_zero_resources_are_reported(monkeypatch):
    conn = FakeConn({"repos": 2, "kb_documents": 1})
    _patch(monkeypatch, conn)
    counts = asyncio.run(projects.count_project_resources(7))
    assert counts == {"repos": 2, "kb_documents": 1}


def test_every_scoped_table_is_checked(monkeypatch):
    """Se una tabella scoped sfugge al conteggio, il delete lascerebbe righe orfane."""
    conn = FakeConn({})
    _patch(monkeypatch, conn)
    asyncio.run(projects.count_project_resources(7))
    joined = " ".join(conn.queries)
    for table in ("repos", "kb_documents", "web_sites", "api_contracts", "chat_sessions", "jobs", "mcp_api_keys"):
        assert f"FROM {table} " in joined
    # Cartelle SSH e database appartengono al catalogo: le selezioni cadono con il progetto.
    assert "FROM ssh_sources " not in joined
    assert "FROM db_connections " not in joined
