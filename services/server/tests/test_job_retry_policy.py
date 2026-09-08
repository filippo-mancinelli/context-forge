"""Backoff schedule, outcome classification, and the 0002_jobs_retry migration module."""
import asyncio
import importlib
import inspect

import httpx
import pytest

from src.mcp import jobs as jobs_tools

# Module names starting with a digit need importlib, not an import statement.
migration = importlib.import_module("src.migrations.versions.0002_jobs_retry")


@pytest.mark.parametrize("attempts,expected", [(1, 5), (2, 20), (3, 80), (4, 300), (5, 300)])
def test_backoff_grows_by_four_and_caps_at_five_minutes(attempts, expected):
    assert jobs_tools.next_backoff_seconds(attempts) == expected


def test_backoff_of_zero_attempts_is_the_base_delay():
    assert jobs_tools.next_backoff_seconds(0) == 5


@pytest.mark.parametrize("code", [200, 201, 204, 299])
def test_2xx_is_done(code):
    assert jobs_tools.classify_http_status(code) == "done"


@pytest.mark.parametrize("code", [400, 401, 403, 404, 422, 451])
def test_4xx_is_a_permanent_error(code):
    assert jobs_tools.classify_http_status(code) == "error"


@pytest.mark.parametrize("code", [408, 429, 500, 502, 503, 504])
def test_5xx_408_and_429_are_retryable(code):
    assert jobs_tools.classify_http_status(code) == "retry"


def test_redirect_is_a_permanent_error():
    assert jobs_tools.classify_http_status(302) == "error"


def test_timeout_is_retryable():
    assert jobs_tools.classify_exception(httpx.ReadTimeout("slow")) == "retry"


def test_connection_error_is_retryable():
    assert jobs_tools.classify_exception(httpx.ConnectError("refused")) == "retry"


def test_an_attempt_deadline_is_retryable():
    assert jobs_tools.classify_exception(TimeoutError("attempt budget spent")) == "retry"


def test_programming_error_is_not_retryable():
    assert jobs_tools.classify_exception(ValueError("bad url")) == "error"


class _FakeConn:
    def __init__(self):
        self.executed = []

    async def execute(self, sql, *args):
        self.executed.append(sql)
        return "ALTER TABLE"


def test_migration_declares_the_module_contract():
    assert migration.VERSION == 2
    assert migration.NAME == "jobs_retry"
    assert migration.TRANSACTIONAL is True


def test_migration_adds_the_four_retry_columns_and_the_due_index():
    conn = _FakeConn()
    asyncio.run(migration.upgrade(conn))
    sql = " ".join(conn.executed).lower()
    assert "attempts int not null default 0" in sql
    assert "max_attempts int not null default 3" in sql
    assert "next_attempt_at timestamptz not null default now()" in sql
    assert "last_error text" in sql
    assert "jobs_due_idx" in sql
    assert sql.count("add column if not exists") == 4


def test_migration_dead_letters_jobs_left_running_by_the_old_executor():
    """The pre-0002 executor was fire-and-forget: a 'running' row may already
    have been delivered, so it is dead-lettered instead of being re-fired."""
    conn = _FakeConn()
    asyncio.run(migration.upgrade(conn))
    assert len(conn.executed) == 6
    sweep = conn.executed[-1]
    assert "status = 'dead'" in sweep
    assert "status = 'running'" in sweep
    assert "tool = 'http'" in sweep
    assert sweep.count("interrupted before durable retries (0002_jobs_retry)") == 2


def test_migration_does_not_open_its_own_transaction():
    """The runner wraps TRANSACTIONAL modules; the module must not nest one."""
    assert "transaction()" not in inspect.getsource(migration.upgrade)
