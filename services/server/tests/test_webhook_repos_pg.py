from fastapi import FastAPI
from fastapi.testclient import TestClient

from src import config, db
from src.api.routes import webhooks
from src.catalog import repos as repo_catalog
from src.catalog import selections
from src.mcp import memory as memory_module
from tests.pgutil import fetch, requires_pg, run_db, seed_org, seed_project

pytestmark = requires_pg


def test_a_push_indexes_the_repository_once_and_records_commits_in_every_selecting_project(pg_database, monkeypatch):
    monkeypatch.setattr(config.get_settings(), "webhook_secret", "s3cret")
    monkeypatch.setattr(config.get_settings(), "encryption_key", "test-key")
    remembered = []

    class FakeMemory:
        def add(self, text, user_id, metadata):
            remembered.append((user_id, metadata["repo"]))

    async def fake_get_memory(org_id):
        return FakeMemory()

    monkeypatch.setattr(memory_module, "_get_memory", fake_get_memory)

    async def seed():
        org = await seed_org("acme")
        alpha = await seed_project(org, "alpha")
        beta = await seed_project(org, "beta")
        await seed_project(org, "gamma")
        sign = await repo_catalog.create_repo(org, {"name": "AsterSign@develop", "type": "gitlab",
                                                    "url": "https://git.example.org/aster/AsterSign", "branch": "develop"})
        # Stesso nome dell'identificativo nel payload ma altro remoto: non va riconosciuto.
        await repo_catalog.create_repo(org, {"name": "AsterSign", "type": "gitlab",
                                             "url": "https://git.example.org/other/AsterSign", "branch": "main"})
        for project in (alpha, beta):
            await selections.select_resource(org, project, "repos", sign["id"], None, False)
        return sign["id"]

    sign_id = run_db(seed)
    app = FastAPI()
    app.include_router(webhooks.router, prefix="/api")
    app.router.add_event_handler("shutdown", db.close_db)
    db._pool = None
    payload = {
        "repository": {"name": "AsterSign", "git_http_url": "http://git.example.org/aster/AsterSign.git"},
        "commits": [{"message": "fix: retry the upload", "author": {"name": "dev"}}, {"message": "wip"}],
    }
    with TestClient(app) as client:
        response = client.post("/api/webhooks/index", json=payload, headers={"X-Webhook-Secret": "s3cret"})

    assert response.status_code == 200
    assert response.json()["repos"] == ["AsterSign@develop"]
    requests = run_db(lambda: fetch("SELECT repo_id, project_id FROM index_requests"))
    assert requests == [{"repo_id": sign_id, "project_id": None}]
    assert sorted(remembered) == [("acme--alpha", "AsterSign@develop"), ("acme--beta", "AsterSign@develop")]


def test_a_push_with_a_ref_only_matches_and_memorizes_that_branchs_repository(pg_database, monkeypatch):
    monkeypatch.setattr(config.get_settings(), "webhook_secret", "s3cret")
    monkeypatch.setattr(config.get_settings(), "encryption_key", "test-key")
    remembered = []

    class FakeMemory:
        def add(self, text, user_id, metadata):
            remembered.append((user_id, metadata["repo"]))

    async def fake_get_memory(org_id):
        return FakeMemory()

    monkeypatch.setattr(memory_module, "_get_memory", fake_get_memory)

    async def seed():
        org = await seed_org("acme")
        alpha = await seed_project(org, "alpha")
        beta = await seed_project(org, "beta")
        # Same URL, catalog repos on two branches: master is another project's
        # concern, only develop must react to a push to refs/heads/develop.
        master = await repo_catalog.create_repo(org, {"name": "AsterSign", "type": "gitlab",
                                                       "url": "https://git.example.org/aster/AsterSign", "branch": "master"})
        develop = await repo_catalog.create_repo(org, {"name": "AsterSign@develop", "type": "gitlab",
                                                        "url": "https://git.example.org/aster/AsterSign", "branch": "develop"})
        await selections.select_resource(org, alpha, "repos", master["id"], None, False)
        await selections.select_resource(org, beta, "repos", develop["id"], None, False)
        return master["id"], develop["id"]

    master_id, develop_id = run_db(seed)
    app = FastAPI()
    app.include_router(webhooks.router, prefix="/api")
    app.router.add_event_handler("shutdown", db.close_db)
    db._pool = None
    payload = {
        "ref": "refs/heads/develop",
        "repository": {"name": "AsterSign", "git_http_url": "http://git.example.org/aster/AsterSign.git"},
        "commits": [{"message": "fix: retry the upload", "author": {"name": "dev"}}],
    }
    with TestClient(app) as client:
        response = client.post("/api/webhooks/index", json=payload, headers={"X-Webhook-Secret": "s3cret"})

    assert response.status_code == 200
    assert response.json()["repos"] == ["AsterSign@develop"]
    requests = run_db(lambda: fetch("SELECT repo_id FROM index_requests"))
    assert requests == [{"repo_id": develop_id}]
    assert remembered == [("acme--beta", "AsterSign@develop")]
