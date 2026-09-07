"""Il DDL deve sostituire le tabelle OAuth locali con lo storage del bridge."""
from src.db import DDL


def test_bridge_flows_table_created():
    assert "CREATE TABLE IF NOT EXISTS oauth_bridge_flows" in DDL
    for col in (
        "bridge_state", "client_id", "client_redirect_uri", "client_state",
        "client_code_challenge", "kc_pkce_verifier", "bridge_code",
        "kc_access_token", "kc_refresh_token", "expires_at",
    ):
        assert col in DDL, f"manca colonna {col} su oauth_bridge_flows"


def test_oauth_clients_has_dynamic_column():
    assert "ALTER TABLE oauth_clients ADD COLUMN IF NOT EXISTS dynamic BOOLEAN" in DDL


def test_legacy_oauth_tables_dropped():
    assert "DROP TABLE IF EXISTS oauth_authorization_codes" in DDL
    assert "DROP TABLE IF EXISTS oauth_tokens" in DDL
    assert "CREATE TABLE IF NOT EXISTS oauth_authorization_codes" not in DDL
    assert "CREATE TABLE IF NOT EXISTS oauth_tokens" not in DDL


def test_public_mcp_url_setting_exists():
    from src.config import Settings

    assert hasattr(Settings(), "public_mcp_url")
