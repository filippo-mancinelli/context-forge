import time

import httpx
import jwt
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Route

from src.mcp import oauth_bridge


def _app():
    return Starlette(routes=[Route("/oauth/token", oauth_bridge.token, methods=["POST"])])


def test_authorization_code_pkce_ok(monkeypatch):
    verifier = "s3cr3t-verifier-0123456789abcdef"
    challenge = oauth_bridge._pkce_challenge(verifier)
    access = jwt.encode({"exp": int(time.time()) + 3600}, "k", algorithm="HS256")

    async def fake_consume(code):
        assert code == "bcode"
        return {"client_id": "dcr_x", "client_code_challenge": challenge,
                "kc_access_token": access, "kc_refresh_token": "ref"}

    monkeypatch.setattr(oauth_bridge, "consume_flow_by_code", fake_consume)
    resp = TestClient(_app()).post(
        "/oauth/token",
        data={"grant_type": "authorization_code", "code": "bcode",
              "code_verifier": verifier, "client_id": "dcr_x"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"] == access
    assert body["token_type"] == "Bearer"
    assert body["refresh_token"] == "ref"
    assert 3500 <= body["expires_in"] <= 3600


def test_authorization_code_pkce_ko(monkeypatch):
    challenge = oauth_bridge._pkce_challenge("the-real-verifier-value")

    async def fake_consume(code):
        return {"client_id": "dcr_x", "client_code_challenge": challenge,
                "kc_access_token": "jwt", "kc_refresh_token": "ref"}

    monkeypatch.setattr(oauth_bridge, "consume_flow_by_code", fake_consume)
    resp = TestClient(_app()).post(
        "/oauth/token",
        data={"grant_type": "authorization_code", "code": "bcode",
              "code_verifier": "WRONG-verifier", "client_id": "dcr_x"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"


def test_authorization_code_pkce_non_ascii_verifier_is_400(monkeypatch):
    """A malformed (non-ASCII) code_verifier is simply an invalid PKCE match: 400, not 500."""
    challenge = oauth_bridge._pkce_challenge("the-real-verifier-value")

    async def fake_consume(code):
        assert code == "bcode"
        return {"client_id": "dcr_x", "client_code_challenge": challenge,
                "kc_access_token": "jwt", "kc_refresh_token": "ref"}

    monkeypatch.setattr(oauth_bridge, "consume_flow_by_code", fake_consume)
    resp = TestClient(_app()).post(
        "/oauth/token",
        data={"grant_type": "authorization_code", "code": "bcode",
              "code_verifier": "verifier-\U0001F60A", "client_id": "dcr_x"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"


def test_authorization_code_missing_verifier_is_400(monkeypatch):
    """A missing code_verifier is rejected before the bridge code is ever consumed."""

    async def fake_consume(code):
        raise AssertionError("bridge code must not be consumed without a code_verifier")

    monkeypatch.setattr(oauth_bridge, "consume_flow_by_code", fake_consume)
    resp = TestClient(_app()).post(
        "/oauth/token",
        data={"grant_type": "authorization_code", "code": "bcode", "client_id": "dcr_x"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_request"


def test_authorization_code_unknown_is_400(monkeypatch):
    async def fake_consume(code):
        return None

    monkeypatch.setattr(oauth_bridge, "consume_flow_by_code", fake_consume)
    resp = TestClient(_app()).post(
        "/oauth/token",
        data={"grant_type": "authorization_code", "code": "gone", "code_verifier": "v"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"


def test_refresh_token_ok(monkeypatch):
    access = jwt.encode({"exp": int(time.time()) + 1800}, "k", algorithm="HS256")

    async def fake_refresh(refresh_token):
        assert refresh_token == "old-ref"
        return {"access_token": access, "refresh_token": "new-ref"}

    monkeypatch.setattr(oauth_bridge, "refresh_keycloak_token", fake_refresh)
    resp = TestClient(_app()).post(
        "/oauth/token", data={"grant_type": "refresh_token", "refresh_token": "old-ref"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"] == access
    assert body["refresh_token"] == "new-ref"


def test_refresh_token_rejected_by_keycloak(monkeypatch):
    async def fake_refresh(refresh_token):
        req = httpx.Request("POST", "https://kc/token")
        raise httpx.HTTPStatusError("bad", request=req, response=httpx.Response(400, request=req))

    monkeypatch.setattr(oauth_bridge, "refresh_keycloak_token", fake_refresh)
    resp = TestClient(_app()).post(
        "/oauth/token", data={"grant_type": "refresh_token", "refresh_token": "old"}
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"


def test_unsupported_grant_is_400():
    resp = TestClient(_app()).post("/oauth/token", data={"grant_type": "password"})
    assert resp.status_code == 400
    assert resp.json()["error"] == "unsupported_grant_type"
