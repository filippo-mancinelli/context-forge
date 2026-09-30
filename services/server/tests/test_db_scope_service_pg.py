import pytest

from src import config
from src.catalog import selections
from src.datasources import engines, introspect, project_scopes, service
from tests.pgutil import execute, fetch, fetchval, requires_pg, run_db, seed_org, seed_project

pytestmark = requires_pg


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    monkeypatch.setattr(config.get_settings(), "encryption_key", "test-key")


def _data(**over):
    data = {"name": "erp", "engine": "postgresql", "host": "127.0.0.1", "port": 5432,
            "database_name": "app", "username": "reader", "password": "secret",
            "options": {}, "description": None, "ssh_machine_id": None, "restricted": False}
    data.update(over)
    return data


def test_selecting_a_database_creates_the_default_scope(pg_database):
    async def scenario():
        # Il collegamento vive solo nel perimetro: la tabella precedente non serve.
        await execute("DROP TABLE IF EXISTS project_db_connections")
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        conn = await service.create_connection(org, _data(name="erp"))
        first = await selections.select_resource(org, project, "databases", conn["id"], None, False)
        # Selezionare di nuovo non duplica nulla.
        again = await selections.select_resource(org, project, "databases", conn["id"], None, False)
        scopes_rows = await fetch(
            "SELECT alias, database_name, schema_name, scope_inferred FROM project_db_scopes"
        )
        return first, again, scopes_rows

    first, again, scopes_rows = run_db(scenario)
    assert first["already_selected"] is False
    assert again["already_selected"] is True
    assert scopes_rows == [{
        "alias": "erp", "database_name": "app", "schema_name": "public",
        "scope_inferred": True,
    }]


def test_deselecting_removes_every_scope_of_that_connection(pg_database):
    async def scenario():
        await execute("DROP TABLE IF EXISTS project_db_connections")
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        conn = await service.create_connection(org, _data(name="erp"))
        await selections.select_resource(org, project, "databases", conn["id"], None, False)
        await execute(
            "INSERT INTO project_db_scopes (project_id, db_connection_id, database_name, "
            "schema_name, alias) VALUES ($1, $2, 'app', 'vendite', 'erp-vendite')",
            project, conn["id"],
        )
        removed = await selections.deselect_resource(org, project, "databases", conn["id"])
        again = await selections.deselect_resource(org, project, "databases", conn["id"])
        return removed, again, await fetch("SELECT id FROM project_db_scopes")

    removed, again, scopes_rows = run_db(scenario)
    assert removed is True and again is False and scopes_rows == []


def test_selected_ids_and_restricted_rule_read_the_scopes(pg_database):
    async def scenario():
        await execute("DROP TABLE IF EXISTS project_db_connections")
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        erp = await service.create_connection(org, _data(name="erp"))
        locked = await service.create_connection(org, _data(name="hr", database_name="hr",
                                                            restricted=True))
        await execute(
            "INSERT INTO project_db_scopes (project_id, db_connection_id, database_name, "
            "schema_name, alias) VALUES ($1, $2, 'app', 'sales', 'erp-sales')",
            project, erp["id"],
        )
        # Un perimetro già presente conta come selezione: nessun perimetro predefinito in più.
        result = await selections.select_resource(org, project, "databases", erp["id"], None, False)
        with pytest.raises(selections.RestrictedResourceError):
            await selections.select_resource(org, project, "databases", locked["id"], None, False)
        ids = await selections.selected_ids(project, "databases")
        count = await fetchval("SELECT count(*) FROM project_db_scopes")
        return result, ids, count, erp["id"]

    result, ids, count, erp_id = run_db(scenario)
    assert result["already_selected"] is True
    assert ids == {erp_id}
    assert count == 1


def test_project_rows_carry_alias_and_scope(pg_database):
    async def scenario():
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        conn = await service.create_connection(org, _data())
        await selections.select_resource(org, project, "databases", conn["id"], None, False)
        listed = await service.list_connections(org, project)
        single = await service.get_connection(org, project, "erp")
        return listed, single

    listed, single = run_db(scenario)
    assert len(listed) == 1
    row = listed[0]
    # I campi di oggi restano tutti al loro posto.
    assert row["name"] == "erp" and row["engine"] == "postgresql" and row["host"] == "127.0.0.1"
    assert row["has_password"] is True and row["annotation_count"] == 0
    # I campi del perimetro si aggiungono.
    assert row["alias"] == "erp"
    assert row["scope_database"] == "app" and row["scope_schema"] == "public"
    # Il perimetro appena selezionato è dedotto: l'etichetta descrive la
    # connessione così com'è, senza uno schema che nessuno ha scelto.
    assert row["scope_label"] == "app"
    assert isinstance(row["scope_id"], int)
    assert single["scope_id"] == row["scope_id"]


def test_two_projects_see_their_own_scope_of_the_same_connection(pg_database):
    async def scenario():
        org = await seed_org("acme")
        alpha = await seed_project(org, "alpha")
        beta = await seed_project(org, "beta")
        conn = await service.create_connection(org, _data())
        for project, schema in ((alpha, "vendite"), (beta, "acquisti")):
            await execute(
                "INSERT INTO project_db_scopes (project_id, db_connection_id, database_name, "
                "schema_name, alias) VALUES ($1, $2, 'app', $3, 'erp')",
                project, conn["id"], schema,
            )
        return (
            await service.get_connection(org, alpha, "erp"),
            await service.get_connection(org, beta, "erp"),
        )

    first, second = run_db(scenario)
    assert first["scope_schema"] == "vendite"
    assert second["scope_schema"] == "acquisti"


def test_engine_is_built_on_the_scope(pg_database, monkeypatch):
    captured = {}

    def fake_get_engine(connection_id, engine, url, scope_key=()):
        captured["url"] = url
        captured["scope_key"] = scope_key
        return object()

    monkeypatch.setattr(engines, "get_engine", fake_get_engine)

    async def scenario():
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        conn = await service.create_connection(org, _data(database_name="app"))
        await execute(
            "INSERT INTO project_db_scopes (project_id, db_connection_id, database_name, "
            "schema_name, alias, scope_inferred) VALUES ($1, $2, 'altro', 'vendite', 'erp', false)",
            project, conn["id"],
        )
        record = await service.get_connection(org, project, "erp", include_secret=True)
        await service._resolve_engine(record)

    run_db(scenario)
    # Il database della URL è quello del perimetro, non quello della connessione.
    assert captured["url"].database == "altro"
    assert captured["url"].query["options"] == '-csearch_path="vendite"'
    assert captured["scope_key"] == ("altro", "vendite")


@pytest.mark.parametrize("inferred, logged", [(False, "vendite"), (True, None)])
def test_query_log_records_the_schema_of_a_confirmed_scope(pg_database, inferred, logged):
    async def scenario():
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        conn = await service.create_connection(org, _data())
        await execute(
            "INSERT INTO project_db_scopes (project_id, db_connection_id, database_name, "
            "schema_name, alias, scope_inferred) VALUES ($1, $2, 'app', 'vendite', 'erp', $3)",
            project, conn["id"], inferred,
        )
        record = await service.get_connection(org, project, "erp")
        await service._log_query(record, org, project, "ui", "SELECT 1 LIMIT 1",
                                 True, None, 1, 3)
        return await fetch("SELECT schema_name, sql_text FROM db_query_log")

    rows = run_db(scenario)
    # Un perimetro dedotto non è una scelta: il log non gli attribuisce uno schema.
    assert rows == [{"schema_name": logged, "sql_text": "SELECT 1 LIMIT 1"}]


def test_inferred_scope_follows_the_connection(pg_database, monkeypatch):
    captured = {}

    def fake_get_engine(connection_id, engine, url, scope_key=()):
        captured["url"] = url
        captured["scope_key"] = scope_key
        return object()

    monkeypatch.setattr(engines, "get_engine", fake_get_engine)

    async def scenario():
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        conn = await service.create_connection(org, _data(database_name="app"))
        await selections.select_resource(org, project, "databases", conn["id"], None, False)
        # Il database della connessione cambia dopo la selezione.
        await service.update_connection(org, conn["id"], _data(database_name="nuovo"))
        record = await service.get_connection(org, project, "erp", include_secret=True)
        await service._resolve_engine(record)
        return record

    record = run_db(scenario)
    assert record["scope_inferred"] is True and record["scope_database"] == "app"
    # La URL segue la connessione, senza restringere il search_path.
    assert captured["url"].database == "nuovo"
    assert "options" not in captured["url"].query
    assert captured["scope_key"] == service._scope_key(record) == ("nuovo", None)


def _introspection_spy(monkeypatch):
    seen = []
    monkeypatch.setattr(engines, "get_engine", lambda *args, **kwargs: object())

    def fake_overview(engine, schema=None):
        seen.append(("overview", schema))
        return {"schema": schema, "tables": []}

    def fake_describe(engine, table, schema=None):
        seen.append(("describe", schema))
        return {"schema": schema, "columns": []}

    monkeypatch.setattr(introspect, "get_overview", fake_overview)
    monkeypatch.setattr(introspect, "describe_table", fake_describe)
    return seen


@pytest.mark.parametrize("inferred, default", [(False, "vendite"), (True, None)])
def test_introspection_defaults_to_a_confirmed_scope_schema(pg_database, monkeypatch,
                                                            inferred, default):
    seen = _introspection_spy(monkeypatch)

    async def scenario():
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        conn = await service.create_connection(org, _data())
        await execute(
            "INSERT INTO project_db_scopes (project_id, db_connection_id, database_name, "
            "schema_name, alias, scope_inferred) VALUES ($1, $2, 'app', 'vendite', 'erp', $3)",
            project, conn["id"], inferred,
        )
        await service.schema_overview(org, project, "erp")
        await service.describe_table(org, project, "erp", "ordini")

    run_db(scenario)
    assert seen == [("overview", default), ("describe", default)]


def test_confirmed_scope_label_names_database_and_schema(pg_database):
    async def scenario():
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        conn = await service.create_connection(org, _data())
        await execute(
            "INSERT INTO project_db_scopes (project_id, db_connection_id, database_name, "
            "schema_name, alias, scope_inferred) VALUES ($1, $2, 'app', 'vendite', 'erp', false)",
            project, conn["id"],
        )
        return await service.list_connections(org, project)

    rows = run_db(scenario)
    assert rows[0]["scope_label"] == "app.vendite"


def test_inferred_scope_label_follows_the_connection(pg_database):
    async def scenario():
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        conn = await service.create_connection(org, _data(database_name="app"))
        await selections.select_resource(org, project, "databases", conn["id"], None, False)
        await service.update_connection(org, conn["id"], _data(database_name="nuovo"))
        return await service.get_connection(org, project, "erp")

    record = run_db(scenario)
    # Il perimetro dedotto conserva il valore della selezione, ma la connessione
    # lavora sul database attuale: l'etichetta mostra quello.
    assert record["scope_database"] == "app"
    assert record["scope_label"] == "nuovo"


def test_project_scopes_live_without_the_legacy_links(pg_database):
    async def scenario():
        await execute("DROP TABLE IF EXISTS project_db_connections")
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        conn = await service.create_connection(org, _data())
        await project_scopes.create_scope(org, project, conn["id"], "app", "public", None, None, False)
        await project_scopes.create_scope(org, project, conn["id"], "app", "sales", "erp-sales", None, False)
        created = [r["id"] for r in await fetch("SELECT id FROM project_db_scopes ORDER BY id")]
        # Cade prima un perimetro e poi l'ultimo della connessione: nessuno dei due
        # passa più dalla tabella dei collegamenti.
        for scope_id in created:
            await project_scopes.delete_scope(org, project, scope_id)
        return created, await fetch("SELECT id FROM project_db_scopes")

    created, remaining = run_db(scenario)
    assert len(created) == 2
    assert remaining == []
