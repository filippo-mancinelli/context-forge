"""Scheduler-owned job execution: claim, run, finalise, retry, dead letter, stuck reset."""
import asyncio
import inspect
import json
import logging

import httpx
import pytest

from src import scheduler
from src.mcp import jobs as jobs_tools


class FakeConn:
    def __init__(self, fetch_rows=None):
        self.fetch_rows = fetch_rows or []
        self.executed = []
        self.fetched = []

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        return "UPDATE 1"

    async def fetch(self, sql, *args):
        self.fetched.append((sql, args))
        return self.fetch_rows


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


def _wire_pool(monkeypatch, module, conn):
    async def fake_pool():
        return FakePool(conn)

    monkeypatch.setattr(module, "get_pool", fake_pool)
    return conn


# ── finalize_job ──────────────────────────────────────────────────────────────

def test_success_marks_the_job_done(monkeypatch):
    conn = _wire_pool(monkeypatch, jobs_tools, FakeConn())
    status = asyncio.run(
        jobs_tools.finalize_job("j1", "done", 1, 3, {"ok": True}, None)
    )
    assert status == "done"
    sql, args = conn.executed[0]
    assert "UPDATE jobs" in sql
    assert args[0] == "done"


def test_permanent_error_does_not_retry(monkeypatch):
    _wire_pool(monkeypatch, jobs_tools, FakeConn())
    status = asyncio.run(jobs_tools.finalize_job("j1", "error", 1, 3, None, "HTTP 404"))
    assert status == "error"


def test_retryable_failure_goes_back_to_pending_with_backoff(monkeypatch):
    conn = _wire_pool(monkeypatch, jobs_tools, FakeConn())
    status = asyncio.run(jobs_tools.finalize_job("j1", "retry", 1, 3, None, "HTTP 503"))
    assert status == "pending"
    _sql, args = conn.executed[0]
    # delay parameter carries the 5s backoff for the first failed attempt
    assert 5.0 in args


def test_retryable_failure_at_max_attempts_is_dead_lettered(monkeypatch):
    conn = _wire_pool(monkeypatch, jobs_tools, FakeConn())
    status = asyncio.run(jobs_tools.finalize_job("j1", "retry", 3, 3, None, "HTTP 503"))
    assert status == "dead"
    _sql, args = conn.executed[0]
    assert args[0] == "dead"


def test_terminal_states_write_error_message_but_retries_do_not(monkeypatch):
    conn = _wire_pool(monkeypatch, jobs_tools, FakeConn())
    asyncio.run(jobs_tools.finalize_job("j1", "retry", 1, 3, None, "HTTP 503"))
    _sql, retry_args = conn.executed[0]
    conn.executed.clear()
    asyncio.run(jobs_tools.finalize_job("j1", "error", 1, 3, None, "HTTP 404"))
    _sql, error_args = conn.executed[0]
    # last_error is always written; error_message only on terminal states.
    assert "HTTP 503" in retry_args and None in retry_args
    assert error_args.count("HTTP 404") == 2


# ── run_claimed_job ───────────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeClient:
    def __init__(self, response=None, raises=None):
        self.response = response
        self.raises = raises
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        self.calls.append(("POST", url, json, headers))
        if self.raises:
            raise self.raises
        return self.response

    async def get(self, url, headers=None):
        self.calls.append(("GET", url, None, headers))
        if self.raises:
            raise self.raises
        return self.response


def _capture_finalize(monkeypatch):
    captured = {}

    async def fake_finalize(job_id, outcome, attempts, max_attempts, result, error):
        captured.update(
            job_id=job_id, outcome=outcome, attempts=attempts,
            max_attempts=max_attempts, result=result, error=error,
        )
        return "done"

    monkeypatch.setattr(jobs_tools, "finalize_job", fake_finalize)
    return captured


def test_run_claimed_job_posts_the_stored_params(monkeypatch):
    client = FakeClient(FakeResponse(200, {"answer": 42}))
    monkeypatch.setattr(jobs_tools.httpx, "AsyncClient", lambda *a, **kw: client)
    captured = _capture_finalize(monkeypatch)

    job = {
        "id": "j1",
        "params": json.dumps({
            "url": "http://svc/api", "method": "POST",
            "payload": {"q": "hi"}, "headers": {"X": "1"},
        }),
        "attempts": 1,
        "max_attempts": 3,
    }
    asyncio.run(jobs_tools.run_claimed_job(job))

    assert client.calls == [("POST", "http://svc/api", {"q": "hi"}, {"X": "1"})]
    assert captured["outcome"] == "done"
    assert captured["result"] == {"answer": 42}


def test_run_claimed_job_accepts_params_already_decoded(monkeypatch):
    client = FakeClient(FakeResponse(200, {"ok": True}))
    monkeypatch.setattr(jobs_tools.httpx, "AsyncClient", lambda *a, **kw: client)
    captured = _capture_finalize(monkeypatch)

    job = {"id": "j1", "params": {"url": "http://svc", "method": "GET"},
           "attempts": 1, "max_attempts": 3}
    asyncio.run(jobs_tools.run_claimed_job(job))

    assert client.calls[0][0] == "GET"
    assert captured["outcome"] == "done"


def test_run_claimed_job_classifies_a_5xx_as_retry(monkeypatch):
    client = FakeClient(FakeResponse(503, None, "boom"))
    monkeypatch.setattr(jobs_tools.httpx, "AsyncClient", lambda *a, **kw: client)
    captured = _capture_finalize(monkeypatch)

    asyncio.run(jobs_tools.run_claimed_job(
        {"id": "j1", "params": {"url": "http://svc"}, "attempts": 2, "max_attempts": 3}
    ))

    assert captured["outcome"] == "retry"
    assert captured["error"] == "HTTP 503"
    assert captured["attempts"] == 2


def test_run_claimed_job_classifies_a_timeout_as_retry(monkeypatch):
    client = FakeClient(raises=httpx.ReadTimeout("slow"))
    monkeypatch.setattr(jobs_tools.httpx, "AsyncClient", lambda *a, **kw: client)
    captured = _capture_finalize(monkeypatch)

    asyncio.run(jobs_tools.run_claimed_job(
        {"id": "j1", "params": {"url": "http://svc"}, "attempts": 1, "max_attempts": 3}
    ))

    assert captured["outcome"] == "retry"


def test_run_claimed_job_classifies_a_404_as_permanent_error(monkeypatch):
    client = FakeClient(FakeResponse(404, {"detail": "nope"}))
    monkeypatch.setattr(jobs_tools.httpx, "AsyncClient", lambda *a, **kw: client)
    captured = _capture_finalize(monkeypatch)

    asyncio.run(jobs_tools.run_claimed_job(
        {"id": "j1", "params": {"url": "http://svc"}, "attempts": 1, "max_attempts": 3}
    ))

    assert captured["outcome"] == "error"


# ── _check_jobs ───────────────────────────────────────────────────────────────

def test_check_jobs_claims_only_due_http_rows(monkeypatch):
    conn = FakeConn(fetch_rows=[])
    _wire_pool(monkeypatch, scheduler, conn)

    asyncio.run(scheduler._check_jobs())

    claim_sql = conn.fetched[0][0]
    assert "FOR UPDATE SKIP LOCKED" in claim_sql
    assert "attempts = attempts + 1" in claim_sql
    assert "next_attempt_at <= NOW()" in claim_sql
    assert f"LIMIT {scheduler._JOB_CLAIM_BATCH}" in claim_sql
    assert "tool = 'http'" in claim_sql


def test_check_jobs_skips_rows_that_exhausted_their_attempts(monkeypatch):
    conn = FakeConn(fetch_rows=[])
    _wire_pool(monkeypatch, scheduler, conn)

    asyncio.run(scheduler._check_jobs())

    assert "attempts < max_attempts" in conn.fetched[0][0]


def test_check_jobs_no_longer_resets_stuck_rows(monkeypatch):
    """The reset moved to the maintenance tick, which a long job cannot block."""
    conn = FakeConn(fetch_rows=[])
    _wire_pool(monkeypatch, scheduler, conn)

    asyncio.run(scheduler._check_jobs())

    assert conn.executed == []


def test_check_jobs_runs_every_claimed_row(monkeypatch):
    rows = [
        {"id": "j1", "params": {}, "attempts": 1, "max_attempts": 3},
        {"id": "j2", "params": {}, "attempts": 1, "max_attempts": 3},
    ]
    _wire_pool(monkeypatch, scheduler, FakeConn(fetch_rows=rows))
    ran = []

    async def fake_run(job):
        ran.append(job["id"])

    monkeypatch.setattr(scheduler, "run_claimed_job", fake_run)
    asyncio.run(scheduler._check_jobs())

    assert sorted(ran) == ["j1", "j2"]


def test_check_jobs_survives_one_failing_job(monkeypatch):
    rows = [
        {"id": "j1", "params": {}, "attempts": 1, "max_attempts": 3},
        {"id": "j2", "params": {}, "attempts": 1, "max_attempts": 3},
    ]
    _wire_pool(monkeypatch, scheduler, FakeConn(fetch_rows=rows))
    ran = []

    async def fake_run(job):
        if job["id"] == "j1":
            raise RuntimeError("boom")
        ran.append(job["id"])

    monkeypatch.setattr(scheduler, "run_claimed_job", fake_run)
    asyncio.run(scheduler._check_jobs())

    assert ran == ["j2"]


def test_check_jobs_logs_the_id_of_a_job_that_raised(monkeypatch, caplog):
    rows = [{"id": "j1", "params": {}, "attempts": 1, "max_attempts": 3}]
    _wire_pool(monkeypatch, scheduler, FakeConn(fetch_rows=rows))

    async def fake_run(job):
        raise RuntimeError("boom")

    monkeypatch.setattr(scheduler, "run_claimed_job", fake_run)
    with caplog.at_level(logging.WARNING, logger="src.scheduler"):
        asyncio.run(scheduler._check_jobs())

    assert "j1" in caplog.text
    assert "boom" in caplog.text


# maintenance tick

def test_maintain_jobs_resets_only_stuck_http_rows(monkeypatch):
    conn = FakeConn()
    _wire_pool(monkeypatch, scheduler, conn)

    asyncio.run(scheduler._maintain_jobs())

    reset_sql = conn.executed[0][0]
    assert "status = 'running'" in reset_sql
    assert "status = 'pending'" in reset_sql
    assert f"{scheduler._JOB_STUCK_MINUTES} minutes" in reset_sql
    assert "tool = 'http'" in reset_sql


def test_maintain_jobs_dead_letters_rows_past_max_attempts(monkeypatch):
    conn = FakeConn()
    _wire_pool(monkeypatch, scheduler, conn)

    asyncio.run(scheduler._maintain_jobs())

    sweep_sql = conn.executed[1][0]
    assert "status = 'dead'" in sweep_sql
    assert "status = 'pending'" in sweep_sql
    assert "attempts >= max_attempts" in sweep_sql
    assert "COALESCE(error_message, last_error, 'exceeded max_attempts')" in sweep_sql
    assert "tool = 'http'" in sweep_sql


def test_a_stuck_org_reembed_row_is_neither_reset_nor_claimed(monkeypatch):
    """`org_reembed` rows are driven by src/reembed.py and never refresh updated_at."""
    conn = FakeConn(fetch_rows=[])
    _wire_pool(monkeypatch, scheduler, conn)

    asyncio.run(scheduler._maintain_jobs())
    asyncio.run(scheduler._check_jobs())

    statements = [sql for sql, _a in conn.executed] + [sql for sql, _a in conn.fetched]
    assert len(statements) == 3
    for sql in statements:
        assert "tool = 'http'" in sql


# a database outage degrades to one warning line

def _wire_broken_pool(monkeypatch, module):
    async def fake_pool():
        raise RuntimeError("database is down")

    monkeypatch.setattr(module, "get_pool", fake_pool)


@pytest.mark.parametrize("tick", ["_check_jobs", "_maintain_jobs"])
def test_a_job_tick_logs_one_warning_when_the_database_is_down(monkeypatch, caplog, tick):
    _wire_broken_pool(monkeypatch, scheduler)

    with caplog.at_level(logging.WARNING, logger="src.scheduler"):
        asyncio.run(getattr(scheduler, tick)())

    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.WARNING
    assert caplog.records[0].exc_info is None
    assert "database is down" in caplog.text


def test_the_metrics_tick_logs_one_warning_when_the_refresh_fails(monkeypatch, caplog):
    async def broken_refresh():
        raise RuntimeError("database is down")

    monkeypatch.setattr(scheduler, "refresh_metrics", broken_refresh)
    with caplog.at_level(logging.WARNING, logger="src.scheduler"):
        asyncio.run(scheduler._refresh_metrics())

    assert len(caplog.records) == 1
    assert caplog.records[0].exc_info is None


def test_scheduler_registers_the_executor_and_maintenance_ticks():
    source = inspect.getsource(scheduler.start_scheduler)
    assert "_check_jobs" in source
    assert 'id="jobs"' in source
    assert "seconds=5" in source
    assert "_maintain_jobs" in source
    assert 'id="jobs_maintenance"' in source
    assert "seconds=60" in source


@pytest.mark.parametrize(
    "option", ["max_instances=1", "coalesce=True", "misfire_grace_time=None"]
)
def test_both_job_ticks_declare_their_back_pressure_options(option):
    source = inspect.getsource(scheduler.start_scheduler)
    assert source.count(option) >= 2, option
