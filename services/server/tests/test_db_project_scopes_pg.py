import pytest

from src import config
from src.catalog import selections
from src.datasources import project_scopes, service
from src.datasources.project_scopes import AliasError, ScopeConflictError, ScopeNotFoundError
from src.datasources.scope_migration import apply_db_scope_migration
from src.datasources.scopes import ScopeShapeError
from tests.pgutil import execute, fetch, requires_pg, run_db, seed_org, seed_project

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


def test_create_confirms_the_scope_and_counts_its_own_scope_rows(pg_database):
    async def scenario():
        await execute("DROP TABLE IF EXISTS project_db_connections")
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        conn = await service.create_connection(org, _data())
        first = await project_scopes.create_scope(org, project, conn["id"], "app", "vendite", None, None, False)
        second = await project_scopes.create_scope(org, project, conn["id"], "app", None, None, None, False)
        scopes = await fetch(
            "SELECT id FROM project_db_scopes WHERE project_id = $1 AND db_connection_id = $2",
            project, conn["id"],
        )
        return conn, first, second, scopes

    conn, first, second, scopes = run_db(scenario)
    assert first["id"] == conn["id"] and isinstance(first["scope_id"], int)
    assert first["alias"] == "erp" and first["scope_label"] == "app.vendite"
    assert first["scope_inferred"] is False
    # Lo schema mancante su PostgreSQL è public; l'alias dice quale perimetro è.
    assert second["scope_schema"] == "public" and second["alias"] == "erp/app.public"
    # Una riga di perimetro per ciascuna scelta, senza nessun'altra tabella coinvolta.
    assert len(scopes) == 2


def test_create_can_mark_the_scope_as_inferred(pg_database):
    """Un perimetro che nessuno ha scelto nasce dedotto: resta enforcement
    morbido e tiene l'avviso di verificarlo. Il valore predefinito del
    parametro lascia confermato ogni altro chiamante."""
    async def scenario():
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        conn = await service.create_connection(org, _data())
        guessed = await project_scopes.create_scope(
            org, project, conn["id"], "app", "public", None, None, False, inferred=True
        )
        chosen = await project_scopes.create_scope(
            org, project, conn["id"], "app", "vendite", None, None, False
        )
        return guessed, chosen

    guessed, chosen = run_db(scenario)
    assert guessed["scope_inferred"] is True
    assert chosen["scope_inferred"] is False


def test_duplicates_bad_shapes_and_bad_aliases_are_refused(pg_database):
    async def scenario():
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        conn = await service.create_connection(org, _data())
        crm = await service.create_connection(
            org, _data(name="crm", engine="mysql", port=3306, database_name="crm")
        )
        await project_scopes.create_scope(org, project, conn["id"], "app", "vendite", "erp", None, False)
        refused = []
        for connection_id, database, schema, alias in (
            (conn["id"], "app", "vendite", "altro"),   # stesso perimetro
            (conn["id"], "app", "acquisti", "ERP"),    # alias occupato, senza maiuscole
            (crm["id"], "crm", "altro", None),         # MySQL non ha schemi separati
            (crm["id"], "crm", None, "42"),            # un numero sembrerebbe un id
            (crm["id"], "crm", None, "x" * 101),       # alias troppo lungo
        ):
            try:
                await project_scopes.create_scope(
                    org, project, connection_id, database, schema, alias, None, False
                )
            except Exception as exc:  # noqa: BLE001 - il test raccoglie il tipo
                refused.append(type(exc))
        return refused

    assert run_db(scenario) == [
        ScopeConflictError, ScopeConflictError, ScopeShapeError, AliasError, AliasError,
    ]


def test_restricted_connection_needs_the_right(pg_database):
    async def scenario():
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        conn = await service.create_connection(org, _data(restricted=True))
        with pytest.raises(selections.RestrictedResourceError):
            await project_scopes.create_scope(org, project, conn["id"], "app", None, None, None, False)
        with pytest.raises(selections.ResourceNotFoundError):
            await project_scopes.create_scope(org, project, 999999, "app", None, None, None, True)
        return await project_scopes.create_scope(org, project, conn["id"], "app", None, None, None, True)

    assert run_db(scenario)["alias"] == "erp"


def test_update_confirms_an_inferred_scope(pg_database):
    async def scenario():
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        conn = await service.create_connection(org, _data())
        await selections.select_resource(org, project, "databases", conn["id"], None, False)
        row = (await service.list_connections(org, project))[0]
        updated = await project_scopes.update_scope(org, project, row["scope_id"], "app", "vendite", "")
        return row, updated

    row, updated = run_db(scenario)
    assert row["scope_inferred"] is True
    assert updated["scope_inferred"] is False and updated["scope_schema"] == "vendite"
    assert updated["scope_label"] == "app.vendite"
    # Un alias vuoto lascia quello di prima.
    assert updated["alias"] == "erp" and updated["scope_id"] == row["scope_id"]


def test_update_refuses_an_alias_or_a_scope_already_used(pg_database):
    async def scenario():
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        conn = await service.create_connection(org, _data())
        await project_scopes.create_scope(org, project, conn["id"], "app", "vendite", None, None, False)
        second = await project_scopes.create_scope(org, project, conn["id"], "app", "acquisti", None, None, False)
        with pytest.raises(ScopeConflictError):
            await project_scopes.update_scope(org, project, second["scope_id"], "app", "acquisti", "erp")
        with pytest.raises(ScopeConflictError):
            await project_scopes.update_scope(org, project, second["scope_id"], "app", "vendite", "altro")
        # Salvare lo stesso perimetro con il proprio alias non è un conflitto.
        return await project_scopes.update_scope(
            org, project, second["scope_id"], "app", "acquisti", second["alias"]
        )

    assert run_db(scenario)["alias"] == "erp/app.acquisti"


def test_delete_unlinks_the_connection_with_its_last_scope(pg_database):
    async def scenario():
        await execute("DROP TABLE IF EXISTS project_db_connections")
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        conn = await service.create_connection(org, _data())
        a = await project_scopes.create_scope(org, project, conn["id"], "app", "vendite", None, None, False)
        b = await project_scopes.create_scope(org, project, conn["id"], "app", "acquisti", None, None, False)
        await project_scopes.delete_scope(org, project, a["scope_id"])
        after_first = await fetch(
            "SELECT id FROM project_db_scopes WHERE project_id = $1 AND db_connection_id = $2",
            project, conn["id"],
        )
        await project_scopes.delete_scope(org, project, b["scope_id"])
        after_last = await fetch(
            "SELECT id FROM project_db_scopes WHERE project_id = $1 AND db_connection_id = $2",
            project, conn["id"],
        )
        with pytest.raises(ScopeNotFoundError):
            await project_scopes.delete_scope(org, project, b["scope_id"])
        return after_first, after_last

    after_first, after_last = run_db(scenario)
    assert len(after_first) == 1
    assert after_last == []


def test_a_long_connection_name_yields_an_alias_the_update_accepts_unchanged(pg_database):
    async def scenario():
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        conn = await service.create_connection(org, _data(name="x" * 95))
        await project_scopes.create_scope(org, project, conn["id"], "app", "vendite", None, None, False)
        second = await project_scopes.create_scope(org, project, conn["id"], "app", "acquisti", None, None, False)
        # Lo stesso alias generato, salvato di nuovo senza cambiarlo, non si rifiuta.
        return await project_scopes.update_scope(
            org, project, second["scope_id"], "app", "acquisti", second["alias"]
        )

    updated = run_db(scenario)
    assert len(updated["alias"]) <= project_scopes.ALIAS_MAX_LENGTH


def test_a_numeric_connection_name_gets_a_non_numeric_default_alias(pg_database):
    async def scenario():
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        conn = await service.create_connection(org, _data(name="42"))
        return await project_scopes.create_scope(org, project, conn["id"], "app", None, None, None, False)

    assert not run_db(scenario)["alias"].isdigit()


def test_scopes_are_out_of_reach_from_another_organization(pg_database):
    async def scenario():
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        conn = await service.create_connection(org, _data())
        scope = await project_scopes.create_scope(org, project, conn["id"], "app", None, None, None, False)

        # Un'altra organizzazione, con un suo progetto e una sua connessione:
        # non deve bastare a raggiungere il perimetro della prima.
        other_org = await seed_org("beta-inc")
        await seed_project(other_org, "beta")
        await service.create_connection(other_org, _data(name="other-erp"))

        with pytest.raises(ScopeNotFoundError):
            await project_scopes.update_scope(other_org, project, scope["scope_id"], "app", "vendite", None)
        with pytest.raises(ScopeNotFoundError):
            await project_scopes.delete_scope(other_org, project, scope["scope_id"])
        return await fetch("SELECT schema_name FROM project_db_scopes")

    assert run_db(scenario) == [{"schema_name": "public"}]


def test_scopes_of_another_project_are_out_of_reach(pg_database):
    async def scenario():
        org = await seed_org("acme")
        alpha = await seed_project(org, "alpha")
        beta = await seed_project(org, "beta")
        conn = await service.create_connection(org, _data())
        scope = await project_scopes.create_scope(org, alpha, conn["id"], "app", None, None, None, False)
        with pytest.raises(ScopeNotFoundError):
            await project_scopes.update_scope(org, beta, scope["scope_id"], "app", "vendite", None)
        with pytest.raises(ScopeNotFoundError):
            await project_scopes.delete_scope(org, beta, scope["scope_id"])
        return await fetch("SELECT schema_name FROM project_db_scopes")

    assert run_db(scenario) == [{"schema_name": "public"}]


def test_startup_migration_keeps_scopes_without_the_legacy_link(pg_database):
    async def scenario():
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        conn = await service.create_connection(org, _data())
        await project_scopes.create_scope(org, project, conn["id"], "app", "vendite", None, None, False)
        await project_scopes.create_scope(org, project, conn["id"], "app", "acquisti", None, None, False)
        report = await apply_db_scope_migration()
        aliases = await fetch("SELECT alias FROM project_db_scopes ORDER BY alias")
        return report, aliases

    report, aliases = run_db(scenario)
    # Un perimetro nato dalla selezione, senza riga legacy, è il collegamento a
    # tutti gli effetti: la migrazione lo lascia stare.
    assert report["scopes"] == 0
    assert aliases == [{"alias": "erp"}, {"alias": "erp/app.acquisti"}]
