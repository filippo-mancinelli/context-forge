"""Retention dell'audit: cancella le righe più vecchie della finestra."""
import asyncio
import inspect

from src import config, scheduler
from src.mcp import audit
from tests.fake_db import FakeConn, FakePool


def _patch(monkeypatch, conn):
    async def fake_pool():
        return FakePool(conn)

    monkeypatch.setattr(audit, "get_pool", fake_pool)


def test_default_retention_is_90_days():
    assert config.get_settings().mcp_audit_retention_days == 90


def test_purge_uses_the_configured_window(monkeypatch):
    conn = FakeConn(execute_result="DELETE 4")
    _patch(monkeypatch, conn)
    monkeypatch.setattr(config.get_settings(), "mcp_audit_retention_days", 7, raising=False)

    deleted = asyncio.run(audit.purge_old_calls())

    assert deleted == 4
    sql, args = conn.executed[0]
    assert "DELETE FROM mcp_tool_calls" in sql
    assert "make_interval(days => $1)" in sql
    assert args == (7,)


def test_purge_disabled_when_retention_is_zero(monkeypatch):
    conn = FakeConn()
    _patch(monkeypatch, conn)
    monkeypatch.setattr(config.get_settings(), "mcp_audit_retention_days", 0, raising=False)

    assert asyncio.run(audit.purge_old_calls()) == 0
    assert conn.executed == []


def test_purge_never_raises(monkeypatch):
    conn = FakeConn(execute_error=RuntimeError("db down"))
    _patch(monkeypatch, conn)
    monkeypatch.setattr(config.get_settings(), "mcp_audit_retention_days", 30, raising=False)
    assert asyncio.run(audit.purge_old_calls()) == 0


def test_scheduler_wrapper_calls_the_purge(monkeypatch):
    calls = []

    async def fake_purge():
        calls.append(True)
        return 3

    monkeypatch.setattr(scheduler, "purge_old_tool_calls", fake_purge, raising=False)
    asyncio.run(scheduler._purge_old_tool_calls())
    assert calls == [True]


def test_scheduler_registers_the_retention_job():
    source = inspect.getsource(scheduler.start_scheduler)
    assert "_purge_old_tool_calls" in source
    assert 'id="mcp_audit_retention"' in source
    assert "hours=24" in source
