import asyncio

import pytest

from src.mcp import datasources as mcp_datasources
from src.mcp.context import set_current_org_id, set_current_project_id
from src.mcp.permissions import set_current_permissions


@pytest.fixture(autouse=True)
def _reset_mcp_context():
    """Reset MCP context vars after each test to prevent state pollution."""
    yield
    set_current_org_id(None)
    set_current_project_id(None)
    set_current_permissions(None)


def _underlying(tool):
    return getattr(tool, "fn", tool)


def test_db_execute_requires_db_write_permission(monkeypatch):
    set_current_org_id(1)
    set_current_project_id(2)
    set_current_permissions(frozenset({"context-read", "db-query"}))
    with pytest.raises(Exception, match="db-write"):
        asyncio.run(_underlying(mcp_datasources.db_execute)(connection="erp", sql="UPDATE t SET a=1 WHERE id=1"))


def test_db_execute_runs_write(monkeypatch):
    called = {}

    async def fake_run_write(org_id, project_id, ref, sql, source="mcp"):
        called.update(org=org_id, project=project_id, ref=ref, sql=sql, source=source)
        return {"connection": "erp", "sql": sql, "row_count": 1, "duration_ms": 5}

    monkeypatch.setattr(mcp_datasources.service, "run_write", fake_run_write)
    set_current_org_id(1)
    set_current_project_id(2)
    set_current_permissions(frozenset({"db-write"}))
    out = asyncio.run(_underlying(mcp_datasources.db_execute)(connection="erp", sql="UPDATE t SET a=1 WHERE id=1"))
    assert out["row_count"] == 1
    assert called["source"] == "mcp"
