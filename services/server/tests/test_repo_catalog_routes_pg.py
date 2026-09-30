import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src import config, db
from src.api import deps
from src.api.routes import catalog as catalog_routes
from src.api.routes import project_resources
from src.api.routes import repos as repo_routes
from src.org_settings import OrgSettings
from tests.pgutil import (
    fetch, fetchval, requires_pg, run_db, seed_org, seed_project, seed_project_member, seed_user,
)

pytestmark = requires_pg

REPO = {"type": "gitlab", "url": "https://git.example.org/aster/aster-desk.git", "branch": "main"}


@pytest.fixture(autouse=True)
def _settings(monkeypatch, tmp_path):
    monkeypatch.setattr(config.get_settings(), "encryption_key", "test-key")
    monkeypatch.setattr(config.get_settings(), "repos_cache_dir", str(tmp_path))


def _client(user_id: int, project_id=None) -> TestClient:
    app = FastAPI()
    for router in (catalog_routes.router, project_resources.router, repo_routes.router):
        app.include_router(router, prefix="/api")
    app.dependency_overrides[deps.get_current_user_id] = lambda: user_id
    app.router.add_event_handler("shutdown", db.close_db)
    db._pool = None
    client = TestClient(app)
    if project_id is not None:
        client.headers["X-Project-Id"] = str(project_id)
    return client


def _seed():
    async def scenario():
        org = await seed_org("acme")
        alpha = await seed_project(org, "alpha")
        beta = await seed_project(org, "beta")
        admin = await seed_user("admin", org, "admin")
        member = await seed_user("member", org, "member")
        viewer = await seed_user("viewer", org, "member")
        await seed_project_member(alpha, member, "member")
        await seed_project_member(alpha, viewer, "viewer")
        return {"org": org, "alpha": alpha, "beta": beta, "admin": admin, "member": member, "viewer": viewer}

    return run_db(scenario)


def test_only_admins_register_repositories_and_a_new_one_is_queued(pg_database):
    ids = _seed()
    with _client(ids["member"]) as client:
        assert client.get("/api/catalog/repos").status_code == 200
        assert client.post("/api/catalog/repos", json=REPO).status_code == 403
    with _client(ids["admin"]) as client:
        created = client.post("/api/catalog/repos", json=REPO)
        duplicate = client.post("/api/catalog/repos", json=dict(REPO, url="http://git.example.org/aster/aster-desk/"))
    assert created.status_code == 200
    repo = created.json()["repo"]
    assert (repo["name"], repo["url"]) == ("aster-desk", "https://git.example.org/aster/aster-desk")
    assert duplicate.status_code == 400 and "aster-desk" in duplicate.json()["detail"]
    queued = run_db(lambda: fetch("SELECT project_id, repo_id FROM index_requests"))
    assert queued == [{"project_id": None, "repo_id": repo["id"]}]


def test_a_project_sees_and_indexes_only_the_repositories_it_selected(pg_database):
    ids = _seed()
    with _client(ids["admin"]) as client:
        repo = client.post("/api/catalog/repos", json=REPO).json()["repo"]
    with _client(ids["member"], ids["alpha"]) as client:
        selected = client.post(f"/api/projects/{ids['alpha']}/resources", json={"kind": "repos", "resource_id": repo["id"]})
        listed = client.get("/api/repos").json()
        queued = client.post("/api/repos/aster-desk/index")
        resources = client.get(f"/api/projects/{ids['alpha']}/resources").json()
        assert client.post("/api/repos", json=REPO).status_code == 405
    with _client(ids["admin"], ids["beta"]) as client:
        beta_list = client.get("/api/repos").json()
        beta_index = client.post("/api/repos/aster-desk/index")
    with _client(ids["viewer"], ids["alpha"]) as client:
        viewer_index = client.post("/api/repos/aster-desk/index")
    assert selected.status_code == 200
    assert [r["name"] for r in listed] == ["aster-desk"]
    assert [r["name"] for r in resources["repos"]] == ["aster-desk"]
    assert queued.status_code == 200
    assert beta_list == [] and beta_index.status_code == 404
    assert viewer_index.status_code == 403
    by_project = run_db(lambda: fetch("SELECT project_id FROM index_requests WHERE project_id IS NOT NULL"))
    assert by_project == [{"project_id": ids["alpha"]}]


def test_deleting_a_selected_repository_needs_confirmation(pg_database):
    ids = _seed()
    with _client(ids["admin"]) as client:
        repo = client.post("/api/catalog/repos", json=REPO).json()["repo"]
        client.post(f"/api/projects/{ids['alpha']}/resources", json={"kind": "repos", "resource_id": repo["id"]})
        refused = client.delete(f"/api/catalog/repos/{repo['id']}")
        deleted = client.delete(f"/api/catalog/repos/{repo['id']}?confirm=true")
    assert refused.status_code == 409
    assert refused.json()["detail"]["projects"][0]["id"] == ids["alpha"]
    assert deleted.status_code == 200
    assert run_db(lambda: fetchval("SELECT count(*) FROM project_repos")) == 0


def test_a_github_repository_is_imported_by_full_name(pg_database, monkeypatch):
    ids = _seed()

    async def fake_settings(org_id):
        return OrgSettings(github_token="ghp-test")

    monkeypatch.setattr(catalog_routes, "get_org_settings", fake_settings)
    with _client(ids["admin"]) as client:
        imported = client.post("/api/catalog/repos/import", json={"provider": "github", "full_name": "acme/widget"})
    assert imported.status_code == 200
    repo = imported.json()["repo"]
    assert (repo["name"], repo["url"], repo["branch"]) == ("widget", "https://github.com/acme/widget", "main")
