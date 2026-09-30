import asyncio

import pytest

from src import config
from src.api.deps import ActiveProject
from src.api.routes import chat
from src.catalog import selections
from src.datasources import introspect, service
from src.mcp import datasources as mcp_datasources
from src.mcp.context import set_current_org_id, set_current_project_id
from src.mcp.permissions import set_current_permissions
from tests.pgutil import execute, run_db, seed_org, seed_project

# Secondo perimetro della connessione "erp": il nome della connessione
# indicherebbe il primo, l'alias indica questo.
SCOPE = {"id": 7, "name": "erp", "alias": "erp/app.vendite", "engine": "postgresql"}


@pytest.fixture(autouse=True)
def _reset_mcp_context():
    yield
    set_current_org_id(None)
    set_current_project_id(None)
    set_current_permissions(None)


def _underlying(tool):
    return getattr(tool, "fn", tool)


def _spy(monkeypatch):
    seen = []

    async def fake_get_connection(org_id, project_id, ref, include_secret=False):
        return dict(SCOPE)

    async def fake_get_scope(org_id, project_id, ref, include_secret=False):
        return dict(SCOPE)

    async def fake_resolve(org_id, project_id, hint=None, **kwargs):
        return dict(SCOPE)

    async def fake_overview(org_id, project_id, ref, schema=None):
        seen.append(("overview", ref))
        return {"tables": []}

    async def fake_describe(org_id, project_id, ref, table, schema=None, sample_rows=0):
        seen.append(("describe", ref))
        return {"columns": []}

    async def fake_run_query(org_id, project_id, ref, sql, max_rows=100, source="mcp"):
        seen.append(("query", ref))
        return {"rows": []}

    monkeypatch.setattr(service, "get_connection", fake_get_connection)
    monkeypatch.setattr(service, "get_scope", fake_get_scope)
    monkeypatch.setattr(service, "resolve_connection", fake_resolve)
    monkeypatch.setattr(service, "schema_overview", fake_overview)
    monkeypatch.setattr(service, "describe_table", fake_describe)
    monkeypatch.setattr(service, "run_query", fake_run_query)
    return seen


def test_mcp_tools_continue_with_the_alias(monkeypatch):
    seen = _spy(monkeypatch)
    set_current_org_id(1)
    set_current_project_id(4)
    set_current_permissions(frozenset({"context-read", "db-query"}))

    by_name = asyncio.run(_underlying(mcp_datasources.db_schema)(connection="erp/app.vendite"))
    by_hint = asyncio.run(_underlying(mcp_datasources.db_query)(sql="SELECT 1", hint="vendite"))

    assert by_name["status"] == "ok" and by_hint["status"] == "ok"
    assert seen == [("overview", "erp/app.vendite"), ("query", "erp/app.vendite")]


def test_chat_database_tools_continue_with_the_alias(monkeypatch):
    seen = _spy(monkeypatch)
    org = ActiveProject(org_id=1, project_id=4, role="member", namespace="acme--alpha",
                        name="alpha", org_name="acme")

    asyncio.run(chat._get_database_schema(org, {"connection": "erp/app.vendite"}))
    asyncio.run(chat._get_database_schema(org, {"connection": "erp/app.vendite", "table": "ordini"}))
    asyncio.run(chat._query_database(org, {"hint": "vendite", "sql": "SELECT 1"}))

    assert seen == [
        ("overview", "erp/app.vendite"),
        ("describe", "erp/app.vendite"),
        ("query", "erp/app.vendite"),
    ]


def test_chat_results_name_the_alias(monkeypatch):
    _spy(monkeypatch)

    async def fake_overview(org_id, project_id, ref, schema=None):
        # Rispecchia il comportamento reale di service.schema_overview: il
        # payload nomina il perimetro (l'alias) con cui è stato interrogato.
        return {"connection": ref, "tables": [{"name": "ordini", "column_count": 3}]}

    monkeypatch.setattr(service, "schema_overview", fake_overview)
    org = ActiveProject(org_id=1, project_id=4, role="member", namespace="acme--alpha",
                        name="alpha", org_name="acme")

    rows = asyncio.run(chat._get_database_schema(org, {"connection": "erp/app.vendite"}))

    assert rows[0]["connection"] == "erp/app.vendite"


def _connection_data(**over):
    data = {"name": "erp", "engine": "postgresql", "host": "127.0.0.1", "port": 5432,
            "database_name": "app", "username": "reader", "password": "secret",
            "options": {}, "description": None, "ssh_machine_id": None, "restricted": False}
    data.update(over)
    return data


async def _seed_connection_with_two_scopes():
    """Una connessione "erp" collegata due volte allo stesso progetto: il perimetro
    dedotto tiene l'alias "erp", il secondo (confermato) l'alias "erp/app.vendite"."""
    org = await seed_org("acme")
    project = await seed_project(org, "alpha")
    conn = await service.create_connection(org, _connection_data())
    await selections.select_resource(org, project, "databases", conn["id"], None, False)
    await execute(
        "INSERT INTO project_db_scopes (project_id, db_connection_id, database_name, "
        "schema_name, alias, scope_inferred) VALUES ($1, $2, 'app', 'vendite', "
        "'erp/app.vendite', false)",
        project, conn["id"],
    )
    return org, project


def test_schema_overview_reports_the_alias_of_the_resolved_scope(pg_database, monkeypatch):
    monkeypatch.setattr(config.get_settings(), "encryption_key", "test-key")

    async def fake_resolve_engine(record):
        return object()

    def fake_get_overview(engine, schema=None):
        return {"schema": schema, "tables": []}

    monkeypatch.setattr(service, "_resolve_engine", fake_resolve_engine)
    monkeypatch.setattr(introspect, "get_overview", fake_get_overview)

    async def scenario():
        org, project = await _seed_connection_with_two_scopes()
        return await service.schema_overview(org, project, "erp/app.vendite")

    overview = run_db(scenario)
    assert overview["connection"] == "erp/app.vendite"


def test_db_list_distinguishes_two_scopes_of_one_connection(pg_database, monkeypatch):
    monkeypatch.setattr(config.get_settings(), "encryption_key", "test-key")

    async def scenario():
        org, project = await _seed_connection_with_two_scopes()
        set_current_org_id(org)
        set_current_project_id(project)
        set_current_permissions(frozenset({"context-read"}))
        return await _underlying(mcp_datasources.db_list)()

    listing = run_db(scenario)

    assert listing["count"] == 2
    # Il nome della connessione è lo stesso per entrambi: alias e perimetro
    # sono ciò che distingue una riga dall'altra.
    assert {c["name"] for c in listing["connections"]} == {"erp"}
    assert sorted((c["alias"], c["scope"]) for c in listing["connections"]) == [
        ("erp", "app"), ("erp/app.vendite", "app.vendite"),
    ]


def test_db_execute_writes_on_the_scope_of_the_alias(pg_database, monkeypatch):
    monkeypatch.setattr(config.get_settings(), "encryption_key", "test-key")

    async def fake_resolve_engine(record):
        return object()

    def fake_execute_write(engine, sql):
        return 1

    monkeypatch.setattr(service, "_resolve_engine", fake_resolve_engine)
    monkeypatch.setattr(service, "_execute_write", fake_execute_write)

    async def scenario():
        org, project = await _seed_connection_with_two_scopes()
        set_current_org_id(org)
        set_current_project_id(project)
        set_current_permissions(frozenset({"db-write"}))
        written = await _underlying(mcp_datasources.db_execute)(
            connection="erp/app.vendite", sql="UPDATE ordini SET stato = 'ok' WHERE id = 1"
        )
        listed = await _underlying(mcp_datasources.db_execute)(
            connection="__list__", sql="UPDATE ordini SET stato = 'ok' WHERE id = 1"
        )
        return written, listed

    written, listed = run_db(scenario)

    assert written["connection"] == "erp/app.vendite" and written["row_count"] == 1
    # Un riferimento che non nomina un perimetro non scrive: elenca i perimetri.
    assert listed["connections"] and "count" in listed


def test_run_query_reports_the_alias_of_the_resolved_scope(pg_database, monkeypatch):
    monkeypatch.setattr(config.get_settings(), "encryption_key", "test-key")

    async def fake_resolve_engine(record):
        return object()

    def fake_execute_readonly(engine, sql, max_rows):
        return ["one"], [{"one": 1}], False

    monkeypatch.setattr(service, "_resolve_engine", fake_resolve_engine)
    monkeypatch.setattr(service, "_execute_readonly", fake_execute_readonly)

    async def scenario():
        org, project = await _seed_connection_with_two_scopes()
        return await service.run_query(org, project, "erp/app.vendite", "SELECT 1")

    result = run_db(scenario)
    assert result["connection"] == "erp/app.vendite"
