"""Audit dei tool MCP: redazione argomenti, esiti, coda e writer."""
import asyncio
import contextlib
import json
import logging

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


def _fresh_queue(monkeypatch):
    """asyncio.Queue binds to the first loop that awaits get(): every test that
    drives the writer needs its own, or the next asyncio.run() sees a queue
    bound to a dead loop."""
    monkeypatch.setattr(audit, "_queue", asyncio.Queue(maxsize=audit.MAX_QUEUE))


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
    _fresh_queue(monkeypatch)
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


def test_scrub_url_brackets_an_ipv6_host():
    assert audit.scrub_url("https://[::1]:8000/p?x=1") == "https://[::1]:8000/p"


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


def test_summarize_args_scrubs_a_url_embedded_in_url_keyed_prose():
    out = audit.summarize_args({"url": "see https://u:pat@host/p?x=1"})
    assert out["url"] == "see https://host/p"


def test_scrub_text_rewrites_every_url_in_a_message():
    text = "fatal: could not read from https://tok:x@a.io/r.git and ssh://u:p@b.io/z"
    assert audit.scrub_text(text) == (
        "fatal: could not read from https://a.io/r.git and ssh://b.io/z"
    )


def test_scrub_text_keeps_trailing_text_after_a_query_carrying_url_in_json():
    text = 'body {"url":"https://u:p@h/p?x=1","code":500}'
    assert audit.scrub_text(text) == 'body {"url":"https://h/p","code":500}'


def test_scrub_text_keeps_trailing_text_after_a_url_in_parentheses():
    text = "(https://h/p?x=1) retrying"
    assert audit.scrub_text(text) == "(https://h/p) retrying"


def test_scrub_text_restores_a_trailing_period_dropped_with_the_query():
    text = "verify at https://u:p@a.io/p?x=1."
    assert audit.scrub_text(text) == "verify at https://a.io/p."


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


def test_classify_outcome_uses_the_exception_type():
    from src.mcp.permissions import PermissionDenied
    from src.mcp.ratelimit import RateLimited

    assert audit.classify_outcome(PermissionDenied("nope")) == "denied"
    assert audit.classify_outcome(RateLimited("slow down")) == "rate_limited"


def test_classify_outcome_does_not_deny_on_an_unrelated_access_denied():
    """A tool body forwarding an upstream 'Access denied' is an error, not a
    permission decision taken by this server."""
    from src.mcp.permissions import ToolError

    assert audit.classify_outcome(ToolError("Access denied by upstream")) == "error"


def test_audited_records_a_dict_error_return_as_an_error():
    _drain()

    async def scenario():
        async with audit.audited("db_execute", "db-write", {}) as call:
            result = {"status": "error", "error": "syntax error at or near FROM"}
            call.set_result(result)
            return result

    out = asyncio.run(scenario())
    assert out == {"status": "error", "error": "syntax error at or near FROM"}
    row = _drain()[0]
    assert row["outcome"] == "error"
    assert row["error"] == "syntax error at or near FROM"


def test_audited_scrubs_a_url_in_a_dict_error_return():
    _drain()

    async def scenario():
        async with audit.audited("repo_clone", "repo-write", {}) as call:
            call.set_result({"status": "error", "error": "auth failed for https://u:p@h/r.git?a=1"})

    asyncio.run(scenario())
    assert _drain()[0]["error"] == "auth failed for https://h/r.git"


def test_audited_records_a_dict_ok_return_as_ok():
    _drain()

    async def scenario():
        async with audit.audited("db_query", "db-query", {}) as call:
            call.set_result({"status": "ok", "rows": []})

    asyncio.run(scenario())
    row = _drain()[0]
    assert row["outcome"] == "ok"
    assert row["error"] is None


def test_decorated_tool_returning_a_dict_error_is_audited_as_an_error():
    from src.mcp import permissions as perms

    _drain()

    @perms.requires_permission("db-write")
    async def db_execute() -> dict:
        return {"status": "error", "error": "relation does not exist"}

    perms.set_current_permissions(frozenset({"*"}))
    try:
        assert asyncio.run(db_execute()) == {"status": "error", "error": "relation does not exist"}
    finally:
        perms.set_current_permissions(None)

    row = _drain()[0]
    assert row["outcome"] == "error"
    assert row["error"] == "relation does not exist"


def test_audit_only_tool_returning_a_dict_error_is_audited_as_an_error():
    from src.mcp import permissions as perms

    _drain()

    @perms.audit_only
    async def use_project() -> dict:
        return {"status": "error", "error": "project not found"}

    asyncio.run(use_project())
    row = _drain()[0]
    assert row["outcome"] == "error"
    assert row["error"] == "project not found"


def test_denied_by_the_decorator_raises_permission_denied():
    from src.mcp import permissions as perms

    _drain()

    @perms.requires_permission("jobs")
    async def fake_tool() -> int:
        return 1

    perms.set_current_permissions(frozenset({"context-read"}))
    try:
        with pytest.raises(perms.PermissionDenied):
            asyncio.run(fake_tool())
    finally:
        perms.set_current_permissions(None)
    assert _drain()[0]["outcome"] == "denied"


def test_a_tool_body_raising_access_denied_is_audited_as_an_error():
    from src.mcp import permissions as perms

    _drain()

    @perms.requires_permission("db-query")
    async def db_query() -> dict:
        raise perms.ToolError("Access denied by upstream")

    perms.set_current_permissions(frozenset({"*"}))
    try:
        with pytest.raises(perms.ToolError):
            asyncio.run(db_query())
    finally:
        perms.set_current_permissions(None)
    assert _drain()[0]["outcome"] == "error"


def test_queue_depth_reports_the_pending_rows():
    _drain()
    assert audit.queue_depth() == 0
    audit._queue.put_nowait({"tool": "a"})
    audit._queue.put_nowait({"tool": "b"})
    assert audit.queue_depth() == 2
    _drain()


def test_write_batch_survives_a_malformed_row(monkeypatch):
    """A row missing a column must not raise out of the writer: the comprehension
    that pivots rows into columns has to sit inside the guarded block."""
    _drain()
    conn = FakeConn()

    async def fake_pool():
        return FakePool(conn)

    monkeypatch.setattr(audit, "get_pool", fake_pool)
    assert asyncio.run(audit._write_batch([{"tool": "a"}])) == 0
    assert conn.executed == []


def test_writer_loop_survives_a_failing_batch(monkeypatch, caplog):
    _fresh_queue(monkeypatch)
    monkeypatch.setattr(audit, "WRITER_ERROR_BACKOFF", 0.01)
    calls = []
    first_failed = asyncio.Event()
    second_written = asyncio.Event()

    async def flaky(batch):
        calls.append(len(batch))
        if len(calls) == 1:
            first_failed.set()
            raise RuntimeError("boom")
        second_written.set()
        return len(batch)

    monkeypatch.setattr(audit, "_write_batch", flaky)

    async def scenario():
        audit._queue.put_nowait({"tool": "a"})
        task = asyncio.create_task(audit._writer_loop())
        try:
            await asyncio.wait_for(first_failed.wait(), timeout=2)
            audit._queue.put_nowait({"tool": "b"})
            await asyncio.wait_for(second_written.wait(), timeout=2)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    with caplog.at_level(logging.WARNING, logger="src.mcp.audit"):
        asyncio.run(scenario())

    assert calls == [1, 1]
    assert "MCP audit writer" in caplog.text


def test_flush_once_returns_none_on_an_empty_queue():
    _drain()
    assert asyncio.run(audit.flush_once()) is None


def test_stop_drains_every_queued_row(monkeypatch):
    _drain()
    sizes = []

    async def fake_write_batch(batch):
        sizes.append(len(batch))
        return len(batch)

    monkeypatch.setattr(audit, "_write_batch", fake_write_batch)
    monkeypatch.setattr(audit, "_writer_task", None)

    async def scenario():
        for i in range(120):
            audit._queue.put_nowait({"tool": f"t{i}"})
        await audit.stop_audit_writer()

    asyncio.run(scenario())
    assert sizes == [50, 50, 20]
    assert audit.queue_depth() == 0


def test_stop_warns_with_the_remaining_depth_when_the_drain_times_out(monkeypatch, caplog):
    _drain()
    monkeypatch.setattr(audit, "_writer_task", None)
    monkeypatch.setattr(audit, "DRAIN_TIMEOUT", 0.05)

    async def slow_write_batch(batch):
        await asyncio.sleep(5)
        return len(batch)

    monkeypatch.setattr(audit, "_write_batch", slow_write_batch)

    async def scenario():
        for i in range(120):
            audit._queue.put_nowait({"tool": f"t{i}"})
        await audit.stop_audit_writer()

    with caplog.at_level(logging.WARNING, logger="src.mcp.audit"):
        asyncio.run(scenario())

    assert "MCP audit drain timed out" in caplog.text
    assert "70" in caplog.text  # 120 queued minus the 50 taken by the batch in flight
    _drain()


def test_metrics_tick_restarts_a_dead_audit_writer(monkeypatch):
    """A writer that died must come back within a scheduler tick."""
    from src import scheduler

    async def noop():
        return None

    _fresh_queue(monkeypatch)
    monkeypatch.setattr(scheduler, "refresh_metrics", noop)

    async def scenario():
        dead = asyncio.create_task(noop())
        await dead
        monkeypatch.setattr(audit, "_writer_task", dead)
        await scheduler._refresh_metrics()
        revived = audit._writer_task
        assert revived is not None and revived is not dead and not revived.done()
        revived.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await revived

    asyncio.run(scenario())
