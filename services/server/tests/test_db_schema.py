# services/server/tests/test_db_schema.py
import asyncio

from src import db
from src.db import DDL
from tests.fake_db import FakeConn, FakePool


def test_org_role_permissions_table_present():
    assert "CREATE TABLE IF NOT EXISTS org_role_permissions" in DDL
    assert "PRIMARY KEY (org_id, role, permission)" in DDL


def test_mcp_api_keys_permissions_column_and_backfill():
    assert "ALTER TABLE mcp_api_keys  ADD COLUMN IF NOT EXISTS permissions TEXT" in DDL
    # Il backfill deve toccare solo le righe non ancora migrate.
    assert "WHERE permissions IS NULL" in DDL
    # Uno scope vuoto/sconosciuto non deve concedere permessi di default (vedi I3):
    # deve allinearsi a permissions_from_scope, che ritorna frozenset() in quel caso.
    assert "WHEN scope LIKE '%read%' THEN 'context-read'" in DDL
    assert "ELSE ''" in DDL


def test_ddl_still_formats():
    # DDL viene passato a .format(dims=...): graffe non raddoppiate esplodono qui.
    DDL.format(dims=1536)


def test_role_check_constraints_present():
    assert "organization_members_role_check" in DDL
    assert "CHECK (role IN ('viewer', 'member', 'admin', 'owner'))" in DDL
    assert "project_members_role_check" in DDL
    assert "CHECK (role IN ('viewer', 'member', 'admin'))" in DDL


def test_role_check_constraints_are_idempotent():
    # Le ADD CONSTRAINT devono essere avvolte in DO/EXCEPTION per rieseguire il DDL.
    assert DDL.count("WHEN duplicate_object THEN NULL") >= 2


def test_auth_sessions_oidc_id_token_column():
    assert "ALTER TABLE auth_sessions ADD COLUMN IF NOT EXISTS oidc_id_token TEXT" in DDL


def test_org_settings_overrides_column_present():
    assert (
        "ALTER TABLE org_runtime_config ADD COLUMN IF NOT EXISTS settings_overrides"
        in DDL
    )


def test_org_settings_overrides_copy_wipe_moved_out_of_ddl():
    # La copia/wipe non convergerebbe mai su un install fresco (0 org al boot
    # della DDL): ora vive in db.apply_settings_overrides_migration().
    assert "CROSS JOIN app_runtime_config" not in DDL
    assert "UPDATE app_runtime_config SET settings_overrides" not in DDL


def _run_settings_overrides_migration(monkeypatch, fetchval_results):
    conn = FakeConn(fetchval_results=fetchval_results)

    async def fake_get_pool():
        return FakePool(conn)

    monkeypatch.setattr(db, "get_pool", fake_get_pool)
    asyncio.run(db.apply_settings_overrides_migration())
    return conn


def test_settings_overrides_migration_copies_and_wipes_when_orgs_exist(monkeypatch):
    conn = _run_settings_overrides_migration(monkeypatch, [True, True])
    assert any("INSERT INTO org_runtime_config" in q for q in conn.sql)
    assert any("UPDATE app_runtime_config SET settings_overrides" in q for q in conn.sql)


def test_settings_overrides_migration_survives_with_no_orgs(monkeypatch):
    conn = _run_settings_overrides_migration(monkeypatch, [True, False])
    assert not any("INSERT INTO org_runtime_config" in q for q in conn.sql)
    assert not any("UPDATE app_runtime_config SET settings_overrides" in q for q in conn.sql)


def test_settings_overrides_migration_second_run_is_a_no_op(monkeypatch):
    # Once the global row is already wiped, the pending-check short-circuits.
    conn = _run_settings_overrides_migration(monkeypatch, [False])
    assert not any("INSERT INTO org_runtime_config" in q for q in conn.sql)
    assert not any("UPDATE app_runtime_config SET settings_overrides" in q for q in conn.sql)


def test_vector_columns_are_untyped():
    assert "vector({dims})" not in DDL
    assert "ivfflat" not in DDL.replace("-- Bump maintenance_work_mem", "")
    # Migrazione guardata per i DB esistenti (evita rewrite a ogni bootstrap).
    assert DDL.count("atttypmod") >= 3
