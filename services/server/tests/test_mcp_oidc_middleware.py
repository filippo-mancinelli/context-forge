from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

import src.projects as projects_mod
from src import config
from src.mcp import auth as mcp_auth
from src.mcp.context import (
    get_current_org_id,
    set_current_namespace,
    set_current_org_id,
    set_current_project_id,
)
from src.mcp.permissions import get_current_permissions

PROJECT_PATH = "/mcp/acme/webshop"


async def echo(request):
    perms = get_current_permissions()
    return JSONResponse(
        {"org": get_current_org_id(), "perms": sorted(perms or []), "perms_is_none": perms is None}
    )


def _fake_project_resolver(org_id):
    async def resolver(org_slug, project_slug):
        assert (org_slug, project_slug) == ("acme", "webshop")
        return {
            "id": 5, "org_id": org_id, "name": "Webshop", "slug": "webshop",
            "memory_namespace": "acme--webshop", "org_slug": "acme",
        }
    return resolver


def _client(monkeypatch, auth_mode="enabled", org_id=1):
    # Context pulito a inizio test: ogni richiesta parte dal path di progetto,
    # che è l'unica fonte di org/namespace in questi test (niente più bare /mcp).
    set_current_org_id(None)
    set_current_project_id(None)
    set_current_namespace(None)

    settings = config.get_settings()
    monkeypatch.setattr(settings, "oidc_enabled", True, raising=False)
    monkeypatch.setattr(settings, "oidc_issuer", "https://sso.lascaux.it/realms/askmesuite", raising=False)
    monkeypatch.setattr(projects_mod, "resolve_project_by_slugs", _fake_project_resolver(org_id))

    def fake_validate(token):
        assert token.startswith("eyJ")
        return {"sub": "u1", "preferred_username": "mario.rossi",
                "tenant_id": "lascaux", "groups": ["/mcp-tools/context-read"]}

    async def fake_resolve(claims):
        # Non più invocata quando l'org arriva dal path di progetto (vedi guard
        # get_current_org_id() is None nel middleware); resta qui solo per i test
        # che vogliono verificare che NON venga chiamata.
        return 7, "ns-lascaux"

    monkeypatch.setattr(mcp_auth, "validate_oidc_token", fake_validate)
    monkeypatch.setattr(mcp_auth, "_resolve_org_from_claims", fake_resolve)
    app = Starlette(routes=[Route("/mcp", echo, methods=["POST"])])
    return TestClient(mcp_auth.add_auth_middleware(app, auth_mode=auth_mode))


def test_keycloak_jwt_authenticates(monkeypatch):
    # L'org arriva dal path di progetto (7), non dalle claims OIDC: il guard nel
    # middleware salta _resolve_org_from_claims quando il context è già popolato.
    async def fake_find_or_create(claims):
        assert claims.get("sub") == "u1"
        return 42

    async def fake_role(org_id, user_id):
        assert (org_id, user_id) == (7, 42)
        return "member"

    async def fake_resolve_perms(org_id, role):
        assert (org_id, role) == (7, "member")
        return frozenset({"context-read"})

    async def fake_project_access(org_id, user_id, project_id):
        return "member"

    monkeypatch.setattr(mcp_auth, "find_or_create_oidc_user", fake_find_or_create)
    monkeypatch.setattr(mcp_auth, "get_membership_role", fake_role)
    monkeypatch.setattr(mcp_auth, "resolve_role_permissions", fake_resolve_perms)
    monkeypatch.setattr(projects_mod, "resolve_project_access", fake_project_access)

    client = _client(monkeypatch, org_id=7)
    resp = client.post(PROJECT_PATH, headers={"Authorization": "Bearer eyJhbGciOiJSUzI1NiJ9.x.y"})
    assert resp.status_code == 200
    assert resp.json() == {"org": 7, "perms": ["context-read"], "perms_is_none": False}


def test_path_org_wins_over_claims_org(monkeypatch):
    # Il resolver di path restituisce org 5, mentre _resolve_org_from_claims
    # (se venisse invocata) restituirebbe org 7: le due fonti divergono di
    # proposito per dimostrare che vince il path e che le claims non vengono
    # nemmeno consultate quando il context è già popolato.
    calls = []

    async def fake_resolve_from_claims(claims):
        calls.append(claims)
        return 7, "ns-claims"

    async def fake_find_or_create(claims):
        return 42

    async def fake_role(org_id, user_id):
        assert (org_id, user_id) == (5, 42)
        return "member"

    async def fake_resolve_perms(org_id, role):
        assert (org_id, role) == (5, "member")
        return frozenset({"context-read"})

    async def fake_project_access(org_id, user_id, project_id):
        return "member"

    client = _client(monkeypatch, org_id=5)
    monkeypatch.setattr(mcp_auth, "_resolve_org_from_claims", fake_resolve_from_claims)
    monkeypatch.setattr(mcp_auth, "find_or_create_oidc_user", fake_find_or_create)
    monkeypatch.setattr(mcp_auth, "get_membership_role", fake_role)
    monkeypatch.setattr(mcp_auth, "resolve_role_permissions", fake_resolve_perms)
    monkeypatch.setattr(projects_mod, "resolve_project_access", fake_project_access)

    resp = client.post(PROJECT_PATH, headers={"Authorization": "Bearer eyJhbGciOiJSUzI1NiJ9.x.y"})
    assert resp.status_code == 200
    assert resp.json()["org"] == 5
    assert calls == []


def test_invalid_jwt_rejected(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(
        mcp_auth, "validate_oidc_token",
        lambda tok: (_ for _ in ()).throw(mcp_auth.OIDCError("bad")),
    )
    resp = client.post(PROJECT_PATH, headers={"Authorization": "Bearer eyJhbGciOiJSUzI1NiJ9.x.y"})
    assert resp.status_code == 401


def test_missing_auth_still_rejected(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post(PROJECT_PATH)
    assert resp.status_code == 401


def test_transition_mode_missing_auth_is_read_only(monkeypatch):
    client = _client(monkeypatch, auth_mode="transition")
    resp = client.post(PROJECT_PATH)
    assert resp.status_code == 200
    assert resp.json() == {"org": 1, "perms": ["context-read"], "perms_is_none": False}


def test_transition_mode_invalid_jwt_still_rejected(monkeypatch):
    client = _client(monkeypatch, auth_mode="transition")
    monkeypatch.setattr(
        mcp_auth, "validate_oidc_token",
        lambda tok: (_ for _ in ()).throw(mcp_auth.OIDCError("bad")),
    )
    resp = client.post(PROJECT_PATH, headers={"Authorization": "Bearer eyJhbGciOiJSUzI1NiJ9.x.y"})
    assert resp.status_code == 401


def test_missing_auth_sends_www_authenticate(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post(PROJECT_PATH)
    assert resp.status_code == 401
    assert "resource_metadata=" in resp.headers.get("WWW-Authenticate", "")


def test_opaque_bearer_now_unauthorized(monkeypatch):
    # Rimosso l'OAuth locale, un bearer non-JWT non ha più validatore: 401 con challenge.
    client = _client(monkeypatch)
    resp = client.post(PROJECT_PATH, headers={"Authorization": "Bearer cf_opaque_token"})
    assert resp.status_code == 401
    assert "resource_metadata=" in resp.headers.get("WWW-Authenticate", "")


def test_oidc_non_member_on_project_path_is_403(monkeypatch):
    # Token Keycloak valido ma utente senza membership nell'org del path:
    # sull'endpoint di progetto la richiesta viene rifiutata, non lasciata
    # passare con permessi vuoti.
    async def fake_find_or_create(claims):
        return 42

    async def fake_role(org_id, user_id):
        return None

    client = _client(monkeypatch, org_id=5)
    monkeypatch.setattr(mcp_auth, "find_or_create_oidc_user", fake_find_or_create)
    monkeypatch.setattr(mcp_auth, "get_membership_role", fake_role)
    resp = client.post(PROJECT_PATH, headers={"Authorization": "Bearer eyJhbGciOiJSUzI1NiJ9.x.y"})
    assert resp.status_code == 403


def test_oidc_member_without_project_membership_is_403(monkeypatch):
    # Membro dell'org (role member) ma senza riga project_members sul progetto
    # del path: il path diretto deve rifiutare come fa l'endpoint org-level.
    async def fake_find_or_create(claims):
        return 42

    async def fake_role(org_id, user_id):
        return "member"

    async def fake_resolve_perms(org_id, role):
        return frozenset({"context-read"})

    async def fake_project_access(org_id, user_id, project_id):
        assert (org_id, user_id, project_id) == (5, 42, 5)
        return None

    client = _client(monkeypatch, org_id=5)
    monkeypatch.setattr(mcp_auth, "find_or_create_oidc_user", fake_find_or_create)
    monkeypatch.setattr(mcp_auth, "get_membership_role", fake_role)
    monkeypatch.setattr(mcp_auth, "resolve_role_permissions", fake_resolve_perms)
    monkeypatch.setattr(projects_mod, "resolve_project_access", fake_project_access)
    resp = client.post(PROJECT_PATH, headers={"Authorization": "Bearer eyJhbGciOiJSUzI1NiJ9.x.y"})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Not a member of this project"


def test_oidc_project_member_passes_direct_path(monkeypatch):
    async def fake_find_or_create(claims):
        return 42

    async def fake_role(org_id, user_id):
        return "member"

    async def fake_resolve_perms(org_id, role):
        return frozenset({"context-read"})

    async def fake_project_access(org_id, user_id, project_id):
        return "member"

    client = _client(monkeypatch, org_id=5)
    monkeypatch.setattr(mcp_auth, "find_or_create_oidc_user", fake_find_or_create)
    monkeypatch.setattr(mcp_auth, "get_membership_role", fake_role)
    monkeypatch.setattr(mcp_auth, "resolve_role_permissions", fake_resolve_perms)
    monkeypatch.setattr(projects_mod, "resolve_project_access", fake_project_access)
    resp = client.post(PROJECT_PATH, headers={"Authorization": "Bearer eyJhbGciOiJSUzI1NiJ9.x.y"})
    assert resp.status_code == 200
