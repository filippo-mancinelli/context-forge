"""Principal in the MCP context: default, explicit set, middleware wiring."""
import asyncio

from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

import src.projects as projects_mod
from src.api import security
from src.mcp import auth as mcp_auth
from src.mcp.context import (
    Principal,
    get_current_principal,
    set_current_namespace,
    set_current_org_id,
    set_current_principal,
    set_current_project_id,
)
from tests.fake_db import FakeConn, FakePool

PROJECT_PATH = "/mcp/acme/webshop"


def test_default_principal_is_anonymous():
    set_current_principal(None)
    p = get_current_principal()
    assert (p.kind, p.id, p.label, p.rate_limit_per_minute) == ("anonymous", None, "anonymous", None)


def test_principal_round_trips():
    set_current_principal(Principal(kind="api_key", id=7, label="ci-bot", rate_limit_per_minute=30))
    try:
        p = get_current_principal()
        assert (p.kind, p.id, p.label, p.rate_limit_per_minute) == ("api_key", 7, "ci-bot", 30)
    finally:
        set_current_principal(None)


async def echo(request):
    p = get_current_principal()
    return JSONResponse(
        {"kind": p.kind, "id": p.id, "label": p.label, "limit": p.rate_limit_per_minute}
    )


def _fake_project_resolver(org_id):
    async def resolver(org_slug, project_slug):
        return {
            "id": 5, "org_id": org_id, "name": "Webshop", "slug": "webshop",
            "memory_namespace": "acme--webshop", "org_slug": "acme",
        }
    return resolver


def _client(monkeypatch, auth_mode="enabled", org_id=1):
    set_current_org_id(None)
    set_current_project_id(None)
    set_current_namespace(None)
    set_current_principal(None)
    monkeypatch.setattr(projects_mod, "resolve_project_by_slugs", _fake_project_resolver(org_id))
    app = Starlette(routes=[Route("/mcp", echo, methods=["POST"])])
    return TestClient(mcp_auth.add_auth_middleware(app, auth_mode=auth_mode))


def test_api_key_principal_carries_name_and_limit(monkeypatch):
    async def fake_validate(api_key):
        return {
            "id": 11, "name": "ci-bot", "scope": "read", "permissions": "context-read",
            "expires_at": None, "org_id": 1, "project_id": 5, "allowed_projects": [5],
            "rate_limit_per_minute": 42,
        }

    monkeypatch.setattr(mcp_auth, "validate_mcp_api_key", fake_validate)
    client = _client(monkeypatch, org_id=1)
    resp = client.post(PROJECT_PATH, headers={"X-API-Key": "forge_abc"})
    assert resp.status_code == 200
    assert resp.json() == {"kind": "api_key", "id": 11, "label": "ci-bot", "limit": 42}


def test_oidc_principal_is_the_user(monkeypatch):
    def fake_validate(token):
        return {"sub": "u1", "preferred_username": "mario.rossi", "tenant_id": "lascaux"}

    async def fake_find_or_create(claims):
        return 42

    async def fake_role(org_id, user_id):
        return "member"

    async def fake_resolve_perms(org_id, role):
        return frozenset({"context-read"})

    async def fake_project_access(org_id, user_id, project_id):
        return "member"

    from src import config
    settings = config.get_settings()
    monkeypatch.setattr(settings, "oidc_enabled", True, raising=False)
    monkeypatch.setattr(mcp_auth, "validate_oidc_token", fake_validate)
    monkeypatch.setattr(mcp_auth, "find_or_create_oidc_user", fake_find_or_create)
    monkeypatch.setattr(mcp_auth, "get_membership_role", fake_role)
    monkeypatch.setattr(mcp_auth, "resolve_role_permissions", fake_resolve_perms)
    monkeypatch.setattr(projects_mod, "resolve_project_access", fake_project_access)

    client = _client(monkeypatch, org_id=7)
    resp = client.post(PROJECT_PATH, headers={"Authorization": "Bearer eyJhbGciOiJSUzI1NiJ9.x.y"})
    assert resp.status_code == 200
    assert resp.json() == {"kind": "user", "id": 42, "label": "mario.rossi", "limit": None}


def test_transition_mode_without_credentials_is_anonymous(monkeypatch):
    client = _client(monkeypatch, auth_mode="transition", org_id=1)
    resp = client.post(PROJECT_PATH)
    assert resp.status_code == 200
    assert resp.json() == {"kind": "anonymous", "id": None, "label": "anonymous", "limit": None}


def test_validate_mcp_api_key_select_includes_rate_limit(monkeypatch):
    """The rate limit must be read back by the SELECT, not only created by 0003."""
    row = {
        "id": 11, "name": "ci-bot", "scope": "read", "permissions": "context-read",
        "expires_at": None, "org_id": 1, "project_id": 5, "rate_limit_per_minute": 42,
    }
    conn = FakeConn(fetchrow_results=[row])

    async def fake_pool():
        return FakePool(conn)

    monkeypatch.setattr(security, "get_pool", fake_pool)
    info = asyncio.run(security.validate_mcp_api_key("forge_abc"))
    assert info["rate_limit_per_minute"] == 42
    assert "rate_limit_per_minute" in "\n".join(conn.sql)
