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

    async def fake_get_scope(org_id, project_id, ref, include_secret=False):
        return {"id": 7, "name": "erp", "alias": "erp", "engine": "postgresql"}

    async def fake_run_write(org_id, project_id, ref, sql, source="mcp"):
        called.update(org=org_id, project=project_id, ref=ref, sql=sql, source=source)
        return {"connection": "erp", "sql": sql, "row_count": 1, "duration_ms": 5}

    monkeypatch.setattr(mcp_datasources.service, "get_scope", fake_get_scope)
    monkeypatch.setattr(mcp_datasources.service, "run_write", fake_run_write)
    set_current_org_id(1)
    set_current_project_id(2)
    set_current_permissions(frozenset({"db-write"}))
    out = asyncio.run(_underlying(mcp_datasources.db_execute)(connection="erp", sql="UPDATE t SET a=1 WHERE id=1"))
    assert out["row_count"] == 1
    assert called["source"] == "mcp"


def test_db_execute_prefixes_a_validator_rejection_like_db_query(monkeypatch):
    """Il rifiuto del validator arriva con lo stesso prefisso su tutte le
    superfici, così l'agente lo riconosce come tale e non come un errore
    del database."""
    from src.datasources.validator import QueryValidationError

    async def fake_get_scope(org_id, project_id, ref, include_secret=False):
        return {"id": 7, "name": "erp", "alias": "erp", "engine": "postgresql"}

    async def refusing_write(org_id, project_id, ref, sql, source="mcp"):
        raise QueryValidationError("DELETE without a WHERE clause is not allowed.")

    monkeypatch.setattr(mcp_datasources.service, "get_scope", fake_get_scope)
    monkeypatch.setattr(mcp_datasources.service, "run_write", refusing_write)
    set_current_org_id(1)
    set_current_project_id(2)
    set_current_permissions(frozenset({"db-write"}))
    out = asyncio.run(_underlying(mcp_datasources.db_execute)(connection="erp", sql="DELETE FROM t"))
    assert out["status"] == "error"
    assert out["error"] == "Query rejected: DELETE without a WHERE clause is not allowed."


def test_the_scope_argument_docs_speak_of_the_alias():
    """I tool nominano il perimetro con il suo alias: "connection name" era il
    nome della connessione, che non è più quello che si passa."""
    for tool in (mcp_datasources.db_query, mcp_datasources.db_describe,
                 mcp_datasources.db_schema, mcp_datasources.db_execute):
        doc = _underlying(tool).__doc__ or ""
        assert "onnection name" not in doc, tool.__name__
