import httpx
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Route

from src.mcp import oauth_bridge


def _app():
    return Starlette(routes=[Route("/oauth/callback", oauth_bridge.callback, methods=["GET"])])


def test_callback_emits_bridge_code(monkeypatch):
    captured = {}

    async def fake_load_flow(state):
        assert state == "bridgestate"
        return {"bridge_state": "bridgestate", "kc_pkce_verifier": "ver",
                "client_redirect_uri": "http://127.0.0.1:5000/cb", "client_state": "cli"}

    async def fake_exchange(code, redirect_uri, code_verifier):
        assert code == "kc-code" and code_verifier == "ver"
        return {"access_token": "jwt-abc", "refresh_token": "ref-abc"}

    async def fake_attach(bridge_state, bridge_code, kc_access_token, kc_refresh_token):
        captured.update(bridge_code=bridge_code, at=kc_access_token, rt=kc_refresh_token)

    monkeypatch.setattr(oauth_bridge, "load_flow_by_state", fake_load_flow)
    monkeypatch.setattr(oauth_bridge, "exchange_keycloak_code", fake_exchange)
    monkeypatch.setattr(oauth_bridge, "attach_bridge_code", fake_attach)

    resp = TestClient(_app()).get(
        "/oauth/callback", params={"code": "kc-code", "state": "bridgestate"}, follow_redirects=False
    )
    assert resp.status_code == 302
    loc = resp.headers["location"]
    assert loc.startswith("http://127.0.0.1:5000/cb?")
    assert f"code={captured['bridge_code']}" in loc
    assert "state=cli" in loc
    assert captured["at"] == "jwt-abc"
    assert captured["rt"] == "ref-abc"


def test_callback_unknown_state_is_400(monkeypatch):
    async def fake_load_flow(state):
        return None

    monkeypatch.setattr(oauth_bridge, "load_flow_by_state", fake_load_flow)
    resp = TestClient(_app()).get(
        "/oauth/callback", params={"code": "x", "state": "y"}, follow_redirects=False
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"


def test_callback_keycloak_unreachable_is_503(monkeypatch):
    async def fake_load_flow(state):
        return {"bridge_state": "s", "kc_pkce_verifier": "v",
                "client_redirect_uri": "http://127.0.0.1:5000/cb", "client_state": ""}

    async def fake_exchange(code, redirect_uri, code_verifier):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(oauth_bridge, "load_flow_by_state", fake_load_flow)
    monkeypatch.setattr(oauth_bridge, "exchange_keycloak_code", fake_exchange)
    resp = TestClient(_app()).get(
        "/oauth/callback", params={"code": "x", "state": "s"}, follow_redirects=False
    )
    assert resp.status_code == 503


def test_callback_provider_error_is_400(monkeypatch):
    resp = TestClient(_app()).get(
        "/oauth/callback", params={"error": "access_denied", "state": "s"}, follow_redirects=False
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "access_denied"


def test_callback_keycloak_rejected_grant_is_400(monkeypatch):
    async def fake_load_flow(state):
        return {"bridge_state": "s", "kc_pkce_verifier": "v",
                "client_redirect_uri": "http://127.0.0.1:5000/cb", "client_state": ""}

    async def fake_exchange(code, redirect_uri, code_verifier):
        request = httpx.Request("POST", "http://kc")
        response = httpx.Response(400, request=request)
        raise httpx.HTTPStatusError("Bad Request", request=request, response=response)

    monkeypatch.setattr(oauth_bridge, "load_flow_by_state", fake_load_flow)
    monkeypatch.setattr(oauth_bridge, "exchange_keycloak_code", fake_exchange)
    resp = TestClient(_app()).get(
        "/oauth/callback", params={"code": "x", "state": "s"}, follow_redirects=False
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"


def test_callback_keycloak_timeout_is_503(monkeypatch):
    async def fake_load_flow(state):
        return {"bridge_state": "s", "kc_pkce_verifier": "v",
                "client_redirect_uri": "http://127.0.0.1:5000/cb", "client_state": ""}

    async def fake_exchange(code, redirect_uri, code_verifier):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(oauth_bridge, "load_flow_by_state", fake_load_flow)
    monkeypatch.setattr(oauth_bridge, "exchange_keycloak_code", fake_exchange)
    resp = TestClient(_app()).get(
        "/oauth/callback", params={"code": "x", "state": "s"}, follow_redirects=False
    )
    assert resp.status_code == 503
    assert resp.json()["error"] == "temporarily_unavailable"


def test_callback_non_dict_token_response_is_400(monkeypatch):
    async def fake_load_flow(state):
        return {"bridge_state": "s", "kc_pkce_verifier": "v",
                "client_redirect_uri": "http://127.0.0.1:5000/cb", "client_state": ""}

    async def fake_exchange(code, redirect_uri, code_verifier):
        return None

    monkeypatch.setattr(oauth_bridge, "load_flow_by_state", fake_load_flow)
    monkeypatch.setattr(oauth_bridge, "exchange_keycloak_code", fake_exchange)
    resp = TestClient(_app()).get(
        "/oauth/callback", params={"code": "x", "state": "s"}, follow_redirects=False
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"


def test_callback_missing_access_token_is_400(monkeypatch):
    async def fake_load_flow(state):
        return {"bridge_state": "s", "kc_pkce_verifier": "v",
                "client_redirect_uri": "http://127.0.0.1:5000/cb", "client_state": ""}

    async def fake_exchange(code, redirect_uri, code_verifier):
        return {"token_type": "Bearer"}

    monkeypatch.setattr(oauth_bridge, "load_flow_by_state", fake_load_flow)
    monkeypatch.setattr(oauth_bridge, "exchange_keycloak_code", fake_exchange)
    resp = TestClient(_app()).get(
        "/oauth/callback", params={"code": "x", "state": "s"}, follow_redirects=False
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"
