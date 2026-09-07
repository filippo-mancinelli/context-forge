# services/server/tests/test_db_schema.py
from src.db import DDL


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


def test_org_settings_overrides_column_and_migration():
    assert (
        "ALTER TABLE org_runtime_config ADD COLUMN IF NOT EXISTS settings_overrides"
        in DDL
    )
    # La copia one-shot dal globale gira solo finché il globale non è stato svuotato.
    assert "CROSS JOIN app_runtime_config" in DDL
    assert "UPDATE app_runtime_config SET settings_overrides" in DDL
    assert "AND EXISTS (SELECT 1 FROM organizations)" in DDL


def test_vector_columns_are_untyped():
    assert "vector({dims})" not in DDL
    assert "ivfflat" not in DDL.replace("-- Bump maintenance_work_mem", "")
    # Migrazione guardata per i DB esistenti (evita rewrite a ogni bootstrap).
    assert DDL.count("atttypmod") >= 3
