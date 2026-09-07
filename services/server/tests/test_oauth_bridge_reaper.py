"""Reaper dei flow OAuth scaduti: niente ritenzione di token in chiaro oltre il TTL."""
import asyncio
import inspect

from src import scheduler
from src.mcp import oauth_bridge


class FakeConn:
    def __init__(self, execute_result):
        self.execute_result = execute_result
        self.executed = []

    async def execute(self, query, *args):
        self.executed.append((query, args))
        return self.execute_result


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


def test_purge_expired_flows_parses_deleted_count(monkeypatch):
    conn = FakeConn(execute_result="DELETE 3")

    async def fake_pool():
        return FakePool(conn)

    monkeypatch.setattr(oauth_bridge, "get_pool", fake_pool)

    deleted = asyncio.run(oauth_bridge.purge_expired_flows())

    assert deleted == 3
    query, _args = conn.executed[0]
    assert "DELETE FROM oauth_bridge_flows" in query
    assert "expires_at" in query


def test_purge_expired_flows_no_rows_deleted(monkeypatch):
    conn = FakeConn(execute_result="DELETE 0")

    async def fake_pool():
        return FakePool(conn)

    monkeypatch.setattr(oauth_bridge, "get_pool", fake_pool)

    deleted = asyncio.run(oauth_bridge.purge_expired_flows())

    assert deleted == 0


def test_purge_expired_flows_unparsable_result_returns_zero(monkeypatch):
    conn = FakeConn(execute_result="")

    async def fake_pool():
        return FakePool(conn)

    monkeypatch.setattr(oauth_bridge, "get_pool", fake_pool)

    deleted = asyncio.run(oauth_bridge.purge_expired_flows())

    assert deleted == 0


def test_scheduler_registers_oauth_flow_reaper_job():
    """Il job va registrato globalmente allo startup dello scheduler (non per-org)."""
    source = inspect.getsource(scheduler)
    assert "purge_expired_flows" in source
    assert "oauth_flow_reaper" in source
