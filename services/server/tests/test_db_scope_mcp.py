import asyncio
import logging

import pytest

from src.datasources import service
from src.datasources.scopes import ScopeViolationError
from src.datasources.validator import QueryValidationError
from src.mcp import context as mcp_context
from src.mcp import datasources
from src.mcp import permissions as perms

SCOPES = [
    {"id": 4, "name": "erp", "alias": "erp-acquisti", "engine": "postgresql", "host": "h",
     "database_name": "gestionale", "description": "ERP", "status": "ok", "annotation_count": 0,
     "scope_id": 12, "scope_database": "app", "scope_schema": "acquisti",
     "scope_inferred": False, "scope_label": "app.acquisti"},
    {"id": 4, "name": "erp", "alias": "erp-vendite", "engine": "postgresql", "host": "h",
     "database_name": "gestionale", "description": "ERP", "status": "ok", "annotation_count": 2,
     "scope_id": 11, "scope_database": "app", "scope_schema": "vendite",
     "scope_inferred": False, "scope_label": "app.vendite"},
]


def _underlying(tool):
    return getattr(tool, "fn", tool)


class _Identity:
    def __enter__(self):
        perms.set_current_permissions(frozenset({"context-read", "db-query"}))
        mcp_context.set_current_org_id(1)
        mcp_context.set_current_project_id(4)
        return self

    def __exit__(self, *exc):
        perms.set_current_permissions(None)
        mcp_context.set_current_org_id(None)
        mcp_context.set_current_project_id(None)
        return False


@pytest.fixture(autouse=True)
def _scopes(monkeypatch):
    async def fake_list(org_id, project_id):
        return SCOPES

    async def fake_get_scope(org_id, project_id, ref, include_secret=False):
        for scope in SCOPES:
            if str(ref).lower() in (scope["alias"], str(scope["scope_id"])):
                return scope
        raise service.ConnectionNotFoundError(f"Database connection '{ref}' not found")

    monkeypatch.setattr(service, "list_connections", fake_list)
    monkeypatch.setattr(service, "get_scope", fake_get_scope)


def test_db_list_shows_alias_and_scope():
    with _Identity():
        result = asyncio.run(_underlying(datasources.db_list)())
    assert result["connections"][1] == {
        "alias": "erp-vendite", "name": "erp", "engine": "postgresql", "host": "h",
        "database": "app", "scope": "app.vendite", "inferred": False,
        "description": "ERP", "status": "ok", "annotation_count": 2,
    }


def test_db_schema_names_the_scope_when_the_schema_is_outside():
    with _Identity():
        result = asyncio.run(
            _underlying(datasources.db_schema)(connection="erp-vendite", schema="acquisti")
        )
    assert result["status"] == "error"
    assert "app.vendite" in result["error"] and "erp-acquisti" in result["error"]
    assert "catalog_list" in result["error"] and "resource_select" in result["error"]


def test_fuzzy_resolution_matches_alias_and_scope():
    record = asyncio.run(service.resolve_connection(1, 4, "acquisti"))
    assert record["alias"] == "erp-acquisti"


def test_fuzzy_resolution_no_longer_matches_the_connection_database():
    with pytest.raises(service.ConnectionNotFoundError) as exc:
        asyncio.run(service.resolve_connection(1, 4, "gestionale"))
    assert "erp-acquisti" in str(exc.value) and "erp-vendite" in str(exc.value)


# --------------------------------------------------------------------------- #
# A scope refusal or a validator rejection is a normal, well-formed answer to
# the agent, not an unexpected failure: the MCP handlers must return the
# actionable message without logging it at ERROR level.
# --------------------------------------------------------------------------- #
_VIOLATION = ScopeViolationError(
    "The schema 'acquisti' is outside the scope. The data source 'erp-vendite' is limited "
    "to the scope 'app.vendite'. Data sources of this project: erp-acquisti, erp-vendite. "
    "To work on another scope, add it to the project with catalog_list and resource_select.",
    "erp-vendite", "app.vendite", ["erp-acquisti", "erp-vendite"],
)


def _no_error_logs(caplog) -> bool:
    return not any(r.levelno >= logging.ERROR for r in caplog.records)


def test_db_schema_scope_violation_is_a_rejection_not_a_failure(monkeypatch, caplog):
    async def fake_overview(org_id, project_id, ref, schema=None):
        raise _VIOLATION

    monkeypatch.setattr(service, "schema_overview", fake_overview)
    with caplog.at_level(logging.DEBUG, logger="src.mcp.datasources"):
        with _Identity():
            result = asyncio.run(_underlying(datasources.db_schema)(connection="erp-vendite"))
    assert result == {"status": "error", "error": str(_VIOLATION)}
    assert _no_error_logs(caplog)


def test_db_describe_scope_violation_is_a_rejection_not_a_failure(monkeypatch, caplog):
    async def fake_describe(org_id, project_id, ref, table, schema=None, sample_rows=0):
        raise _VIOLATION

    monkeypatch.setattr(service, "describe_table", fake_describe)
    with caplog.at_level(logging.DEBUG, logger="src.mcp.datasources"):
        with _Identity():
            result = asyncio.run(
                _underlying(datasources.db_describe)(table="ordini", connection="erp-vendite")
            )
    assert result == {"status": "error", "error": str(_VIOLATION)}
    assert _no_error_logs(caplog)


def test_db_query_scope_violation_is_a_rejection_not_a_failure(monkeypatch, caplog):
    async def fake_run_query(org_id, project_id, ref, sql, max_rows=100, source="mcp"):
        raise _VIOLATION

    monkeypatch.setattr(service, "run_query", fake_run_query)
    with caplog.at_level(logging.DEBUG, logger="src.mcp.datasources"):
        with _Identity():
            result = asyncio.run(
                _underlying(datasources.db_query)(sql="SELECT 1", connection="erp-vendite")
            )
    assert result == {"status": "error", "error": str(_VIOLATION)}
    assert _no_error_logs(caplog)


def test_db_execute_scope_violation_is_a_rejection_not_a_failure(monkeypatch, caplog):
    async def fake_run_write(org_id, project_id, ref, sql, source="mcp"):
        raise _VIOLATION

    monkeypatch.setattr(service, "run_write", fake_run_write)
    perms.set_current_permissions(frozenset({"db-write"}))
    mcp_context.set_current_org_id(1)
    mcp_context.set_current_project_id(4)
    try:
        with caplog.at_level(logging.DEBUG, logger="src.mcp.datasources"):
            result = asyncio.run(
                _underlying(datasources.db_execute)(connection="erp-vendite", sql="UPDATE t SET a=1 WHERE id=1")
            )
    finally:
        perms.set_current_permissions(None)
        mcp_context.set_current_org_id(None)
        mcp_context.set_current_project_id(None)
    assert result == {"status": "error", "error": str(_VIOLATION)}
    assert _no_error_logs(caplog)


def test_db_execute_validator_rejection_is_not_logged_as_a_failure(monkeypatch, caplog):
    async def fake_run_write(org_id, project_id, ref, sql, source="mcp"):
        raise QueryValidationError("Query contains blocked operation: DROP.")

    monkeypatch.setattr(service, "run_write", fake_run_write)
    perms.set_current_permissions(frozenset({"db-write"}))
    mcp_context.set_current_org_id(1)
    mcp_context.set_current_project_id(4)
    try:
        with caplog.at_level(logging.DEBUG, logger="src.mcp.datasources"):
            result = asyncio.run(
                _underlying(datasources.db_execute)(connection="erp-vendite", sql="DROP TABLE t")
            )
    finally:
        perms.set_current_permissions(None)
        mcp_context.set_current_org_id(None)
        mcp_context.set_current_project_id(None)
    assert result["status"] == "error"
    assert "blocked operation" in result["error"]
    assert _no_error_logs(caplog)
