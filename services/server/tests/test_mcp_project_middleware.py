import asyncio
from types import SimpleNamespace

from src.mcp import auth as mcp_auth
from src.mcp import context


class FakeURL:
    def __init__(self, path):
        self.path = path


class FakeRequest:
    method = "POST"

    def __init__(self, path, headers=None):
        self.url = FakeURL(path)
        self.scope = {"path": path}
        self.headers = headers or {}
        self.state = SimpleNamespace()
        self.client = SimpleNamespace(host="test")
        self.base_url = "http://testserver/"


def _run(request, auth_mode="disabled", resolver=None):
    context.set_current_org_id(None)
    context.set_current_project_id(None)
    context.set_current_namespace(None)
    captured = {}

    async def call_next(req):
        captured["path"] = req.scope["path"]
        captured["project"] = context.get_current_project_id()
        captured["org"] = context.get_current_org_id()
        captured["namespace"] = context.get_current_namespace()
        return "ok"

    middleware = mcp_auth.MCPAuthMiddleware(lambda scope: None, auth_mode=auth_mode)
    if resolver is not None:
        import src.projects as projects_mod
        projects_mod_orig = projects_mod.resolve_project_by_slugs
        projects_mod.resolve_project_by_slugs = resolver
        try:
            response = asyncio.run(middleware.dispatch(request, call_next))
        finally:
            projects_mod.resolve_project_by_slugs = projects_mod_orig
    else:
        response = asyncio.run(middleware.dispatch(request, call_next))
    return response, captured


def _fake_resolver(org_slug, project_slug):
    async def resolver(o, p):
        if (o, p) == (org_slug, project_slug):
            return {
                "id": 5, "org_id": 2, "name": "Webshop", "slug": p,
                "memory_namespace": f"{o}--{p}", "org_slug": o,
            }
        return None
    return resolver


def test_project_path_sets_context_and_rewrites():
    req = FakeRequest("/mcp/acme/webshop")
    response, captured = _run(req, auth_mode="disabled",
                              resolver=_fake_resolver("acme", "webshop"))
    assert response == "ok"
    assert captured["path"] == "/mcp"
    assert captured["project"] == 5
    assert captured["org"] == 2
    assert captured["namespace"] == "acme--webshop"


def test_unknown_project_is_404():
    req = FakeRequest("/mcp/acme/nope")
    response, captured = _run(req, auth_mode="disabled",
                              resolver=_fake_resolver("acme", "webshop"))
    assert response.status_code == 404
    assert "path" not in captured


def test_bare_mcp_passes_in_disabled_mode():
    req = FakeRequest("/mcp")
    response, captured = _run(req, auth_mode="disabled")
    assert response == "ok"
    assert captured["project"] is None  # fallback ai default a valle


def test_bare_mcp_is_404_when_auth_enabled(monkeypatch):
    req = FakeRequest("/mcp")
    response, captured = _run(req, auth_mode="enabled")
    assert response.status_code == 404


def test_org_level_unknown_org_is_404(monkeypatch):
    async def no_org(slug):
        return None

    monkeypatch.setattr(mcp_auth, "get_organization_by_slug", no_org)
    req = FakeRequest("/mcp/acme")
    response, _ = _run(req, auth_mode="enabled")
    assert response.status_code == 404


def test_org_level_known_org_without_auth_is_401(monkeypatch):
    async def org(slug):
        return {"id": 2, "memory_namespace": "acme"}

    monkeypatch.setattr(mcp_auth, "get_organization_by_slug", org)
    req = FakeRequest("/mcp/acme")
    response, captured = _run(req, auth_mode="enabled")
    # Org risolta, path riscritto a /mcp, ma nessuna credenziale -> 401.
    assert response.status_code == 401


def test_org_level_forwards_to_bare_mcp_path(monkeypatch):
    async def org(slug):
        return {"id": 2, "memory_namespace": "acme"}

    monkeypatch.setattr(mcp_auth, "get_organization_by_slug", org)
    req = FakeRequest("/mcp/acme")
    _run(req, auth_mode="disabled")
    # In modalità disabled l'org-level inoltra la richiesta con path /mcp.
    assert req.scope["path"] == "/mcp"
