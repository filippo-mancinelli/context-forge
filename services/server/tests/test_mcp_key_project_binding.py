import asyncio
from types import SimpleNamespace

from src.mcp import auth as mcp_auth
from src.mcp import context


class FakeURL:
    def __init__(self, path):
        self.path = path


class FakeRequest:
    method = "POST"

    def __init__(self, path, headers):
        self.url = FakeURL(path)
        self.scope = {"path": path}
        self.headers = headers
        self.state = SimpleNamespace()
        self.client = SimpleNamespace(host="test")


def _dispatch_with_key(monkeypatch, key_project_id, path_project_id, key_org_id=2, allowed_projects=None):
    # Il progetto arriva dal routing del path: monkeypatch del resolver.
    context.set_current_org_id(None)
    context.set_current_project_id(None)

    async def fake_resolve(org_slug, project_slug):
        return {
            "id": path_project_id, "org_id": 2, "name": "Webshop",
            "slug": project_slug, "memory_namespace": "acme--webshop",
            "org_slug": org_slug,
        }

    import src.projects as projects_mod
    monkeypatch.setattr(projects_mod, "resolve_project_by_slugs", fake_resolve)

    async def fake_validate(key):
        return {
            "id": 1, "name": "k", "scope": "read", "permissions": "context-read",
            "org_id": key_org_id, "project_id": key_project_id, "expires_at": None,
            "allowed_projects": allowed_projects,
        }

    monkeypatch.setattr(mcp_auth, "validate_mcp_api_key", fake_validate)

    async def call_next(request):
        return "ok"

    middleware = mcp_auth.MCPAuthMiddleware(lambda scope: None, auth_mode="enabled")
    req = FakeRequest("/mcp/acme/webshop", {"X-API-Key": "forge_x"})
    return asyncio.run(middleware.dispatch(req, call_next))


def test_key_bound_to_other_project_is_403(monkeypatch):
    response = _dispatch_with_key(monkeypatch, key_project_id=9, path_project_id=5)
    assert response.status_code == 403


def test_key_bound_to_same_project_passes(monkeypatch):
    response = _dispatch_with_key(monkeypatch, key_project_id=5, path_project_id=5)
    assert response == "ok"


def test_key_bound_to_other_org_with_no_project_is_403(monkeypatch):
    # Chiave con project_id NULL (nessun vincolo progetto) ma org_id diverso
    # dall'org del path: il guard su org deve intercettarla comunque.
    response = _dispatch_with_key(
        monkeypatch, key_project_id=None, path_project_id=5, key_org_id=3
    )
    assert response.status_code == 403


def test_key_bound_to_same_org_and_project_passes(monkeypatch):
    response = _dispatch_with_key(
        monkeypatch, key_project_id=5, path_project_id=5, key_org_id=2
    )
    assert response == "ok"


def test_multi_project_key_allowed_on_secondary_project(monkeypatch):
    # Key con allowed_projects [4, 5] e project_id legacy 4 (il primo):
    # sul path del progetto 5 deve passare, non 403.
    response = _dispatch_with_key(
        monkeypatch,
        key_project_id=4,
        path_project_id=5,
        allowed_projects=[4, 5],
    )
    assert response == "ok"


def test_key_scoped_elsewhere_still_rejected(monkeypatch):
    response = _dispatch_with_key(
        monkeypatch,
        key_project_id=4,
        path_project_id=5,
        allowed_projects=[4],
    )
    assert response.status_code == 403
