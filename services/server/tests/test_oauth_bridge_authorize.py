from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Route

from src import config
from src.mcp import oauth_bridge

ISSUER = "https://identity-collaudo.lascaux.it/realms/askmesuite"


def _client(monkeypatch, saved):
    async def fake_load_client(client_id):
        if client_id == "dcr_ok":
            return {"client_id": "dcr_ok", "redirect_uris": ["http://127.0.0.1:5000/cb"]}
        return None

    async def fake_create_flow(bridge_state, client_id, client_redirect_uri,
                               client_state, client_code_challenge, kc_pkce_verifier):
        saved.update(
            bridge_state=bridge_state, client_id=client_id,
            client_redirect_uri=client_redirect_uri, client_state=client_state,
            client_code_challenge=client_code_challenge, kc_pkce_verifier=kc_pkce_verifier,
        )

    monkeypatch.setattr(oauth_bridge, "load_client", fake_load_client)
    monkeypatch.setattr(oauth_bridge, "create_flow", fake_create_flow)
    s = config.get_settings()
    monkeypatch.setattr(s, "oidc_issuer", ISSUER, raising=False)
    monkeypatch.setattr(s, "oidc_client_id", "context-forge", raising=False)
    monkeypatch.setattr(s, "public_mcp_url", "http://localhost:4000", raising=False)
    app = Starlette(routes=[Route("/oauth/authorize", oauth_bridge.authorize, methods=["GET"])])
    return TestClient(app)


def test_authorize_redirects_to_keycloak(monkeypatch):
    saved = {}
    resp = _client(monkeypatch, saved).get(
        "/oauth/authorize",
        params={"client_id": "dcr_ok", "redirect_uri": "http://127.0.0.1:5000/cb",
                "code_challenge": "client-challenge", "code_challenge_method": "S256",
                "state": "cli-state", "response_type": "code"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    loc = resp.headers["location"]
    assert loc.startswith(ISSUER + "/protocol/openid-connect/auth?")
    assert "client_id=context-forge" in loc
    assert "code_challenge_method=S256" in loc
    assert f"state={saved['bridge_state']}" in loc
    # Il code_challenge del client è salvato tal quale; verso Keycloak viaggia
    # il challenge derivato dal verifier PKCE proprio del bridge.
    assert saved["client_code_challenge"] == "client-challenge"
    assert saved["client_redirect_uri"] == "http://127.0.0.1:5000/cb"
    assert oauth_bridge._pkce_challenge(saved["kc_pkce_verifier"]) not in ("", None)


def test_authorize_unknown_client_is_401(monkeypatch):
    resp = _client(monkeypatch, {}).get(
        "/oauth/authorize",
        params={"client_id": "dcr_nope", "redirect_uri": "http://127.0.0.1:5000/cb",
                "code_challenge": "c", "code_challenge_method": "S256", "response_type": "code"},
        follow_redirects=False,
    )
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_client"


def test_authorize_bad_redirect_is_400(monkeypatch):
    resp = _client(monkeypatch, {}).get(
        "/oauth/authorize",
        params={"client_id": "dcr_ok", "redirect_uri": "http://127.0.0.1:9999/other",
                "code_challenge": "c", "code_challenge_method": "S256", "response_type": "code"},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_authorize_missing_pkce_redirects_error(monkeypatch):
    resp = _client(monkeypatch, {}).get(
        "/oauth/authorize",
        params={"client_id": "dcr_ok", "redirect_uri": "http://127.0.0.1:5000/cb",
                "state": "cli", "response_type": "code"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    loc = resp.headers["location"]
    assert loc.startswith("http://127.0.0.1:5000/cb?")
    assert "error=invalid_request" in loc
