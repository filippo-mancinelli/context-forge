import inspect

from src import db
from src.migrations.runner import discover_versions

# The inline DDL is frozen: the catalog schema lives in migration 0005.
ORG_CATALOG = next(m for m in discover_versions() if m.NAME == "org_catalog")
DDL = ORG_CATALOG.SQL
# Database links carry a scope since migration 0007.
DB_SCOPES = next(m for m in discover_versions() if m.NAME == "db_scopes")


def test_org_catalog_migration_is_version_5_and_transactional():
    assert ORG_CATALOG.VERSION == 5
    assert ORG_CATALOG.TRANSACTIONAL is True


def test_machines_table_is_created_before_the_tables_that_reference_it():
    machines = DDL.index("CREATE TABLE IF NOT EXISTS machines")
    assert machines < DDL.index("ALTER TABLE db_connections")
    assert machines < DDL.index("ALTER TABLE ssh_sources")


def test_selection_tables_are_created():
    assert "CREATE TABLE IF NOT EXISTS project_ssh_sources" in DDL
    assert "CREATE TABLE IF NOT EXISTS project_db_scopes" in DB_SCOPES.SQL
    # Database links live only in scopes: 0007 never creates the legacy table.
    assert "project_db_connections" not in DB_SCOPES.SQL


def test_ddl_does_not_recreate_project_scoped_columns():
    assert "ALTER TABLE db_connections ADD COLUMN IF NOT EXISTS project_id" not in DDL
    assert "ADD COLUMN IF NOT EXISTS ssh_host" not in DDL
    assert "ssh_sources_project_idx" not in DDL


def test_project_migration_leaves_database_connections_alone():
    source = inspect.getsource(db.apply_project_migration)
    assert '"db_connections"' not in source
    assert "db_connections_project_name_key" not in source
