"""Queue snapshot, gauge refresh, and the scheduler heartbeat."""
import asyncio
import inspect
import time

import pytest

from src import metrics, scheduler


class FakeConn:
    def __init__(self, row):
        self.row = row
        self.queries = []

    async def fetchrow(self, sql, *args):
        self.queries.append(sql)
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


ROW = {
    "jobs_pending": 4,
    "jobs_running": 1,
    "jobs_done": 90,
    "jobs_error": 2,
    "jobs_dead": 3,
    "index_requests_pending": 7,
    "index_requests_oldest_age_seconds": 4200,
    "kb_documents_pending": 5,
    "web_pages_pending": 6,
}


@pytest.fixture
def wired(monkeypatch):
    conn = FakeConn(ROW)

    async def fake_pool():
        return FakePool(conn)

    async def fake_version(pool):
        return 2

    monkeypatch.setattr(metrics, "get_pool", fake_pool)
    monkeypatch.setattr(metrics, "current_version", fake_version)
    return conn


def test_queue_stats_come_from_a_single_round_trip(wired):
    stats = asyncio.run(metrics.collect_queue_stats())
    assert len(wired.queries) == 1
    assert stats["jobs_pending"] == 4
    assert stats["jobs_dead"] == 3
    assert stats["index_requests_oldest_age_seconds"] == 4200
    assert stats["kb_documents_pending"] == 5
    assert stats["web_pages_pending"] == 6


def test_queue_stats_query_reads_every_source(wired):
    asyncio.run(metrics.collect_queue_stats())
    sql = wired.queries[0].lower()
    assert "from jobs" in sql
    assert "from index_requests" in sql
    assert "from kb_documents" in sql
    assert "from web_pages" in sql


def test_queue_stats_count_only_the_jobs_the_executor_owns(wired):
    """`org_reembed` rows have their own producer and must not skew the gauges."""
    asyncio.run(metrics.collect_queue_stats())
    sql = wired.queries[0]
    assert sql.count("tool = 'http'") == 5


def test_queue_stats_tolerate_nulls(monkeypatch):
    row = dict(ROW, index_requests_oldest_age_seconds=None)
    conn = FakeConn(row)

    async def fake_pool():
        return FakePool(conn)

    monkeypatch.setattr(metrics, "get_pool", fake_pool)
    stats = asyncio.run(metrics.collect_queue_stats())
    assert stats["index_requests_oldest_age_seconds"] == 0.0


def _sample(name, labels=None):
    for metric in metrics.registry.collect():
        for s in metric.samples:
            if s.name == name and (labels is None or s.labels == labels):
                return s.value
    return None


def test_refresh_sets_every_gauge(wired):
    asyncio.run(metrics.refresh_metrics())
    assert _sample("contextforge_jobs", {"status": "pending"}) == 4
    assert _sample("contextforge_jobs", {"status": "dead"}) == 3
    assert _sample("contextforge_index_requests_pending") == 7
    assert _sample("contextforge_index_requests_oldest_age_seconds") == 4200
    assert _sample("contextforge_kb_documents_pending") == 5
    assert _sample("contextforge_web_pages_pending") == 6
    assert _sample("contextforge_schema_version") == 2


def test_refresh_records_the_heartbeat(wired):
    before = time.time()
    asyncio.run(metrics.refresh_metrics())
    assert metrics.last_scheduler_tick() >= before
    assert _sample("contextforge_scheduler_tick_timestamp_seconds") >= before


def test_record_scheduler_tick_accepts_an_explicit_time():
    metrics.record_scheduler_tick(1_000_000.0)
    assert metrics.last_scheduler_tick() == 1_000_000.0
    assert _sample("contextforge_scheduler_tick_timestamp_seconds") == 1_000_000.0


def test_refresh_leaves_every_gauge_and_the_heartbeat_untouched_on_partial_failure(
    monkeypatch,
):
    """schema_version's lookup fails after collect_queue_stats succeeds: nothing
    should be applied, so the previous gauge values and heartbeat survive."""

    async def fake_collect_queue_stats():
        return {
            "jobs_pending": 4.0,
            "jobs_running": 1.0,
            "jobs_done": 90.0,
            "jobs_error": 2.0,
            "jobs_dead": 3.0,
            "index_requests_pending": 7.0,
            "index_requests_oldest_age_seconds": 4200.0,
            "kb_documents_pending": 5.0,
            "web_pages_pending": 6.0,
        }

    async def fake_pool():
        return object()

    async def fake_version_fails(pool):
        raise RuntimeError("schema_migrations unreachable")

    monkeypatch.setattr(metrics, "collect_queue_stats", fake_collect_queue_stats)
    monkeypatch.setattr(metrics, "get_pool", fake_pool)
    monkeypatch.setattr(metrics, "current_version", fake_version_fails)

    # Seed sentinel values distinct from both the "old" state and the stats above.
    for status in ("pending", "running", "done", "error", "dead"):
        metrics.jobs_by_status.labels(status=status).set(-1)
    metrics.index_requests_pending.set(-1)
    metrics.index_requests_oldest_age_seconds.set(-1)
    metrics.kb_documents_pending.set(-1)
    metrics.web_pages_pending.set(-1)
    metrics.schema_version.set(-1)
    metrics.record_scheduler_tick(123.0)

    with pytest.raises(RuntimeError):
        asyncio.run(metrics.refresh_metrics())

    for status in ("pending", "running", "done", "error", "dead"):
        assert _sample("contextforge_jobs", {"status": status}) == -1
    assert _sample("contextforge_index_requests_pending") == -1
    assert _sample("contextforge_index_requests_oldest_age_seconds") == -1
    assert _sample("contextforge_kb_documents_pending") == -1
    assert _sample("contextforge_web_pages_pending") == -1
    assert _sample("contextforge_schema_version") == -1
    assert metrics.last_scheduler_tick() == 123.0
    assert _sample("contextforge_scheduler_tick_timestamp_seconds") == 123.0


def test_scheduler_refresh_job_delegates_to_metrics(monkeypatch):
    called = []

    async def fake_refresh():
        called.append(True)

    monkeypatch.setattr(scheduler, "start_audit_writer", lambda: None)
    monkeypatch.setattr(scheduler, "refresh_metrics", fake_refresh)
    asyncio.run(scheduler._refresh_metrics())
    assert called == [True]


def test_scheduler_registers_the_metrics_tick_and_an_immediate_first_tick():
    source = inspect.getsource(scheduler.start_scheduler)
    assert "_refresh_metrics" in source
    assert 'id="metrics"' in source
    assert "seconds=30" in source
    assert "record_scheduler_tick()" in source


def test_is_scheduler_running_reflects_the_global(monkeypatch):
    monkeypatch.setattr(scheduler, "_scheduler", None)
    assert scheduler.is_scheduler_running() is False

    class _Live:
        running = True

    monkeypatch.setattr(scheduler, "_scheduler", _Live())
    assert scheduler.is_scheduler_running() is True


def test_refresh_reports_the_audit_queue_depth(wired):
    """The gauge makes a stalled MCP audit writer visible in Prometheus."""
    from src.mcp import audit

    while not audit._queue.empty():
        audit._queue.get_nowait()
    try:
        audit._queue.put_nowait({"tool": "a"})
        audit._queue.put_nowait({"tool": "b"})
        asyncio.run(metrics.refresh_metrics())
        assert _sample("contextforge_mcp_audit_queue_depth") == 2
    finally:
        while not audit._queue.empty():
            audit._queue.get_nowait()
