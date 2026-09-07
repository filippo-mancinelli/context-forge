# services/server/tests/test_mcp_auth_permissions.py
import asyncio
from types import SimpleNamespace

from src.mcp import auth as mcp_auth
from src.mcp.permissions import get_current_permissions, set_current_permissions


class FakeURL:
    # Path fuori dal namespace /mcp: questi test esercitano solo la logica di
    # risoluzione permessi del middleware (rami OIDC/OAuth/API key) a valle del
    # routing per progetto, quindi il path non deve intercettare né la forma
    # progetto né il gate su /mcp* incompleto.
    path = "/tools"


class FakeRequest:
    method = "POST"
    url = FakeURL()

    def __init__(self, headers):
        self.headers = headers
        self.state = SimpleNamespace()
        self.client = SimpleNamespace(host="test")


def _dispatch(monkeypatch, headers, auth_mode="enabled", seed_permissions=None):
    """Esegue il middleware e cattura i permessi visti dall'app a valle."""
    set_current_permissions(seed_permissions)
    captured = {}

    async def call_next(request):
        captured["perms"] = get_current_permissions()
        return "ok"

    middleware = mcp_auth.MCPAuthMiddleware(lambda scope: None, auth_mode=auth_mode)
    asyncio.run(middleware.dispatch(FakeRequest(headers), call_next))
    return captured.get("perms")


def _patch_common(monkeypatch):
    monkeypatch.setattr(
        mcp_auth,
        "get_settings",
        lambda: SimpleNamespace(
            oidc_enabled=True, oidc_group_prefix="/mcp-tools/", oidc_org_claim="org"
        ),
    )

    async def fake_resolve_from_claims(claims):
        return 1, "ns"

    monkeypatch.setattr(mcp_auth, "_resolve_org_from_claims", fake_resolve_from_claims)


def test_oidc_token_gets_db_role_permissions(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(mcp_auth, "looks_like_jwt", lambda t: True)
    monkeypatch.setattr(mcp_auth, "validate_oidc_token", lambda t: {"sub": "kc-1"})

    async def fake_find_or_create(claims):
        return 42

    async def fake_role(org_id, user_id):
        assert (org_id, user_id) == (1, 42)
        return "member"

    async def fake_resolve(org_id, role):
        assert (org_id, role) == (1, "member")
        return frozenset({"context-read", "db-query"})

    monkeypatch.setattr(mcp_auth, "find_or_create_oidc_user", fake_find_or_create)
    monkeypatch.setattr(mcp_auth, "get_membership_role", fake_role)
    monkeypatch.setattr(mcp_auth, "resolve_role_permissions", fake_resolve)

    perms = _dispatch(monkeypatch, {"Authorization": "Bearer x.y.z"})
    assert perms == frozenset({"context-read", "db-query"})


def test_oidc_user_without_membership_gets_no_permissions(monkeypatch):
    """A valid OIDC token for a user with no org membership must resolve to frozenset(),
    and the request must still reach the app (not be rejected outright) — see C1."""
    _patch_common(monkeypatch)
    monkeypatch.setattr(mcp_auth, "looks_like_jwt", lambda t: True)
    monkeypatch.setattr(mcp_auth, "validate_oidc_token", lambda t: {"sub": "kc-stranger"})

    async def fake_find_or_create(claims):
        return 99

    async def fake_role(org_id, user_id):
        assert (org_id, user_id) == (1, 99)
        return None

    monkeypatch.setattr(mcp_auth, "find_or_create_oidc_user", fake_find_or_create)
    monkeypatch.setattr(mcp_auth, "get_membership_role", fake_role)
    # resolve_role_permissions is NOT monkeypatched: the real function runs and must
    # return frozenset() for a None role without ever touching the DB (short-circuits).

    perms = _dispatch(monkeypatch, {"Authorization": "Bearer x.y.z"})
    assert perms == frozenset()


def test_api_key_uses_permissions_column(monkeypatch):
    _patch_common(monkeypatch)

    async def fake_validate(key):
        return {"id": 1, "name": "k", "scope": "admin", "permissions": "db-query", "org_id": 1}

    async def fake_resolve_org(org_id, user_id=None):
        return 1, "ns"

    monkeypatch.setattr(mcp_auth, "validate_mcp_api_key", fake_validate)
    monkeypatch.setattr(mcp_auth, "_resolve_org", fake_resolve_org)

    perms = _dispatch(monkeypatch, {"X-API-Key": "forge_abc"})
    assert perms == frozenset({"db-query"})


def test_legacy_api_key_falls_back_to_scope(monkeypatch):
    _patch_common(monkeypatch)

    async def fake_validate(key):
        return {"id": 1, "name": "k", "scope": "read,write", "permissions": None, "org_id": 1}

    async def fake_resolve_org(org_id, user_id=None):
        return 1, "ns"

    monkeypatch.setattr(mcp_auth, "validate_mcp_api_key", fake_validate)
    monkeypatch.setattr(mcp_auth, "_resolve_org", fake_resolve_org)

    perms = _dispatch(monkeypatch, {"X-API-Key": "forge_abc"})
    assert perms == frozenset({"context-read", "context-write"})


def test_transition_mode_unauthenticated_is_read_only(monkeypatch):
    _patch_common(monkeypatch)
    # Unauthenticated transition-mode requests get read-only permissions, not allow-all.
    # Seed a sentinel so the assertion actually proves the middleware called
    # set_current_permissions, rather than observing the ContextVar default.
    perms = _dispatch(
        monkeypatch, {}, auth_mode="transition",
        seed_permissions=frozenset({"sentinel"}),
    )
    assert perms == frozenset({"context-read"})


def test_non_jwt_bearer_is_treated_as_api_key(monkeypatch):
    """Clients that can only send Authorization: Bearer (e.g. headless agents) must be able
    to present an MCP API key that way; a non-JWT bearer takes the API key path."""
    _patch_common(monkeypatch)
    monkeypatch.setattr(mcp_auth, "looks_like_jwt", lambda t: False)
    seen = {}

    async def fake_validate(key):
        seen["key"] = key
        return {"id": 1, "name": "k", "scope": "admin", "permissions": "db-query", "org_id": 1}

    async def fake_resolve_org(org_id, user_id=None):
        return 1, "ns"

    monkeypatch.setattr(mcp_auth, "validate_mcp_api_key", fake_validate)
    monkeypatch.setattr(mcp_auth, "_resolve_org", fake_resolve_org)

    perms = _dispatch(monkeypatch, {"Authorization": "Bearer forge_abc"})
    assert seen["key"] == "forge_abc"
    assert perms == frozenset({"db-query"})


def test_x_api_key_wins_over_bearer(monkeypatch):
    """An explicit X-API-Key header keeps precedence over a non-JWT bearer."""
    _patch_common(monkeypatch)
    monkeypatch.setattr(mcp_auth, "looks_like_jwt", lambda t: False)
    seen = {}

    async def fake_validate(key):
        seen["key"] = key
        return {"id": 1, "name": "k", "scope": "admin", "permissions": "db-query", "org_id": 1}

    async def fake_resolve_org(org_id, user_id=None):
        return 1, "ns"

    monkeypatch.setattr(mcp_auth, "validate_mcp_api_key", fake_validate)
    monkeypatch.setattr(mcp_auth, "_resolve_org", fake_resolve_org)

    _dispatch(monkeypatch, {"Authorization": "Bearer bearer_key", "X-API-Key": "header_key"})
    assert seen["key"] == "header_key"
