import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import deps
from src.api.routes import github as github_routes
from src.api.routes import gitlab as gitlab_routes
from src.org_settings import OrgSettings

ACTIVE_PROJECT = deps.ActiveProject(
    org_id=1, project_id=7, role="owner", namespace="acme--widgets", name="Widgets", org_name="Acme"
)


class _Cfg:
    def __init__(self):
        self.repos = []


class _FakeResponse:
    status_code = 200
    text = ""

    @staticmethod
    def json():
        return {"web_url": "https://gitlab.example.com/acme/widget", "default_branch": "main"}


class _FakeClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, *args, **kwargs):
        return _FakeResponse()


@pytest.fixture
def wired(monkeypatch):
    """Both add routes with their persistence stubbed; returns cfg + captured binding."""
    cfg = _Cfg()
    bound = {}
    holder = {"project": None}

    async def fake_get_org_config(org_id):
        return cfg

    async def fake_persist_org_config(org_id, _cfg):
        return None

    async def fake_sync_repos_config(org_id):
        return None

    async def fake_bind(org_id, project_id, repo_name):
        bound.update(org_id=org_id, project_id=project_id, name=repo_name)

    async def fake_holder(org_id, repo_name):
        return holder["project"]

    for module in (gitlab_routes, github_routes):
        monkeypatch.setattr(module, "get_org_config", fake_get_org_config)
        monkeypatch.setattr(module, "persist_org_config", fake_persist_org_config)
        monkeypatch.setattr(module, "sync_repos_config", fake_sync_repos_config)
        monkeypatch.setattr(module, "bind_repo_to_project", fake_bind)
        monkeypatch.setattr(module, "get_repo_project_name", fake_holder)

    monkeypatch.setattr(gitlab_routes.httpx, "AsyncClient", lambda *a, **kw: _FakeClient())

    async def fake_get_org_settings(org_id):
        assert org_id == ACTIVE_PROJECT.org_id
        return OrgSettings(gitlab_token="glpat-test", github_token="ghp-test")

    monkeypatch.setattr(gitlab_routes, "get_org_settings", fake_get_org_settings)

    app = FastAPI()
    app.include_router(gitlab_routes.router)
    app.include_router(github_routes.router)
    app.dependency_overrides[deps.get_active_project] = lambda: ACTIVE_PROJECT

    return TestClient(app), cfg, bound, holder


def test_gitlab_add_binds_repo_to_active_project(wired):
    client, cfg, bound, _ = wired
    resp = client.post("/gitlab/repos/add", json={"full_name": "acme/widget"})

    assert resp.status_code == 200
    assert bound == {"org_id": 1, "project_id": 7, "name": "acme-widget"}
    assert [r.name for r in cfg.repos] == ["acme-widget"]


def test_github_add_binds_repo_to_active_project(wired):
    client, cfg, bound, _ = wired
    resp = client.post("/github/repos/add", json={"full_name": "acme/widget"})

    assert resp.status_code == 200
    assert bound == {"org_id": 1, "project_id": 7, "name": "acme-widget"}
    assert [r.name for r in cfg.repos] == ["acme-widget"]


@pytest.mark.parametrize("path", ["/gitlab/repos/add", "/github/repos/add"])
def test_duplicate_reports_the_project_holding_the_repo(wired, path):
    client, cfg, _, holder = wired
    holder["project"] = "Default"
    client.post(path, json={"full_name": "acme/widget"})

    resp = client.post(path, json={"full_name": "acme/widget"})

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Repository already configured in project 'Default'"
