from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Route

from src import config
from src.mcp import oauth_bridge


def _client(monkeypatch, public_url="http://localhost:4000"):
    monkeypatch.setattr(config.get_settings(), "public_mcp_url", public_url, raising=False)
    app = Starlette(routes=[
        Route("/.well-known/oauth-protected-resource",
              oauth_bridge.protected_resource_metadata, methods=["GET"]),
        Route("/.well-known/oauth-authorization-server",
              oauth_bridge.authorization_server_metadata, methods=["GET"]),
    ])
    return TestClient(app)


def test_protected_resource_shape(monkeypatch):
    resp = _client(monkeypatch).get("/.well-known/oauth-protected-resource")
    assert resp.status_code == 200
    assert resp.json() == {
        "resource": "http://localhost:4000",
        "authorization_servers": ["http://localhost:4000"],
    }


def test_authorization_server_shape(monkeypatch):
    body = _client(monkeypatch).get("/.well-known/oauth-authorization-server").json()
    assert body["issuer"] == "http://localhost:4000"
    assert body["authorization_endpoint"] == "http://localhost:4000/oauth/authorize"
    assert body["token_endpoint"] == "http://localhost:4000/oauth/token"
    assert body["registration_endpoint"] == "http://localhost:4000/oauth/register"
    assert body["response_types_supported"] == ["code"]
    assert body["grant_types_supported"] == ["authorization_code", "refresh_token"]
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert body["token_endpoint_auth_methods_supported"] == ["none"]
