"""job_submit / job_status / job_result under the retry model."""
import asyncio
import datetime as dt
import inspect
import json

import pytest

from src.mcp import jobs as jobs_tools


def _fn(tool):
    """`@mcp.tool()` returns the function itself here; `.fn` is the fallback if that changes."""
    return getattr(tool, "fn", tool)


class FakeConn:
    def __init__(self, row=None):
        self.row = row
        self.executed = []

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        return "INSERT 0 1"

    async def fetchrow(self, sql, *args):
        return self.row


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


@pytest.fixture
def wired(monkeypatch):
    conn = FakeConn()

    async def fake_pool():
        return FakePool(conn)

    async def fake_org():
        return 1

    async def fake_project():
        return 42

    monkeypatch.setattr(jobs_tools, "get_pool", fake_pool)
    monkeypatch.setattr("src.mcp.context.resolve_org_id", fake_org)
    monkeypatch.setattr("src.mcp.context.require_project_id", fake_project)
    return conn


NOW = dt.datetime(2026, 9, 7, 10, 0, 0, tzinfo=dt.timezone.utc)


# ── job_submit ────────────────────────────────────────────────────────────────

def test_submit_only_inserts_and_never_starts_a_task(wired):
    out = asyncio.run(_fn(jobs_tools.job_submit)(url="http://svc/api"))
    assert out["status"] == "ok"
    assert "job_id" in out
    sql, _args = wired.executed[0]
    assert "INSERT INTO jobs" in sql
    assert "asyncio.create_task" not in inspect.getsource(_fn(jobs_tools.job_submit))


def test_submit_persists_max_attempts(wired):
    asyncio.run(_fn(jobs_tools.job_submit)(url="http://svc/api", max_attempts=7))
    sql, args = wired.executed[0]
    assert "max_attempts" in sql
    # args = (job_id, params_json, org_id, project_id, max_attempts)
    assert args[4] == 7


def test_submit_defaults_to_three_attempts(wired):
    asyncio.run(_fn(jobs_tools.job_submit)(url="http://svc/api"))
    _sql, args = wired.executed[0]
    assert args[4] == 3


@pytest.mark.parametrize("bad", [0, -1, 11, 100])
def test_submit_rejects_out_of_range_max_attempts(wired, bad):
    out = asyncio.run(_fn(jobs_tools.job_submit)(url="http://svc/api", max_attempts=bad))
    assert out["status"] == "error"
    assert "max_attempts" in out["error"]
    assert wired.executed == []


# ── job_status ────────────────────────────────────────────────────────────────

def _status_row(status="pending", attempts=1):
    return {
        "id": "j1",
        "status": status,
        "error_message": None,
        "attempts": attempts,
        "max_attempts": 3,
        "next_attempt_at": NOW,
        "last_error": "HTTP 503",
        "created_at": NOW,
        "updated_at": NOW,
    }


def test_status_reports_attempt_counters(monkeypatch):
    conn = FakeConn(row=_status_row(attempts=2))

    async def fake_pool():
        return FakePool(conn)

    async def fake_project():
        return 7

    monkeypatch.setattr(jobs_tools, "get_pool", fake_pool)
    monkeypatch.setattr("src.mcp.context.require_project_id", fake_project)

    out = asyncio.run(_fn(jobs_tools.job_status)("j1"))
    assert out["attempts"] == 2
    assert out["max_attempts"] == 3
    assert out["next_attempt_at"] == NOW.isoformat()
    assert out["job_status"] == "pending"
    assert out["last_error"] == "HTTP 503"


def test_status_surfaces_the_dead_state(monkeypatch):
    conn = FakeConn(row=_status_row(status="dead", attempts=3))

    async def fake_pool():
        return FakePool(conn)

    async def fake_project():
        return 7

    monkeypatch.setattr(jobs_tools, "get_pool", fake_pool)
    monkeypatch.setattr("src.mcp.context.require_project_id", fake_project)

    out = asyncio.run(_fn(jobs_tools.job_status)("j1"))
    assert out["job_status"] == "dead"


# ── job_result ────────────────────────────────────────────────────────────────

def _result_row(status, result=None):
    return {
        "id": "j1",
        "status": status,
        "result": result,
        "error_message": "HTTP 503",
        "last_error": "HTTP 503",
        "attempts": 3,
        "max_attempts": 3,
    }


def _run_result(monkeypatch, row):
    conn = FakeConn(row=row)

    async def fake_pool():
        return FakePool(conn)

    async def fake_project():
        return 7

    monkeypatch.setattr(jobs_tools, "get_pool", fake_pool)
    monkeypatch.setattr("src.mcp.context.require_project_id", fake_project)
    return asyncio.run(_fn(jobs_tools.job_result)("j1"))


def test_result_of_a_dead_job_is_an_error_with_the_attempt_count(monkeypatch):
    out = _run_result(monkeypatch, _result_row("dead"))
    assert out["status"] == "error"
    assert out["job_status"] == "dead"
    assert out["error"] == "HTTP 503"
    assert out["attempts"] == 3


def test_result_of_a_pending_job_is_not_ready(monkeypatch):
    out = _run_result(monkeypatch, _result_row("pending"))
    assert out["status"] == "not_ready"


def test_result_of_a_done_job_decodes_json(monkeypatch):
    out = _run_result(monkeypatch, _result_row("done", json.dumps({"a": 1})))
    assert out["status"] == "ok"
    assert out["result"] == {"a": 1}
