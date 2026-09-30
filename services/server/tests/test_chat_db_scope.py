import asyncio
import logging

from src.api.deps import ActiveProject
from src.api.routes import chat
from src.datasources import service
from src.datasources.validator import QueryValidationError

ORG = ActiveProject(org_id=1, project_id=4, role="member", namespace="acme--alpha",
                    name="alpha", org_name="acme")

SCOPES = [
    {"id": 5, "name": "crm", "alias": "crm", "engine": "mysql", "database_name": "crm",
     "description": None, "status": "ok", "scope_id": 21, "scope_database": "crm",
     "scope_schema": None, "scope_inferred": True, "scope_label": "crm"},
    {"id": 4, "name": "erp", "alias": "erp-vendite", "engine": "postgresql",
     "database_name": "app", "description": "Sales", "status": "ok", "scope_id": 11,
     "scope_database": "app", "scope_schema": "vendite", "scope_inferred": False,
     "scope_label": "app.vendite"},
]


def _patch(monkeypatch):
    async def fake_list(org_id, project_id):
        return SCOPES

    async def fake_get_scope(org_id, project_id, ref, include_secret=False):
        for scope in SCOPES:
            if str(ref).lower() == scope["alias"]:
                return scope
        raise service.ConnectionNotFoundError(f"Database connection '{ref}' not found")

    monkeypatch.setattr(service, "list_connections", fake_list)
    monkeypatch.setattr(service, "get_scope", fake_get_scope)


def test_system_prompt_lists_alias_and_scope(monkeypatch):
    _patch(monkeypatch)
    prompt = asyncio.run(chat._build_system_prompt(ORG, []))
    assert "  - crm (scope crm, inferred)" in prompt
    assert "  - erp-vendite — Sales (scope app.vendite)" in prompt


def test_schema_listing_names_alias_and_scope(monkeypatch):
    _patch(monkeypatch)
    rows = asyncio.run(chat._get_database_schema(ORG, {}))
    assert rows[1] == {"connection": "erp-vendite", "engine": "postgresql",
                       "scope": "app.vendite", "inferred": False,
                       "description": "Sales", "status": "ok"}


def test_schema_outside_the_scope_comes_back_as_a_tool_error(monkeypatch):
    _patch(monkeypatch)
    trace = asyncio.run(chat._run_tool(
        ORG, "get_database_schema", {"connection": "erp-vendite", "schema": "acquisti"}
    ))
    assert trace.result_count == 0
    assert "app.vendite" in trace.error and "crm" in trace.error


def test_a_scope_refusal_is_not_logged_as_an_incident(monkeypatch, caplog):
    """Un perimetro fuori portata è un rifiuto normale, come sulle superfici
    MCP: l'agente riceve il messaggio e il log non registra un errore."""
    _patch(monkeypatch)
    with caplog.at_level(logging.DEBUG, logger=chat.logger.name):
        trace = asyncio.run(chat._run_tool(
            ORG, "get_database_schema", {"connection": "erp-vendite", "schema": "acquisti"}
        ))
    assert trace.error and "app.vendite" in trace.error
    assert [r.levelname for r in caplog.records if r.levelname == "ERROR"] == []


def test_a_rejected_query_is_not_logged_as_an_incident(monkeypatch, caplog):
    _patch(monkeypatch)

    async def refusing_query(org_id, project_id, ref, sql, max_rows=100, source="ui"):
        raise QueryValidationError("Query contains blocked operation: DROP.")

    monkeypatch.setattr(service, "run_query", refusing_query)
    with caplog.at_level(logging.DEBUG, logger=chat.logger.name):
        trace = asyncio.run(chat._run_tool(
            ORG, "query_database", {"connection": "erp-vendite", "sql": "DROP TABLE t"}
        ))
    assert trace.error and "blocked operation" in trace.error
    assert [r.levelname for r in caplog.records if r.levelname == "ERROR"] == []


def test_an_unexpected_tool_failure_is_still_logged_as_an_error(monkeypatch, caplog):
    _patch(monkeypatch)

    async def broken_query(org_id, project_id, ref, sql, max_rows=100, source="ui"):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(service, "run_query", broken_query)
    with caplog.at_level(logging.DEBUG, logger=chat.logger.name):
        trace = asyncio.run(chat._run_tool(
            ORG, "query_database", {"connection": "erp-vendite", "sql": "SELECT 1"}
        ))
    assert trace.error == "connection reset"
    assert any(r.levelname == "ERROR" for r in caplog.records)
