import asyncio

from src.api import security


class _FakeTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeConn:
    def __init__(self, key_row=None, allowed=None):
        self.key_row = key_row
        self.allowed = allowed or []
        self.bridge_inserts = []
        self.executed = []

    async def fetchrow(self, query, *args):
        return self.key_row

    async def fetchval(self, query, *args):
        self.executed.append((query, args))
        return 77  # new key id

    async def fetch(self, query, *args):
        return [{"project_id": p} for p in self.allowed]

    async def execute(self, query, *args):
        if "mcp_api_key_projects" in query:
            self.bridge_inserts.append(args)
        self.executed.append((query, args))

    def transaction(self):
        return _FakeTx()


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

    monkeypatch.setattr(security, "get_pool", fake_pool)


def test_validate_returns_allowed_projects(monkeypatch):
    row = {"id": 5, "name": "k", "scope": "read", "permissions": "context-read",
           "expires_at": None, "org_id": 1, "project_id": None}
    conn = FakeConn(key_row=row, allowed=[3, 4])
    _patch(monkeypatch, conn)
    info = asyncio.run(security.validate_mcp_api_key("forge_abc"))
    assert info["allowed_projects"] == [3, 4]


def test_create_with_project_ids_writes_bridge(monkeypatch):
    conn = FakeConn()
    _patch(monkeypatch, conn)
    asyncio.run(
        security.create_mcp_api_key(name="agent", org_id=1, permissions="context-read",
                                    project_ids=[3, 4])
    )
    inserted = {args[1] for args in conn.bridge_inserts}
    assert inserted == {3, 4}


def test_create_org_wide_writes_no_bridge(monkeypatch):
    conn = FakeConn()
    _patch(monkeypatch, conn)
    asyncio.run(
        security.create_mcp_api_key(name="wide", org_id=1, permissions="context-read",
                                    project_ids=None)
    )
    assert conn.bridge_inserts == []
