from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Route

from src.mcp import oauth_bridge


def _client(monkeypatch, captured):
    async def fake_save(client_id, name, redirect_uris):
        captured["client_id"] = client_id
        captured["name"] = name
        captured["redirect_uris"] = redirect_uris

    monkeypatch.setattr(oauth_bridge, "save_dynamic_client", fake_save)
    app = Starlette(routes=[Route("/oauth/register", oauth_bridge.register_client, methods=["POST"])])
    return TestClient(app)


def test_register_loopback_accepted(monkeypatch):
    captured = {}
    resp = _client(monkeypatch, captured).post(
        "/oauth/register",
        json={"redirect_uris": ["http://127.0.0.1:33418/callback"], "client_name": "Claude Code"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["client_id"].startswith("dcr_")
    assert body["token_endpoint_auth_method"] == "none"
    assert body["redirect_uris"] == ["http://127.0.0.1:33418/callback"]
    assert captured["redirect_uris"] == ["http://127.0.0.1:33418/callback"]


def test_register_localhost_accepted(monkeypatch):
    captured = {}
    resp = _client(monkeypatch, captured).post(
        "/oauth/register", json={"redirect_uris": ["http://localhost:8976/cb"]}
    )
    assert resp.status_code == 201


def test_register_non_loopback_rejected(monkeypatch):
    captured = {}
    resp = _client(monkeypatch, captured).post(
        "/oauth/register", json={"redirect_uris": ["https://evil.example/callback"]}
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_redirect_uri"
    assert captured == {}


def test_register_missing_redirects_rejected(monkeypatch):
    captured = {}
    resp = _client(monkeypatch, captured).post("/oauth/register", json={})
    assert resp.status_code == 400
    assert captured == {}


def test_register_non_dict_body_rejected(monkeypatch):
    captured = {}
    resp = _client(monkeypatch, captured).post("/oauth/register", json=[])
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_client_metadata"
    assert captured == {}


def test_register_subdomain_loopback_bypass_rejected(monkeypatch):
    captured = {}
    resp = _client(monkeypatch, captured).post(
        "/oauth/register", json={"redirect_uris": ["http://localhost.evil.com/cb"]}
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_redirect_uri"
    assert captured == {}


def test_register_ip_suffix_loopback_bypass_rejected(monkeypatch):
    captured = {}
    resp = _client(monkeypatch, captured).post(
        "/oauth/register", json={"redirect_uris": ["http://127.0.0.1.evil.com/cb"]}
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_redirect_uri"
    assert captured == {}


def test_register_https_localhost_rejected(monkeypatch):
    captured = {}
    resp = _client(monkeypatch, captured).post(
        "/oauth/register", json={"redirect_uris": ["https://localhost/cb"]}
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_redirect_uri"
    assert captured == {}


def test_register_userinfo_loopback_bypass_rejected(monkeypatch):
    captured = {}
    resp = _client(monkeypatch, captured).post(
        "/oauth/register", json={"redirect_uris": ["http://localhost@evil.com/cb"]}
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_redirect_uri"
    assert captured == {}
