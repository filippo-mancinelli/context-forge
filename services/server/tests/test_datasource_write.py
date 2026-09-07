import asyncio

import pytest

from src.datasources import service
from src.datasources.validator import QueryValidationError


def _patch(monkeypatch, rowcount=3):
    async def fake_get_connection(org_id, project_id, ref, include_secret):
        return {"id": 4, "name": "erp"}

    async def fake_resolve_engine(record):
        return "engine"

    def fake_execute_write(engine, sql):
        assert engine == "engine"
        return rowcount

    logged = {}

    async def fake_log_write(record, org_id, project_id, source, sql, success, error,
                             rowcount_val, duration_ms):
        logged.update(source=source, sql=sql, success=success)

    monkeypatch.setattr(service, "get_connection", fake_get_connection)
    monkeypatch.setattr(service, "_resolve_engine", fake_resolve_engine)
    monkeypatch.setattr(service, "_execute_write", fake_execute_write)
    monkeypatch.setattr(service, "_log_query", fake_log_write)
    return logged


def test_run_write_returns_rowcount(monkeypatch):
    logged = _patch(monkeypatch)
    out = asyncio.run(service.run_write(1, 2, "erp", "UPDATE t SET a=1 WHERE id=9"))
    assert out["row_count"] == 3
    assert out["connection"] == "erp"
    assert logged["success"] is True


def test_run_write_rejects_readonly_statements(monkeypatch):
    _patch(monkeypatch)
    with pytest.raises(QueryValidationError):
        asyncio.run(service.run_write(1, 2, "erp", "SELECT 1"))


def test_run_write_reports_truthful_failure_on_db_error(monkeypatch):
    """A failing write must never return a success dict (no commit-after-failure)."""

    async def fake_get_connection(org_id, project_id, ref, include_secret):
        return {"id": 4, "name": "erp"}

    async def fake_resolve_engine(record):
        return "engine"

    def fake_execute_write(engine, sql):
        raise TimeoutError("db statement timeout")

    logged = {}

    async def fake_log_write(record, org_id, project_id, source, sql, success, error,
                             rowcount_val, duration_ms):
        logged.update(success=success, error=error, rowcount=rowcount_val)

    monkeypatch.setattr(service, "get_connection", fake_get_connection)
    monkeypatch.setattr(service, "_resolve_engine", fake_resolve_engine)
    monkeypatch.setattr(service, "_execute_write", fake_execute_write)
    monkeypatch.setattr(service, "_log_query", fake_log_write)

    with pytest.raises(RuntimeError):
        asyncio.run(service.run_write(1, 2, "erp", "UPDATE t SET a=1 WHERE id=9"))

    assert logged["success"] is False
    assert logged["rowcount"] == 0
    assert "db statement timeout" in logged["error"]
