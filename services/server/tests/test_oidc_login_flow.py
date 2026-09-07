from fastapi.testclient import TestClient

from src import config
from src.api.app import api
from src.api.routes import auth as auth_route

ISSUER = "https://sso.lascaux.it/realms/askmesuite"


def _enable(monkeypatch):
    settings = config.get_settings()
    monkeypatch.setattr(settings, "oidc_enabled", True, raising=False)
    monkeypatch.setattr(settings, "oidc_issuer", ISSUER, raising=False)
    monkeypatch.setattr(settings, "oidc_client_id", "context-forge", raising=False)
    monkeypatch.setattr(settings, "oidc_client_secret", "s3cret", raising=False)
    monkeypatch.setattr(settings, "public_base_url", "https://forge.lascaux.it", raising=False)


def test_config_reports_enabled(monkeypatch):
    _enable(monkeypatch)
    client = TestClient(api)
    assert client.get("/api/auth/oidc/config").json() == {"enabled": True}


def test_login_redirects_to_keycloak(monkeypatch):
    _enable(monkeypatch)
    client = TestClient(api)
    resp = client.get("/api/auth/oidc/login", follow_redirects=False)
    assert resp.status_code in (302, 307)
    loc = resp.headers["location"]
    assert loc.startswith(f"{ISSUER}/protocol/openid-connect/auth?")
    assert "client_id=context-forge" in loc
    assert "state=" in loc


def test_callback_exchanges_code_and_issues_session(monkeypatch):
    _enable(monkeypatch)
    client = TestClient(api)
    state = auth_route._issue_state()

    class FakeResp:
        status_code = 200

        def json(self):
            return {"access_token": "eyJx.y.z"}

    class FakeClient:
        def __init__(self, *a, **kw): ...
        async def __aenter__(self):
            return self
        async def __aexit__(self, *exc):
            return False
        async def post(self, url, data=None):
            assert url == f"{ISSUER}/protocol/openid-connect/token"
            assert data["grant_type"] == "authorization_code"
            return FakeResp()

    monkeypatch.setattr(auth_route.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(auth_route, "validate_oidc_token",
                        lambda tok: {"sub": "kc-1", "preferred_username": "mario.rossi"})

    async def fake_provision(claims):
        return 42

    async def fake_session(user_id, id_token=None):
        assert user_id == 42
        return "native-session-token"

    monkeypatch.setattr(auth_route, "provision_oidc_user", fake_provision)
    monkeypatch.setattr(auth_route, "create_session", fake_session)

    resp = client.get(f"/api/auth/oidc/callback?code=abc&state={state}", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "/?oidc_token=native-session-token"


def test_callback_rejects_bad_state(monkeypatch):
    _enable(monkeypatch)
    client = TestClient(api)
    resp = client.get("/api/auth/oidc/callback?code=abc&state=forged", follow_redirects=False)
    assert resp.status_code == 400
