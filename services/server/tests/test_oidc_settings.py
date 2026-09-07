from src.config import Settings


def test_oidc_settings_defaults():
    s = Settings(_env_file=None)
    assert s.oidc_enabled is False
    assert s.oidc_issuer == ""
    assert s.oidc_client_id == "context-forge"
    assert s.oidc_client_secret == ""
    assert s.oidc_audience == ""
    assert s.oidc_jwks_url == ""
    assert s.oidc_group_prefix == "/mcp-tools/"
    assert s.oidc_org_claim == "tenant_id"
    assert s.public_base_url == ""
