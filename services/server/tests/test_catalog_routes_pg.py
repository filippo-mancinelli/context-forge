import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src import config, db
from src.api import deps
from src.api.routes import catalog as catalog_routes
from src.api.routes import project_resources
from tests.pgutil import (
    requires_pg, run_db, seed_org, seed_project, seed_project_member, seed_user,
)

pytestmark = requires_pg


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    monkeypatch.setattr(config.get_settings(), "encryption_key", "test-key")


def _client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(catalog_routes.router, prefix="/api")
    app.include_router(project_resources.router, prefix="/api")
    app.dependency_overrides[deps.get_current_user_id] = lambda: user_id
    app.router.add_event_handler("shutdown", db.close_db)
    db._pool = None
    return TestClient(app)


def _seed():
    async def scenario():
        org = await seed_org("acme")
        alpha = await seed_project(org, "alpha")
        beta = await seed_project(org, "beta")
        admin = await seed_user("admin", org, "admin")
        member = await seed_user("member", org, "member")
        viewer = await seed_user("viewer", org, "member")
        outsider = await seed_user("outsider", org, "member")
        await seed_project_member(alpha, member, "member")
        await seed_project_member(alpha, viewer, "viewer")
        return {"org": org, "alpha": alpha, "beta": beta, "admin": admin,
                "member": member, "viewer": viewer, "outsider": outsider}

    return run_db(scenario)


MACHINE = {"name": "astercare@10.0.0.6", "host": "10.0.0.6", "username": "astercare",
           "auth_method": "key", "private_key": "PEM"}


def _catalog(ids) -> dict:
    """Macchina, cartella normale e cartella riservata create dall'admin."""
    with _client(ids["admin"]) as client:
        machine = client.post("/api/catalog/machines", json=MACHINE).json()["machine"]
        logs = client.post("/api/catalog/folders", json={
            "name": "logs", "machine_id": machine["id"], "root_path": "/srv/logs"}).json()["source"]
        secret = client.post("/api/catalog/folders", json={
            "name": "reports", "machine_id": machine["id"], "root_path": "/srv/reports",
            "restricted": True}).json()["source"]
    return {"machine": machine, "logs": logs, "secret": secret}


def test_members_read_the_catalog_but_only_admins_write_it(pg_database):
    ids = _seed()
    with _client(ids["member"]) as client:
        assert client.get("/api/catalog/machines").status_code == 200
        assert client.post("/api/catalog/machines", json=MACHINE).status_code == 403
    with _client(ids["admin"]) as client:
        created = client.post("/api/catalog/machines", json=MACHINE)
        assert created.status_code == 200
        duplicate = client.post("/api/catalog/machines", json=MACHINE)
        assert duplicate.status_code == 400
        listed = client.get("/api/catalog/machines").json()["machines"]
    assert listed[0]["has_secret"] is True
    assert "private_key_enc" not in listed[0] and "private_key" not in listed[0]


def test_project_member_selects_normal_resources_only(pg_database):
    ids = _seed()
    catalog = _catalog(ids)
    url = f"/api/projects/{ids['alpha']}/resources"
    with _client(ids["member"]) as client:
        ok = client.post(url, json={"kind": "folders", "resource_id": catalog["logs"]["id"]})
        restricted = client.post(url, json={"kind": "folders", "resource_id": catalog["secret"]["id"]})
        unknown = client.post(url, json={"kind": "repos", "resource_id": 1})
        listed = client.get(url).json()
    assert ok.status_code == 200 and ok.json()["already_selected"] is False
    assert restricted.status_code == 403
    assert unknown.status_code == 400
    assert [f["name"] for f in listed["folders"]] == ["logs"]
    assert listed["databases"] == []
    with _client(ids["admin"]) as client:
        assert client.post(url, json={"kind": "folders", "resource_id": catalog["secret"]["id"]}).status_code == 200


def test_viewers_and_non_members_cannot_select(pg_database):
    ids = _seed()
    catalog = _catalog(ids)
    body = {"kind": "folders", "resource_id": catalog["logs"]["id"]}
    with _client(ids["viewer"]) as client:
        assert client.post(f"/api/projects/{ids['alpha']}/resources", json=body).status_code == 403
    with _client(ids["outsider"]) as client:
        assert client.post(f"/api/projects/{ids['alpha']}/resources", json=body).status_code == 403
        assert client.get(f"/api/projects/{ids['alpha']}/resources").status_code == 403
        # Senza alcun progetto nemmeno il catalogo è consultabile.
        assert client.get("/api/catalog/folders").status_code == 403


def test_delete_asks_for_confirmation_while_projects_use_the_folder(pg_database):
    ids = _seed()
    catalog = _catalog(ids)
    folder_id = catalog["logs"]["id"]
    with _client(ids["admin"]) as client:
        client.post(f"/api/projects/{ids['beta']}/resources", json={"kind": "folders", "resource_id": folder_id})
        assert [p["slug"] for p in client.get(f"/api/catalog/folders/{folder_id}/projects").json()["projects"]] == ["beta"]
        refused = client.delete(f"/api/catalog/folders/{folder_id}")
        in_use = client.delete(f"/api/catalog/machines/{catalog['machine']['id']}")
        confirmed = client.delete(f"/api/catalog/folders/{folder_id}?confirm=true")
    # I nomi degli altri progetti servono alla conferma di cancellazione: solo admin.
    with _client(ids["member"]) as client:
        assert client.get(f"/api/catalog/folders/{folder_id}/projects").status_code == 403
    assert refused.status_code == 409
    assert refused.json()["detail"]["projects"][0]["slug"] == "beta"
    assert in_use.status_code == 409 and in_use.json()["detail"]["folders"] == 2
    assert confirmed.status_code == 200


def test_deselect_then_not_found(pg_database):
    ids = _seed()
    catalog = _catalog(ids)
    folder_id = catalog["logs"]["id"]
    with _client(ids["member"]) as client:
        client.post(f"/api/projects/{ids['alpha']}/resources", json={"kind": "folders", "resource_id": folder_id})
        first = client.delete(f"/api/projects/{ids['alpha']}/resources/folders/{folder_id}")
        second = client.delete(f"/api/projects/{ids['alpha']}/resources/folders/{folder_id}")
    assert first.status_code == 200
    assert second.status_code == 404


def test_database_catalog_round_trip(pg_database):
    ids = _seed()
    catalog = _catalog(ids)
    with _client(ids["admin"]) as client:
        created = client.post("/api/catalog/databases", json={
            "name": "chat-db", "engine": "mysql", "host": "127.0.0.1", "port": 3306,
            "database_name": "asterchat", "username": "astercare_ro", "password": "pw",
            "ssh_machine_id": catalog["machine"]["id"]})
        bad_engine = client.post("/api/catalog/databases", json={"name": "x", "engine": "oracle"})
        listed = client.get("/api/catalog/databases").json()
    assert created.status_code == 200
    assert created.json()["connection"]["ssh_machine_name"] == "astercare@10.0.0.6"
    assert bad_engine.status_code == 400
    assert listed["connections"][0]["project_count"] == 0
    assert "mysql" in listed["engines"]
