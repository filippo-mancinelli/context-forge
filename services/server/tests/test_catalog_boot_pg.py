"""The catalog conversion at boot, starting from the frozen baseline shape.

A fresh context-forge database is created by the baseline migration, so SSH
folders and databases start project-owned; ensure_tenant_storage() must bring
them to the catalog shape, with or without an organization.
"""
from src import tenancy
from src.datasources.secrets import encrypt_secret
from tests import pgutil
from tests.pgutil import execute, fetch, fetchval, run_db, seed_org, seed_project

pytestmark = pgutil.requires_pg


async def _shape() -> dict:
    rows = await fetch(
        "SELECT table_name, column_name, is_nullable FROM information_schema.columns "
        "WHERE table_name IN ('ssh_sources', 'db_connections')"
    )
    constraints = await fetch(
        "SELECT conname FROM pg_constraint WHERE conrelid IN "
        "('ssh_sources'::regclass, 'db_connections'::regclass)"
    )
    indexes = await fetch(
        "SELECT indexname FROM pg_indexes WHERE tablename IN ('ssh_sources', 'db_connections')"
    )
    return {
        "columns": {f"{r['table_name']}.{r['column_name']}": r["is_nullable"] for r in rows},
        "constraints": {r["conname"] for r in constraints},
        "indexes": {r["indexname"] for r in indexes},
        "legacy_links": await fetchval("SELECT to_regclass('project_db_connections') IS NOT NULL"),
    }


def _assert_catalog_shape(shape: dict) -> None:
    columns = shape["columns"]
    assert columns["ssh_sources.machine_id"] == "NO"
    assert columns["db_connections.ssh_machine_id"] == "YES"
    for legacy in ("ssh_sources.project_id", "ssh_sources.host", "ssh_sources.password_enc",
                   "db_connections.project_id", "db_connections.ssh_host",
                   "db_connections.ssh_enabled"):
        assert legacy not in columns, legacy
    for legacy in ("ssh_sources_project_id_name_key", "db_connections_org_id_name_key",
                   "db_connections_project_name_key"):
        assert legacy not in shape["constraints"], legacy
    assert "ssh_sources_project_idx" not in shape["indexes"]
    assert {"ssh_sources_org_lower_name_idx", "db_connections_org_lower_name_idx"} <= shape["indexes"]
    # Migration 0005 creates the legacy links; the scope conversion drops them.
    assert shape["legacy_links"] is False


def test_baseline_starts_from_the_project_owned_shape(baseline_database):
    shape = run_db(_shape)
    assert shape["columns"]["ssh_sources.host"] == "NO"
    assert shape["columns"]["ssh_sources.machine_id"] == "YES"
    assert "db_connections.ssh_host" in shape["columns"]
    assert "db_connections_org_id_name_key" in shape["constraints"]


def test_boot_before_setup_reaches_the_catalog_shape(baseline_database):
    async def scenario():
        first = await tenancy.ensure_tenant_storage()
        second = await tenancy.ensure_tenant_storage()
        return first, second, await _shape()

    first, second, shape = run_db(scenario)
    assert first is None and second is None
    _assert_catalog_shape(shape)


def test_boot_with_an_org_converts_legacy_rows(baseline_database):
    async def scenario():
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        # An install upgraded before this change carries the swapped constraint.
        await execute("ALTER TABLE db_connections DROP CONSTRAINT db_connections_org_id_name_key")
        await execute(
            "ALTER TABLE db_connections ADD CONSTRAINT db_connections_project_name_key "
            "UNIQUE (project_id, name)"
        )
        source = await fetchval(
            "INSERT INTO ssh_sources (org_id, project_id, name, host, username, auth_method, "
            "private_key_enc, root_path) VALUES ($1, $2, 'logs', '10.0.0.6', 'ops', 'key', $3, '/srv') "
            "RETURNING id",
            org, project, encrypt_secret("K1"),
        )
        database = await fetchval(
            "INSERT INTO db_connections (org_id, project_id, name, engine, ssh_enabled, ssh_host, "
            "ssh_username, ssh_auth_method, ssh_private_key_enc) "
            "VALUES ($1, $2, 'erp', 'mysql', true, '10.0.0.6', 'ops', 'key', $3) RETURNING id",
            org, project, encrypt_secret("K1"),
        )
        returned = await tenancy.ensure_tenant_storage()
        again = await tenancy.ensure_tenant_storage()
        return {
            "org": org, "project": project, "source": source, "database": database,
            "returned": returned, "again": again, "shape": await _shape(),
            "machines": await fetch("SELECT id, name FROM machines WHERE org_id = $1", org),
            "tunnel": await fetchval("SELECT ssh_machine_id FROM db_connections WHERE id = $1", database),
            "folder_machine": await fetchval("SELECT machine_id FROM ssh_sources WHERE id = $1", source),
            "folders": await fetch("SELECT project_id, ssh_source_id FROM project_ssh_sources"),
            "databases": await fetch(
                "SELECT project_id, db_connection_id, alias, scope_inferred FROM project_db_scopes"
            ),
        }

    out = run_db(scenario)
    assert out["returned"] == out["org"] and out["again"] == out["org"]
    _assert_catalog_shape(out["shape"])
    assert [m["name"] for m in out["machines"]] == ["ops@10.0.0.6"]
    assert out["tunnel"] == out["folder_machine"] == out["machines"][0]["id"]
    assert out["folders"] == [{"project_id": out["project"], "ssh_source_id": out["source"]}]
    assert out["databases"] == [{"project_id": out["project"], "db_connection_id": out["database"],
                                 "alias": "erp", "scope_inferred": True}]
