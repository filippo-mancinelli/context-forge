import asyncio
import logging

import pytest

from src import config
from src.catalog import migration
from src.datasources import service
from src.datasources.project_scopes import delete_scope
from src.datasources.scope_migration import apply_db_scope_migration
from tests.pgutil import (
    execute, fetch, fetchval, make_legacy_db_links, make_legacy_schema, requires_pg, run_db,
    seed_org, seed_project,
)

pytestmark = requires_pg

LEGACY_EXISTS = "SELECT to_regclass('project_db_connections') IS NOT NULL"


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    monkeypatch.setattr(config.get_settings(), "encryption_key", "test-key")


def _data(**over):
    data = {"name": "erp", "engine": "postgresql", "host": "127.0.0.1", "port": 5432,
            "database_name": "app", "username": "reader", "password": "secret",
            "options": {}, "description": None, "ssh_machine_id": None, "restricted": False}
    data.update(over)
    return data


async def _unsupported_connection(org: int) -> int:
    """Connessione su un motore non più supportato: non riceve un perimetro dedotto."""
    return await fetchval(
        "INSERT INTO db_connections (org_id, name, engine, database_name) "
        "VALUES ($1, 'ledger', 'oracle', 'XE') RETURNING id",
        org,
    )


async def _link(project: int, connection_id: int) -> None:
    await execute(
        "INSERT INTO project_db_connections (project_id, db_connection_id) VALUES ($1, $2)",
        project, connection_id,
    )


def test_fresh_schema_has_no_legacy_links(pg_database):
    async def scenario():
        before = await fetchval(LEGACY_EXISTS)
        report = await apply_db_scope_migration()
        return before, report

    before, report = run_db(scenario)
    assert before is False
    assert report["legacy_links"] == "absent"


def test_legacy_links_are_dropped_once_every_link_has_a_scope(pg_database):
    async def scenario():
        await make_legacy_db_links()
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        conn = await service.create_connection(org, _data())
        await _link(project, conn["id"])
        first = await apply_db_scope_migration()
        after_first = await fetchval(LEGACY_EXISTS)
        second = await apply_db_scope_migration()
        scopes = await fetch("SELECT alias, scope_inferred FROM project_db_scopes")
        return first, after_first, second, scopes

    first, after_first, second, scopes = run_db(scenario)
    # Nella stessa transazione il collegamento diventa perimetro e la tabella sparisce.
    assert first["scopes"] == 1 and first["legacy_links"] == "dropped"
    assert after_first is False
    assert second["scopes"] == 0 and second["legacy_links"] == "absent"
    assert scopes == [{"alias": "erp", "scope_inferred": True}]


def test_two_instances_starting_together_do_not_race_the_drop(pg_database):
    async def scenario():
        await make_legacy_db_links()
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        conn = await service.create_connection(org, _data())
        await _link(project, conn["id"])
        # Due avvii sovrapposti: una delle due transazioni rimuove la tabella,
        # l'altra aspetta il suo turno e non deve fallire.
        first, second = await asyncio.gather(
            apply_db_scope_migration(), apply_db_scope_migration()
        )
        return first, second, await fetchval("SELECT count(*) FROM project_db_scopes"), \
            await fetchval(LEGACY_EXISTS)

    first, second, scopes, exists = run_db(scenario)
    # Chi arriva prima converte e rimuove, chi arriva dopo trova il lavoro fatto.
    assert sorted([first["legacy_links"], second["legacy_links"]]) == ["absent", "dropped"]
    assert first["scopes"] + second["scopes"] == 1
    assert scopes == 1 and exists is False


def test_legacy_links_stay_when_a_link_cannot_get_a_scope(pg_database, caplog):
    caplog.set_level(logging.WARNING, logger="src.datasources.scope_migration")

    async def scenario():
        await make_legacy_db_links()
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        oracle = await _unsupported_connection(org)
        await _link(project, oracle)
        report = await apply_db_scope_migration()
        return report, await fetchval(LEGACY_EXISTS), \
            await fetchval("SELECT count(*) FROM project_db_connections")

    report, exists, links = run_db(scenario)
    assert report["legacy_links"] == "kept"
    assert report["skipped"] and report["skipped"][0].startswith("ledger:")
    assert exists is True and links == 1
    assert "1 links have no scope" in caplog.text


def test_a_kept_table_does_not_resurrect_a_removed_scope(pg_database):
    async def scenario():
        await make_legacy_db_links()
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        conn = await service.create_connection(org, _data())
        # Il motore non supportato tiene in vita la tabella dei collegamenti.
        await _link(project, conn["id"])
        await _link(project, await _unsupported_connection(org))
        first = await apply_db_scope_migration()
        scope_id = await fetchval(
            "SELECT id FROM project_db_scopes WHERE project_id = $1", project
        )
        # Una persona toglie il database dal progetto.
        await delete_scope(org, project, scope_id)
        second = await apply_db_scope_migration()
        return first, second, await fetch("SELECT alias FROM project_db_scopes"), \
            await fetchval(LEGACY_EXISTS), \
            await fetchval("SELECT count(*) FROM project_db_connections")

    first, second, scopes, exists, links = run_db(scenario)
    assert first["scopes"] == 1 and first["legacy_links"] == "kept"
    # Il collegamento convertito è già stato dimenticato: resta solo quello senza perimetro.
    assert exists is True and links == 1
    # La tabella resta, ma non fa ricomparire il perimetro che la persona ha rimosso.
    assert second["scopes"] == 0 and second["legacy_links"] == "kept"
    assert scopes == []


def test_legacy_catalog_chain_ends_without_the_legacy_table(pg_database):
    async def scenario():
        await make_legacy_schema()
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        await execute(
            "INSERT INTO db_connections (org_id, project_id, name, engine, host, port, database_name) "
            "VALUES ($1, $2, 'chat-db', 'mysql', '127.0.0.1', 3306, 'asterchat')",
            org, project,
        )
        await migration.apply_catalog_migration()
        # La conversione del catalogo passa ancora dai collegamenti legacy.
        links = await fetchval("SELECT count(*) FROM project_db_connections")
        report = await apply_db_scope_migration()
        scopes = await fetch(
            "SELECT alias, database_name, schema_name, scope_inferred FROM project_db_scopes"
        )
        return links, report, scopes, await fetchval(LEGACY_EXISTS)

    links, report, scopes, exists = run_db(scenario)
    assert links == 1
    assert report["legacy_links"] == "dropped"
    assert scopes == [{"alias": "chat-db", "database_name": "asterchat", "schema_name": None,
                       "scope_inferred": True}]
    assert exists is False
