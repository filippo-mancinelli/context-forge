# services/server/tests/test_mcp_key_permissions.py
import asyncio

from src.api import security


class _FakeTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeConn:
    def __init__(self, rows=None, fetch_rows=None):
        self.rows = rows or []
        self.fetch_rows = fetch_rows or []
        self.executed = []

    async def fetchrow(self, query, *args):
        self.executed.append((query, args))
        return self.rows.pop(0) if self.rows else None

    async def fetchval(self, query, *args):
        self.executed.append((query, args))
        return 1

    async def fetch(self, query, *args):
        self.executed.append((query, args))
        return self.fetch_rows

    async def execute(self, query, *args):
        self.executed.append((query, args))

    def transaction(self):
        return _FakeTx()


class FakeAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return FakeAcquire(self.conn)


def _patch_pool(monkeypatch, conn):
    async def fake_pool():
        return FakePool(conn)

    monkeypatch.setattr(security, "get_pool", fake_pool)


def test_create_key_stores_explicit_permissions(monkeypatch):
    conn = FakeConn()
    _patch_pool(monkeypatch, conn)
    asyncio.run(
        security.create_mcp_api_key(
            name="ci-bot", scope="read", org_id=1, permissions="db-query,jobs"
        )
    )
    insert = next(e for e in conn.executed if "INSERT INTO mcp_api_keys" in e[0])
    assert "permissions" in insert[0]
    assert "db-query,jobs" in insert[1]


def test_create_key_derives_permissions_from_scope(monkeypatch):
    conn = FakeConn()
    _patch_pool(monkeypatch, conn)
    asyncio.run(security.create_mcp_api_key(name="ci-bot", scope="read,write", org_id=1))
    insert = next(e for e in conn.executed if "INSERT INTO mcp_api_keys" in e[0])
    assert "context-read,context-write" in insert[1]


def test_validate_key_returns_permissions(monkeypatch):
    row = {
        "id": 5,
        "name": "k",
        "scope": "read",
        "permissions": "context-read",
        "expires_at": None,
        "org_id": 1,
    }
    conn = FakeConn(rows=[row])
    _patch_pool(monkeypatch, conn)
    info = asyncio.run(security.validate_mcp_api_key("forge_abc"))
    assert info["permissions"] == "context-read"


# ===== update_mcp_api_key_rate_limit: SQL shape and the UPDATE-tag-derived bool =====

from tests.fake_db import FakeConn as FakeDbConn
from tests.fake_db import FakePool as FakeDbPool


def _patch_pool_db(monkeypatch, conn):
    async def fake_pool():
        return FakeDbPool(conn)

    monkeypatch.setattr(security, "get_pool", fake_pool)


def test_update_rate_limit_returns_true_on_success(monkeypatch):
    conn = FakeDbConn(execute_result="UPDATE 1")
    _patch_pool_db(monkeypatch, conn)
    ok = asyncio.run(security.update_mcp_api_key_rate_limit(5, 1, 30))
    assert ok is True
    sql, args = conn.executed[0]
    assert "UPDATE mcp_api_keys SET rate_limit_per_minute" in sql
    assert args == (30, 5, 1)


def test_update_rate_limit_accepts_null_to_clear(monkeypatch):
    conn = FakeDbConn(execute_result="UPDATE 1")
    _patch_pool_db(monkeypatch, conn)
    asyncio.run(security.update_mcp_api_key_rate_limit(5, 1, None))
    _sql, args = conn.executed[0]
    assert args == (None, 5, 1)


def test_update_rate_limit_returns_false_when_no_row_matched(monkeypatch):
    conn = FakeDbConn(execute_result="UPDATE 0")
    _patch_pool_db(monkeypatch, conn)
    ok = asyncio.run(security.update_mcp_api_key_rate_limit(5, 1, None))
    assert ok is False


# ===== create/list also carry rate_limit_per_minute through the SQL =====

def test_create_key_stores_the_rate_limit(monkeypatch):
    conn = FakeConn()
    _patch_pool(monkeypatch, conn)
    asyncio.run(
        security.create_mcp_api_key(name="ci-bot", org_id=1, permissions="db-query", rate_limit_per_minute=30)
    )
    insert = next(e for e in conn.executed if "INSERT INTO mcp_api_keys" in e[0])
    assert "rate_limit_per_minute" in insert[0]
    assert insert[1][-1] == 30


def test_list_keys_selects_the_rate_limit(monkeypatch):
    conn = FakeDbConn()
    _patch_pool_db(monkeypatch, conn)
    asyncio.run(security.list_mcp_api_keys(org_id=1))
    sql, _args = conn.executed[0]
    assert "k.rate_limit_per_minute" in sql
