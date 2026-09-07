"""Audit dei tool MCP: redazione argomenti, esiti, coda e writer."""
import asyncio
import json

import pytest

from src import metrics
from src.mcp import audit
from src.mcp.context import Principal, set_current_principal
from tests.fake_db import FakeConn, FakePool


def test_summarize_args_truncates_long_strings():
    out = audit.summarize_args({"query": "x" * 500})
    assert len(out["query"]) == 200


def test_summarize_args_redacts_secrets():
    out = audit.summarize_args(
        {"password": "hunter2", "api_token": "t", "private_key": "k", "content": "body"}
    )
    assert out == {
        "password": "<redacted>",
        "api_token": "<redacted>",
        "private_key": "<redacted>",
        "content": "<redacted>",
    }


def test_summarize_args_keeps_sql_truncated():
    out = audit.summarize_args({"sql": "SELECT " + "a" * 500})
    assert out["sql"].startswith("SELECT ")
    assert len(out["sql"]) == 200


def test_summarize_args_collapses_containers():
    out = audit.summarize_args({"opts": {"a": 1, "b": 2, "c": 3}, "items": [1, 2, 3, 4, 5]})
    assert out == {"opts": "<dict:3>", "items": "<list:5>"}


def test_summarize_args_keeps_scalars():
    out = audit.summarize_args({"limit": 10, "flag": True, "missing": None})
    assert out == {"limit": 10, "flag": True, "missing": None}


def test_classify_outcome():
    assert audit.classify_outcome(Exception("Access denied: tool requires permission 'jobs'")) == "denied"
    assert audit.classify_outcome(Exception("Rate limit exceeded: 5 calls per minute for this API key")) == "rate_limited"
    assert audit.classify_outcome(ValueError("boom")) == "error"


def _drain():
    rows = []
    while True:
        try:
            rows.append(audit._queue.get_nowait())
        except asyncio.QueueEmpty:
            return rows


def test_audited_records_ok():
    _drain()
    set_current_principal(Principal(kind="api_key", id=3, label="ci-bot"))

    async def scenario():
        async with audit.audited("repo_search", "context-read", {"query": "abc"}):
            return 1

    try:
        assert asyncio.run(scenario()) == 1
    finally:
        set_current_principal(None)

    row = _drain()[0]
    assert row["tool"] == "repo_search"
    assert row["permission"] == "context-read"
    assert row["outcome"] == "ok"
    assert row["principal_kind"] == "api_key"
    assert row["principal_id"] == 3
    assert row["principal"] == "ci-bot"
    assert row["error"] is None
    assert json.loads(row["args_summary"]) == {"query": "abc"}
    assert isinstance(row["duration_ms"], int) and row["duration_ms"] >= 0


def _sample(name, labels=None):
    for metric in metrics.registry.collect():
        for s in metric.samples:
            if s.name == name and (labels is None or s.labels == labels):
                return s.value
    return None


def test_audited_increments_the_prometheus_counter():
    """A rename of the counter must fail this suite, not silently no-op."""
    _drain()
    set_current_principal(Principal(kind="api_key", id=9, label="ci-bot"))

    before = _sample(
        "contextforge_mcp_tool_calls_total", {"tool": "repo_search_metric", "outcome": "ok"}
    ) or 0.0

    async def scenario():
        async with audit.audited("repo_search_metric", "context-read", {}):
            return 1

    try:
        asyncio.run(scenario())
    finally:
        set_current_principal(None)

    after = _sample(
        "contextforge_mcp_tool_calls_total", {"tool": "repo_search_metric", "outcome": "ok"}
    )
    assert after == before + 1.0


@pytest.mark.parametrize(
    "exc,expected",
    [
        (Exception("Access denied: tool requires permission 'jobs'"), "denied"),
        (Exception("Rate limit exceeded: 5 calls per minute for this API key"), "rate_limited"),
        (ValueError("boom"), "error"),
    ],
)
def test_audited_records_failures_and_reraises(exc, expected):
    _drain()

    async def scenario():
        async with audit.audited("job_submit", "jobs", {}):
            raise exc

    with pytest.raises(type(exc)):
        asyncio.run(scenario())

    row = _drain()[0]
    assert row["outcome"] == expected
    assert row["error"]


def test_queue_drops_oldest_when_full(monkeypatch):
    _drain()
    monkeypatch.setattr(audit, "_queue", asyncio.Queue(maxsize=2))

    async def scenario():
        for i in range(3):
            await audit.record_call(
                tool=f"t{i}", permission=None, outcome="ok", duration_ms=1
            )

    asyncio.run(scenario())
    rows = []
    while not audit._queue.empty():
        rows.append(audit._queue.get_nowait())
    assert [r["tool"] for r in rows] == ["t1", "t2"]


def test_flush_once_writes_a_batch(monkeypatch):
    _drain()
    conn = FakeConn()

    async def fake_pool():
        return FakePool(conn)

    monkeypatch.setattr(audit, "get_pool", fake_pool)

    async def scenario():
        await audit.record_call(tool="a", permission=None, outcome="ok", duration_ms=1)
        await audit.record_call(tool="b", permission="jobs", outcome="error", duration_ms=2)
        return await audit.flush_once()

    assert asyncio.run(scenario()) == 2
    sql, args = conn.executed[0]
    assert "INSERT INTO mcp_tool_calls" in sql
    assert "unnest(" in sql
    assert len(args) == 11
    assert args[5] == ["a", "b"]          # tool column
    assert args[8] == [1, 2]              # duration_ms column


def test_flush_once_never_raises_when_the_insert_fails(monkeypatch):
    _drain()
    conn = FakeConn(execute_error=RuntimeError("insert failed"))

    async def fake_pool():
        return FakePool(conn)

    monkeypatch.setattr(audit, "get_pool", fake_pool)

    async def scenario():
        await audit.record_call(tool="a", permission=None, outcome="ok", duration_ms=1)
        return await audit.flush_once()

    assert asyncio.run(scenario()) == 0


def test_requires_permission_still_denies_and_audits():
    from src.mcp import permissions as perms

    _drain()

    @perms.requires_permission("jobs")
    async def fake_tool(x: int = 1) -> int:
        return x

    perms.set_current_permissions(frozenset({"context-read"}))
    try:
        with pytest.raises(Exception, match="jobs"):
            asyncio.run(fake_tool(x=2))
    finally:
        perms.set_current_permissions(None)

    row = _drain()[0]
    assert row["tool"] == "fake_tool"
    assert row["outcome"] == "denied"
    assert row["permission"] == "jobs"


def test_audit_only_records_ungated_tool():
    from src.mcp import permissions as perms

    _drain()

    @perms.audit_only
    async def current_project() -> dict:
        return {"selected_project_id": None}

    assert asyncio.run(current_project()) == {"selected_project_id": None}
    row = _drain()[0]
    assert row["tool"] == "current_project"
    assert row["permission"] is None
    assert row["outcome"] == "ok"
