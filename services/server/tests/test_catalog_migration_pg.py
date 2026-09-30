from datetime import datetime, timezone

import pytest

from src import config
from src.catalog import migration
from src.datasources.secrets import decrypt_secret, encrypt_secret
from tests.pgutil import (
    execute, fetch, fetchval, make_legacy_schema, requires_pg, run_db, seed_org, seed_project,
)

pytestmark = requires_pg

EMPTY_REPORT = {"machines": 0, "secret_replaced": [], "renamed": []}


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    # Con Fernet lo stesso segreto produce cifrati diversi: la migrazione deve
    # confrontare i valori in chiaro.
    monkeypatch.setattr(config.get_settings(), "encryption_key", "test-key")


async def _legacy_source(org_id, project_id, name, host, username, *, port=22, auth="key",
                         password="", key="", status="unknown", checked=None):
    return await fetchval(
        """
        INSERT INTO ssh_sources (org_id, project_id, name, host, port, username, auth_method,
                                 password_enc, private_key_enc, root_path, status, last_checked_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, '/srv', $10, $11) RETURNING id
        """,
        org_id, project_id, name, host, port, username, auth,
        encrypt_secret(password), encrypt_secret(key), status, checked,
    )


async def _legacy_database(org_id, project_id, name, *, ssh_host=None, username="astercare", key=""):
    return await fetchval(
        """
        INSERT INTO db_connections (org_id, project_id, name, engine, host, port, database_name,
                                    ssh_enabled, ssh_host, ssh_port, ssh_username, ssh_auth_method,
                                    ssh_private_key_enc)
        VALUES ($1, $2, $3, 'mysql', '127.0.0.1', 3306, 'app', $4, $5, 22, $6, 'key', $7) RETURNING id
        """,
        org_id, project_id, name, ssh_host is not None, ssh_host, username, encrypt_secret(key),
    )


def test_rows_sharing_host_port_and_user_become_one_machine(pg_database):
    async def scenario():
        await make_legacy_schema()
        org = await seed_org("acme")
        p1 = await seed_project(org, "alpha")
        p2 = await seed_project(org, "beta")
        s1 = await _legacy_source(org, p1, "chat-home", "10.0.0.6", "astercare", key="K1")
        s2 = await _legacy_source(org, p2, "self-home", "10.0.0.6", "astercare", key="K1")
        s3 = await _legacy_source(org, p1, "logs", "10.0.0.9", "larkin", auth="password", password="P")
        d1 = await _legacy_database(org, p1, "chat-db", ssh_host="10.0.0.6", key="K1")
        report = await migration.apply_catalog_migration()
        return {
            "report": report,
            "ids": (p1, p2, s1, s2, s3, d1),
            "machines": [r["name"] for r in await fetch("SELECT name FROM machines ORDER BY name")],
            "sources": {r["id"]: r["machine_id"] for r in await fetch("SELECT id, machine_id FROM ssh_sources")},
            "tunnel": await fetchval("SELECT ssh_machine_id FROM db_connections WHERE id = $1", d1),
            "selected_sources": {(r["project_id"], r["ssh_source_id"])
                                 for r in await fetch("SELECT project_id, ssh_source_id FROM project_ssh_sources")},
            "selected_dbs": {(r["project_id"], r["db_connection_id"])
                             for r in await fetch("SELECT project_id, db_connection_id FROM project_db_connections")},
            "legacy": await fetch(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE (table_name = 'ssh_sources' AND column_name IN "
                "('project_id', 'host', 'port', 'username', 'auth_method', 'password_enc', 'private_key_enc')) "
                "OR (table_name = 'db_connections' AND column_name IN "
                "('project_id', 'ssh_enabled', 'ssh_host', 'ssh_port', 'ssh_username', "
                "'ssh_auth_method', 'ssh_password_enc', 'ssh_private_key_enc'))"
            ),
        }

    out = run_db(scenario)
    p1, p2, s1, s2, s3, d1 = out["ids"]
    assert out["report"]["machines"] == 2
    assert out["machines"] == ["astercare@10.0.0.6", "larkin@10.0.0.9"]
    assert out["sources"][s1] == out["sources"][s2] == out["tunnel"]
    assert out["sources"][s3] != out["sources"][s1]
    assert out["selected_sources"] == {(p1, s1), (p2, s2), (p1, s3)}
    assert out["selected_dbs"] == {(p1, d1)}
    assert out["legacy"] == []


def test_most_used_secret_wins_and_replaced_rows_are_reported(pg_database):
    async def scenario():
        await make_legacy_schema()
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        for index in range(3):
            await _legacy_source(org, project, f"s{index}", "10.0.0.6", "astercare", key="K1")
        odd = await _legacy_source(org, project, "odd", "10.0.0.6", "astercare", key="K2")
        report = await migration.apply_catalog_migration()
        return report, await fetchval("SELECT private_key_enc FROM machines"), odd

    report, stored, odd = run_db(scenario)
    assert decrypt_secret(stored) == "K1"
    assert report["secret_replaced"] == [f"ssh_sources#{odd}"]


def test_tie_goes_to_the_latest_successful_check(pg_database):
    async def scenario():
        await make_legacy_schema()
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        await _legacy_source(org, project, "old", "10.0.0.6", "astercare", key="K1", status="ok",
                             checked=datetime(2026, 7, 31, tzinfo=timezone.utc))
        await _legacy_source(org, project, "new", "10.0.0.6", "astercare", key="K2", status="ok",
                             checked=datetime(2026, 8, 6, tzinfo=timezone.utc))
        await migration.apply_catalog_migration()
        return await fetchval("SELECT private_key_enc FROM machines")

    assert decrypt_secret(run_db(scenario)) == "K2"


def test_same_name_in_two_projects_is_renamed_with_the_project_slug(pg_database):
    async def scenario():
        await make_legacy_schema()
        org = await seed_org("acme")
        alpha = await seed_project(org, "alpha")
        beta = await seed_project(org, "beta")
        first = await _legacy_source(org, alpha, "logs", "10.0.0.6", "astercare", key="K1")
        second = await _legacy_source(org, beta, "Logs", "10.0.0.6", "astercare", key="K1")
        report = await migration.apply_catalog_migration()
        names = {r["id"]: r["name"] for r in await fetch("SELECT id, name FROM ssh_sources")}
        return report, names, first, second

    report, names, first, second = run_db(scenario)
    assert names[first] == "logs"
    assert names[second] == "Logs-beta"
    assert report["renamed"] == [f"ssh_sources#{second}: Logs -> Logs-beta"]


def test_direct_database_keeps_no_machine_and_is_selected(pg_database):
    async def scenario():
        await make_legacy_schema()
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        erp = await _legacy_database(org, project, "erp")
        await migration.apply_catalog_migration()
        machine = await fetchval("SELECT ssh_machine_id FROM db_connections WHERE id = $1", erp)
        selected = await fetchval(
            "SELECT count(*) FROM project_db_connections WHERE project_id = $1 AND db_connection_id = $2",
            project, erp,
        )
        return machine, selected

    assert run_db(scenario) == (None, 1)


def test_second_run_changes_nothing(pg_database):
    async def scenario():
        await make_legacy_schema()
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        await _legacy_source(org, project, "logs", "10.0.0.6", "astercare", key="K1")
        await migration.apply_catalog_migration()
        second = await migration.apply_catalog_migration()
        counts = (
            await fetchval("SELECT count(*) FROM machines"),
            await fetchval("SELECT count(*) FROM project_ssh_sources"),
        )
        return second, counts

    second, counts = run_db(scenario)
    assert second == EMPTY_REPORT
    assert counts == (1, 1)


def test_rows_without_a_project_or_an_ssh_user_do_not_stop_the_migration(pg_database):
    async def scenario():
        await make_legacy_schema()
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        gone = await seed_project(org, "gone")
        orphan = await _legacy_database(org, gone, "orphan", ssh_host="10.0.0.6", key="K1")
        anonymous = await _legacy_database(org, project, "anonymous", ssh_host="10.0.0.7",
                                           username=None, key="K1")
        source = await _legacy_source(org, project, "logs", "10.0.0.6", "astercare", key="K1")
        # db_connections.project_id non ha vincolo: la riga sopravvive al progetto.
        await execute("DELETE FROM projects WHERE id = $1", gone)
        report = await migration.apply_catalog_migration()
        return {
            "report": report,
            "ids": (project, orphan, anonymous, source),
            "machines": [r["name"] for r in await fetch("SELECT name FROM machines ORDER BY name")],
            "tunnels": {r["id"]: r["ssh_machine_id"]
                        for r in await fetch("SELECT id, ssh_machine_id FROM db_connections")},
            "selected_dbs": {(r["project_id"], r["db_connection_id"])
                             for r in await fetch("SELECT project_id, db_connection_id FROM project_db_connections")},
            "selected_sources": {(r["project_id"], r["ssh_source_id"])
                                 for r in await fetch("SELECT project_id, ssh_source_id FROM project_ssh_sources")},
        }

    out = run_db(scenario)
    project, orphan, anonymous, source = out["ids"]
    assert out["report"]["machines"] == 1
    assert out["machines"] == ["astercare@10.0.0.6"]
    # Senza utenza SSH la connessione resta diretta; quella del progetto cancellato
    # tiene la macchina ma non diventa una selezione.
    assert out["tunnels"][anonymous] is None
    assert out["tunnels"][orphan] is not None
    assert out["selected_dbs"] == {(project, anonymous)}
    assert out["selected_sources"] == {(project, source)}


def test_fresh_schema_is_a_no_op(pg_database):
    assert run_db(migration.apply_catalog_migration) == EMPTY_REPORT
