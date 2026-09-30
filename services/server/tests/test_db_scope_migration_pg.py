from src.datasources import service
from src.datasources.scope_migration import apply_db_scope_migration
from tests.pgutil import (
    execute, fetch, fetchval, make_legacy_db_links, requires_pg, run_db, seed_org, seed_project,
)

pytestmark = requires_pg


def test_scope_table_and_indexes_exist(pg_database):
    async def scenario():
        columns = await fetch(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'project_db_scopes' ORDER BY column_name"
        )
        indexes = await fetch(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'project_db_scopes' "
            "ORDER BY indexname"
        )
        return columns, indexes

    columns, indexes = run_db(scenario)
    names = [c["column_name"] for c in columns]
    assert names == [
        "added_at", "added_by", "alias", "database_name", "db_connection_id",
        "id", "project_id", "schema_name", "scope_inferred",
    ]
    nullable = {c["column_name"]: c["is_nullable"] for c in columns}
    assert nullable["database_name"] == "YES" and nullable["schema_name"] == "YES"
    assert nullable["alias"] == "NO"
    assert [i["indexname"] for i in indexes] == [
        "project_db_scopes_alias_idx",
        "project_db_scopes_connection_idx",
        "project_db_scopes_pkey",
        "project_db_scopes_unique_idx",
    ]


def _data(**over):
    data = {"name": "erp", "engine": "postgresql", "host": "127.0.0.1", "port": 5432,
            "database_name": "app", "username": "reader", "password": "secret",
            "options": {}, "description": None, "ssh_machine_id": None, "restricted": False}
    data.update(over)
    return data


def test_existing_links_become_inferred_scopes(pg_database):
    async def scenario():
        await make_legacy_db_links()
        org = await seed_org("acme")
        alpha = await seed_project(org, "alpha")
        beta = await seed_project(org, "beta")
        pg = await service.create_connection(org, _data(name="erp"))
        my = await service.create_connection(org, _data(name="crm", engine="mysql", port=3306,
                                                        database_name="crm"))
        lite = await service.create_connection(org, _data(name="local", engine="sqlite",
                                                          host=None, port=None,
                                                          database_name="/data/a.db"))
        for project in (alpha, beta):
            for conn in (pg, my, lite):
                await execute(
                    "INSERT INTO project_db_connections (project_id, db_connection_id) VALUES ($1, $2)",
                    project, conn["id"],
                )
        report = await apply_db_scope_migration()
        rows = await fetch(
            "SELECT p.slug, c.name, s.database_name, s.schema_name, s.alias, s.scope_inferred "
            "FROM project_db_scopes s JOIN projects p ON p.id = s.project_id "
            "JOIN db_connections c ON c.id = s.db_connection_id "
            "ORDER BY p.slug, c.name"
        )
        return report, rows

    report, rows = run_db(scenario)
    assert report["scopes"] == 6
    by_name = {(r["slug"], r["name"]): r for r in rows}
    # PostgreSQL: database della connessione più lo schema di default.
    assert by_name[("alpha", "erp")]["database_name"] == "app"
    assert by_name[("alpha", "erp")]["schema_name"] == "public"
    # MySQL: solo il database.
    assert by_name[("alpha", "crm")]["database_name"] == "crm"
    assert by_name[("alpha", "crm")]["schema_name"] is None
    # SQLite: nessun perimetro.
    assert by_name[("beta", "local")]["database_name"] is None
    assert by_name[("beta", "local")]["schema_name"] is None
    # L'alias parte dal nome della connessione, uguale in ogni progetto.
    assert by_name[("beta", "erp")]["alias"] == "erp"
    assert all(r["scope_inferred"] is True for r in rows)


def test_migration_is_idempotent_and_keeps_confirmed_scopes(pg_database):
    async def scenario():
        await make_legacy_db_links()
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        conn = await service.create_connection(org, _data(name="erp"))
        await execute(
            "INSERT INTO project_db_connections (project_id, db_connection_id) VALUES ($1, $2)",
            project, conn["id"],
        )
        await apply_db_scope_migration()
        # Una persona conferma il perimetro su uno schema diverso.
        await execute(
            "UPDATE project_db_scopes SET schema_name = 'vendite', scope_inferred = false "
            "WHERE project_id = $1", project,
        )
        second = await apply_db_scope_migration()
        rows = await fetch(
            "SELECT database_name, schema_name, scope_inferred FROM project_db_scopes"
        )
        return second, rows

    second, rows = run_db(scenario)
    assert second["scopes"] == 0
    # La seconda passata non aggiunge un secondo perimetro né riscrive il primo.
    assert rows == [{"database_name": "app", "schema_name": "vendite", "scope_inferred": False}]


def test_alias_collision_gets_a_suffix(pg_database):
    async def scenario():
        await make_legacy_db_links()
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        conn = await service.create_connection(org, _data(name="erp"))
        other = await service.create_connection(org, _data(name="erp-2", database_name="other"))
        # Un perimetro già presente occupa l'alias che la migrazione proporrebbe.
        await execute(
            "INSERT INTO project_db_scopes (project_id, db_connection_id, database_name, "
            "schema_name, alias) VALUES ($1, $2, 'other', 'public', 'erp')",
            project, other["id"],
        )
        for linked in (other, conn):
            await execute(
                "INSERT INTO project_db_connections (project_id, db_connection_id) VALUES ($1, $2)",
                project, linked["id"],
            )
        report = await apply_db_scope_migration()
        aliases = await fetch(
            "SELECT alias FROM project_db_scopes WHERE project_id = $1 ORDER BY alias", project
        )
        return report, aliases

    report, aliases = run_db(scenario)
    assert [a["alias"] for a in aliases] == ["erp", "erp-2"]
    assert report["renamed"] == ["erp -> erp-2"]


def test_link_to_a_deleted_project_is_skipped(pg_database):
    async def scenario():
        await make_legacy_db_links()
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        conn = await service.create_connection(org, _data(name="erp"))
        await execute(
            "INSERT INTO project_db_connections (project_id, db_connection_id) VALUES ($1, $2)",
            project, conn["id"],
        )
        await execute("DELETE FROM projects WHERE id = $1", project)
        report = await apply_db_scope_migration()
        count = await fetchval("SELECT count(*) FROM project_db_scopes")
        return report, count

    report, count = run_db(scenario)
    # La cancellazione del progetto porta via il collegamento: niente da migrare.
    assert report["scopes"] == 0 and count == 0


def test_connection_without_database_keeps_a_null_scope(pg_database):
    async def scenario():
        await make_legacy_db_links()
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        conn = await service.create_connection(org, _data(name="partial", engine="postgresql",
                                                         database_name=None))
        await execute(
            "INSERT INTO project_db_connections (project_id, db_connection_id) VALUES ($1, $2)",
            project, conn["id"],
        )
        report = await apply_db_scope_migration()
        rows = await fetch(
            "SELECT database_name, schema_name, scope_inferred FROM project_db_scopes"
        )
        return report, rows

    report, rows = run_db(scenario)
    # Il collegamento resta visibile al progetto, con un perimetro vuoto e dedotto.
    assert report["scopes"] == 1
    assert report["skipped"] == []
    assert rows == [{"database_name": None, "schema_name": None, "scope_inferred": True}]


def test_scopes_without_a_legacy_link_are_kept(pg_database):
    async def scenario():
        await make_legacy_db_links()
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        linked = await service.create_connection(org, _data(name="erp"))
        scoped_only = await service.create_connection(org, _data(name="crm", database_name="crm"))
        await execute(
            "INSERT INTO project_db_connections (project_id, db_connection_id) VALUES ($1, $2)",
            project, linked["id"],
        )
        # Il secondo perimetro non ha collegamento legacy: è nato dopo che la
        # doppia scrittura è finita, ed è il collegamento a tutti gli effetti.
        for conn in (linked, scoped_only):
            await execute(
                "INSERT INTO project_db_scopes (project_id, db_connection_id, database_name, "
                "schema_name, alias) VALUES ($1, $2, $3, 'public', $4)",
                project, conn["id"], conn["database_name"], conn["name"],
            )
        report = await apply_db_scope_migration()
        rows = await fetch("SELECT alias FROM project_db_scopes ORDER BY alias")
        return report, rows

    report, rows = run_db(scenario)
    assert "removed" not in report
    assert report["scopes"] == 0
    assert rows == [{"alias": "crm"}, {"alias": "erp"}]


def test_missing_legacy_table_is_a_no_op(pg_database):
    async def scenario():
        await execute("DROP TABLE IF EXISTS project_db_connections")
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        conn = await service.create_connection(org, _data(name="erp"))
        await execute(
            "INSERT INTO project_db_scopes (project_id, db_connection_id, database_name, "
            "schema_name, alias) VALUES ($1, $2, 'app', 'public', 'erp')",
            project, conn["id"],
        )
        report = await apply_db_scope_migration()
        return report, await fetch("SELECT alias FROM project_db_scopes")

    report, rows = run_db(scenario)
    assert report["scopes"] == 0 and report["renamed"] == [] and report["skipped"] == []
    assert rows == [{"alias": "erp"}]
