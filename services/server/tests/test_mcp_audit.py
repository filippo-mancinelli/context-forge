"""Audit dei tool MCP: redazione argomenti, esiti, coda e writer."""
import asyncio
import contextlib
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


class _FakeContext:
    """Stand-in for FastMCP's injected Context object (list_projects, use_project,
    current_project, create_project all declare ``ctx: Context``)."""


def test_summarize_args_names_non_primitive_values():
    out = audit.summarize_args({"ctx": _FakeContext(), "limit": 5})
    assert out == {"ctx": "<_FakeContext>", "limit": 5}


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


def test_audited_records_a_call_with_a_non_primitive_kwarg():
    """A kwarg that summarize_args can't reduce to a JSON primitive (e.g. the
    Context FastMCP injects into ctx: Context tools) must not make json.dumps
    raise inside record_call and silently drop the row."""
    _drain()

    async def scenario():
        args_summary = audit.summarize_args({"ctx": _FakeContext()})
        async with audit.audited("current_project", None, args_summary):
            return {"selected_project_id": None}

    asyncio.run(scenario())

    rows = _drain()
    assert len(rows) == 1
    assert json.loads(rows[0]["args_summary"]) == {"ctx": "<_FakeContext>"}


def test_audited_with_no_kwargs_stores_an_empty_object():
    _drain()

    async def scenario():
        async with audit.audited("list_projects", None, audit.summarize_args({})):
            return {"projects": []}

    asyncio.run(scenario())

    row = _drain()[0]
    assert row["args_summary"] == "{}"


def test_record_call_args_summary_uses_default_str_as_a_safety_net():
    """Even if a caller bypasses summarize_args and passes a raw
    non-JSON-serialisable value, record_call must not drop the row."""
    _drain()

    class Weird:
        def __str__(self):
            return "weird!"

    async def scenario():
        await audit.record_call(
            tool="x", permission=None, outcome="ok", duration_ms=1,
            args_summary={"raw": Weird()},
        )

    asyncio.run(scenario())

    row = audit._queue.get_nowait()
    assert json.loads(row["args_summary"]) == {"raw": "weird!"}


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


def test_writer_loop_caps_batches_at_batch_size(monkeypatch):
    """_writer_loop takes one row via the blocking get() then tops up with
    _take_batch(): 60 queued rows must split into batches of 50 and 10, never
    a single 51-row batch."""
    _drain()
    batch_sizes = []
    second_batch_written = asyncio.Event()

    async def fake_write_batch(batch):
        batch_sizes.append(len(batch))
        if len(batch_sizes) == 2:
            second_batch_written.set()
        return len(batch)

    monkeypatch.setattr(audit, "_write_batch", fake_write_batch)

    async def scenario():
        for i in range(60):
            audit._queue.put_nowait({"tool": f"t{i}"})
        task = asyncio.create_task(audit._writer_loop())
        try:
            await asyncio.wait_for(second_batch_written.wait(), timeout=2)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(scenario())
    assert batch_sizes == [50, 10]


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


def test_scrub_url_drops_userinfo_query_and_fragment():
    assert audit.scrub_url("https://user:pat@host/p?x=1#f") == "https://host/p"


def test_scrub_url_keeps_the_port_and_the_path():
    assert audit.scrub_url("http://git.local:8443/a/b.git?token=t") == "http://git.local:8443/a/b.git"


def test_scrub_url_leaves_a_non_url_string_alone():
    assert audit.scrub_url("just a sentence") == "just a sentence"


def test_summarize_args_scrubs_url_keys():
    out = audit.summarize_args(
        {"repo_url": "https://x-token:ghp_secret@github.com/acme/app.git?a=1",
         "endpoint": "https://u:p@api.local:9000/v1#frag"}
    )
    assert out == {
        "repo_url": "https://github.com/acme/app.git",
        "endpoint": "https://api.local:9000/v1",
    }


def test_summarize_args_scrubs_a_url_embedded_in_a_plain_string():
    out = audit.summarize_args({"message": "cloning https://u:ghp_x@github.com/a/b.git?t=1 now"})
    assert out["message"] == "cloning https://github.com/a/b.git now"


def test_scrub_text_rewrites_every_url_in_a_message():
    text = "fatal: could not read from https://tok:x@a.io/r.git and ssh://u:p@b.io/z"
    assert audit.scrub_text(text) == (
        "fatal: could not read from https://a.io/r.git and ssh://b.io/z"
    )


def test_record_call_scrubs_the_error_text():
    _drain()

    async def scenario():
        await audit.record_call(
            tool="repo_clone", permission="repo-write", outcome="error", duration_ms=3,
            error="remote: Invalid credentials for https://x:ghp_secret@github.com/a/b.git?y=2",
        )

    asyncio.run(scenario())
    row = _drain()[0]
    assert "ghp_secret" not in row["error"]
    assert row["error"].endswith("https://github.com/a/b.git")
