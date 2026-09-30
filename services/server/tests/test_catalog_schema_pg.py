from tests.pgutil import fetch, requires_pg, run_db


@requires_pg
def test_fresh_schema_has_the_catalog_shape(pg_database):
    async def scenario():
        rows = await fetch(
            "SELECT table_name, column_name, is_nullable FROM information_schema.columns "
            "WHERE table_name IN ('machines', 'ssh_sources', 'db_connections', "
            "'project_ssh_sources', 'project_db_connections')"
        )
        return {f"{r['table_name']}.{r['column_name']}": r["is_nullable"] for r in rows}

    columns = run_db(scenario)
    assert columns["ssh_sources.machine_id"] == "NO"
    assert columns["db_connections.ssh_machine_id"] == "YES"
    for legacy in ("ssh_sources.project_id", "ssh_sources.host", "ssh_sources.password_enc",
                   "db_connections.project_id", "db_connections.ssh_host", "db_connections.ssh_enabled"):
        assert legacy not in columns, legacy
    assert "ssh_sources.restricted" in columns
    assert "db_connections.restricted" in columns
    assert "machines.private_key_enc" in columns
    assert "project_ssh_sources.added_by" in columns
    assert "project_db_connections.db_connection_id" in columns
