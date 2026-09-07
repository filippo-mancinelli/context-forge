"""La migrazione progetti deve backfillare tutte le tabelle contenuto."""
import inspect

from src import db
from tests.test_projects_schema import CONTENT_TABLES


def test_apply_project_migration_exists():
    assert hasattr(db, "apply_project_migration")


def test_migration_backfills_every_content_table():
    src = inspect.getsource(db.apply_project_migration)
    for table in CONTENT_TABLES:
        assert table in src, f"apply_project_migration non tocca {table}"
    # Il progetto default eredita il namespace dell'organizzazione.
    assert "memory_namespace" in src
    assert "SET NOT NULL" in src


def test_ensure_tenant_storage_runs_project_migration():
    from src import tenancy
    src = inspect.getsource(tenancy.ensure_tenant_storage)
    assert "apply_project_migration" in src


def test_mcp_api_keys_project_id_stays_nullable():
    # mcp_api_keys.project_id IS NULL è intenzionale: significa "key valida
    # su tutti i progetti dell'org" (vedi create_key(all_projects=True) e
    # mcp/auth.py: allowed_set is None => org-wide). La migrazione non deve
    # backfillare né forzare NOT NULL su questa colonna, altrimenti le key
    # org-wide esistenti verrebbero riscritte a ogni boot, o la creazione di
    # nuove key org-wide fallirebbe con NotNullViolationError.
    src = inspect.getsource(db.apply_project_migration)
    content_tables_src = src.split("pool = await get_pool()")[0]
    assert "mcp_api_keys" not in content_tables_src, (
        "mcp_api_keys non deve stare in content_tables: il backfill e lo "
        "SET NOT NULL clobbererebbero le key org-wide (project_id NULL)"
    )
