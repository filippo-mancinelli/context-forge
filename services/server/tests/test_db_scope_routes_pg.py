import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src import config, db
from src.api import deps
from src.api.routes import catalog as catalog_routes
from src.api.routes import project_resources
from src.catalog import selections
from src.datasources import introspect, project_scopes, service
from tests.pgutil import (
    execute, fetch, requires_pg, run_db, seed_org, seed_project, seed_project_member, seed_user,
)

pytestmark = requires_pg


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    monkeypatch.setattr(config.get_settings(), "encryption_key", "test-key")


def _client(user_id: int, raise_server_exceptions: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(catalog_routes.router, prefix="/api")
    app.include_router(project_resources.router, prefix="/api")
    app.dependency_overrides[deps.get_current_user_id] = lambda: user_id
    app.router.add_event_handler("shutdown", db.close_db)
    db._pool = None
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def _connection(**over):
    data = {"name": "erp", "engine": "postgresql", "host": "127.0.0.1", "port": 5432,
            "database_name": "app", "username": "reader", "password": "secret",
            "options": {}, "description": None, "ssh_machine_id": None, "restricted": False}
    data.update(over)
    return data


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
        erp = await service.create_connection(org, _connection())
        vault = await service.create_connection(org, _connection(name="vault", restricted=True))
        crm = await service.create_connection(
            org, _connection(name="crm", engine="mysql", port=3306, database_name="crm")
        )
        return {"org": org, "alpha": alpha, "beta": beta, "admin": admin, "member": member,
                "viewer": viewer, "erp": erp["id"], "vault": vault["id"], "crm": crm["id"]}

    return run_db(scenario)


def test_member_links_the_same_connection_on_two_scopes(pg_database):
    ids = _seed()
    run_db(lambda: execute("DROP TABLE IF EXISTS project_db_connections"))
    url = f"/api/projects/{ids['alpha']}/databases"
    with _client(ids["member"]) as client:
        first = client.post(url, json={"connection_id": ids["erp"], "database": "app", "schema": "vendite"})
        second = client.post(url, json={"connection_id": ids["erp"], "database": "app", "schema": "acquisti"})
        same_scope = client.post(url, json={"connection_id": ids["erp"], "database": "app", "schema": "vendite"})
        same_alias = client.post(url, json={"connection_id": ids["erp"], "database": "app",
                                             "schema": "storico", "alias": "ERP"})
        bad_shape = client.post(url, json={"connection_id": ids["crm"], "database": "crm", "schema": "altro"})
        numeric_alias = client.post(url, json={"connection_id": ids["crm"], "database": "crm", "alias": "42"})
        listed = client.get(f"/api/projects/{ids['alpha']}/resources").json()["databases"]
    scopes = run_db(lambda: fetch(
        "SELECT id FROM project_db_scopes WHERE project_id = $1 AND db_connection_id = $2",
        ids["alpha"], ids["erp"],
    ))

    assert first.status_code == 200
    scope = first.json()["scope"]
    assert scope["alias"] == "erp" and scope["scope_label"] == "app.vendite"
    assert scope["scope_inferred"] is False
    assert second.json()["scope"]["alias"] == "erp/app.acquisti"
    assert same_scope.status_code == 409 and same_alias.status_code == 409
    assert bad_shape.status_code == 400 and numeric_alias.status_code == 400
    assert sorted(r["alias"] for r in listed) == ["erp", "erp/app.acquisti"]
    # Un perimetro per ciascuno dei due schemi collegati alla stessa connessione.
    assert len(scopes) == 2


def test_viewers_cannot_link_and_restricted_needs_an_org_admin(pg_database):
    ids = _seed()
    url = f"/api/projects/{ids['alpha']}/databases"
    with _client(ids["viewer"]) as client:
        assert client.post(url, json={"connection_id": ids["erp"], "database": "app"}).status_code == 403
    with _client(ids["member"]) as client:
        assert client.post(url, json={"connection_id": ids["vault"], "database": "app"}).status_code == 403
        assert client.post(url, json={"connection_id": 999999, "database": "app"}).status_code == 404
    with _client(ids["admin"]) as client:
        assert client.post(url, json={"connection_id": ids["vault"], "database": "app"}).status_code == 200


def test_only_an_org_admin_re_points_a_restricted_scope(pg_database):
    ids = _seed()
    url = f"/api/projects/{ids['alpha']}/databases"
    with _client(ids["admin"]) as client:
        linked = client.post(url, json={"connection_id": ids["vault"], "database": "app",
                                        "schema": "vendite", "alias": "vault"}).json()["scope"]
    scope_url = f"{url}/{linked['scope_id']}"

    def row():
        return run_db(lambda: fetch(
            "SELECT database_name, schema_name, alias FROM project_db_scopes WHERE id = $1",
            linked["scope_id"],
        ))[0]

    with _client(ids["member"]) as client:
        re_pointed = client.put(scope_url, json={"database": "app", "schema": "paghe", "alias": "vault"})
        other_db = client.put(scope_url, json={"database": "hr", "schema": "vendite", "alias": "vault"})
        after_refusal = dict(row())
        renamed = client.put(scope_url, json={"database": "app", "schema": "vendite", "alias": "cassaforte"})
        # Lo schema omesso vale public su PostgreSQL: è un altro perimetro.
        implicit = client.put(scope_url, json={"database": "app", "alias": "cassaforte"})
    with _client(ids["admin"]) as client:
        by_admin = client.put(scope_url, json={"database": "app", "schema": "paghe", "alias": "cassaforte"})

    assert re_pointed.status_code == 403 and other_db.status_code == 403
    assert implicit.status_code == 403
    assert after_refusal == {"database_name": "app", "schema_name": "vendite", "alias": "vault"}
    assert renamed.status_code == 200 and renamed.json()["scope"]["alias"] == "cassaforte"
    assert by_admin.status_code == 200
    assert dict(row()) == {"database_name": "app", "schema_name": "paghe", "alias": "cassaforte"}


def test_an_unexpected_error_is_not_echoed_as_a_bad_request(pg_database, monkeypatch):
    ids = _seed()

    async def explode(*args, **kwargs):
        raise ValueError("list index out of range")

    monkeypatch.setattr(project_scopes, "create_scope", explode)
    with _client(ids["member"], raise_server_exceptions=False) as client:
        broken = client.post(f"/api/projects/{ids['alpha']}/databases",
                             json={"connection_id": ids["erp"], "database": "app"})

    # Solo la forma del perimetro e l'alias sono richieste da correggere: un
    # guasto qualunque resta un errore del server, non un messaggio al client.
    assert broken.status_code == 500


def test_changing_a_scope_confirms_it(pg_database):
    ids = _seed()
    run_db(lambda: selections.select_resource(ids["org"], ids["alpha"], "databases", ids["erp"], None, False))
    with _client(ids["member"]) as client:
        scope = client.get(f"/api/projects/{ids['alpha']}/resources").json()["databases"][0]
        changed = client.put(f"/api/projects/{ids['alpha']}/databases/{scope['scope_id']}",
                             json={"database": "app", "schema": "vendite", "alias": "erp"})
    with _client(ids["admin"]) as client:
        foreign = client.put(f"/api/projects/{ids['beta']}/databases/{scope['scope_id']}",
                             json={"database": "app", "schema": "vendite", "alias": "erp"})

    assert scope["scope_inferred"] is True
    assert changed.status_code == 200
    body = changed.json()["scope"]
    assert body["scope_inferred"] is False and body["scope_schema"] == "vendite"
    assert body["scope_label"] == "app.vendite"
    # Il collegamento di un altro progetto non si raggiunge passando un id qualsiasi.
    assert foreign.status_code == 404


def test_each_scope_is_removed_on_its_own_and_only_once(pg_database):
    ids = _seed()
    run_db(lambda: execute("DROP TABLE IF EXISTS project_db_connections"))
    url = f"/api/projects/{ids['alpha']}/databases"
    with _client(ids["member"]) as client:
        a = client.post(url, json={"connection_id": ids["erp"], "database": "app", "schema": "vendite"}).json()
        b = client.post(url, json={"connection_id": ids["erp"], "database": "app", "schema": "acquisti"}).json()
        first = client.delete(f"{url}/{a['scope']['scope_id']}")
    after_first = run_db(lambda: fetch(
        "SELECT id FROM project_db_scopes WHERE project_id = $1 AND db_connection_id = $2",
        ids["alpha"], ids["erp"],
    ))
    with _client(ids["member"]) as client:
        second = client.delete(f"{url}/{b['scope']['scope_id']}")
        again = client.delete(f"{url}/{b['scope']['scope_id']}")
    after_last = run_db(lambda: fetch(
        "SELECT id FROM project_db_scopes WHERE project_id = $1 AND db_connection_id = $2",
        ids["alpha"], ids["erp"],
    ))

    assert first.status_code == 200 and len(after_first) == 1
    assert second.status_code == 200 and after_last == []
    assert again.status_code == 404


def test_catalog_lists_the_scopes_of_a_connection(pg_database, monkeypatch):
    ids = _seed()

    async def fake_resolve(record):
        return object()

    monkeypatch.setattr(service, "_resolve_engine", fake_resolve)
    monkeypatch.setattr(introspect, "list_scopes",
                        lambda engine, open_database, deadline=None: [{"database": "app", "schema": "public"}])
    with _client(ids["member"]) as client:
        stored = client.get(f"/api/catalog/databases/{ids['erp']}/scopes")
        live = client.get(f"/api/catalog/databases/{ids['erp']}/scopes?refresh=true")
        missing = client.get("/api/catalog/databases/999999/scopes")

    assert stored.status_code == 200
    assert stored.json() == {"scopes": [], "checked_at": None, "live": False, "error": None}
    assert live.json()["live"] is True
    assert live.json()["scopes"] == [{"database": "app", "schema": "public", "label": "app.public"}]
    assert missing.status_code == 404


def test_only_a_project_member_reads_the_scopes_from_the_server(pg_database, monkeypatch):
    ids = _seed()

    async def fake_resolve(record):
        return object()

    monkeypatch.setattr(service, "_resolve_engine", fake_resolve)
    monkeypatch.setattr(introspect, "list_scopes",
                        lambda engine, open_database, deadline=None: [{"database": "app", "schema": "public"}])
    erp = f"/api/catalog/databases/{ids['erp']}/scopes"
    vault = f"/api/catalog/databases/{ids['vault']}/scopes"
    with _client(ids["viewer"]) as client:
        viewer_snapshot = client.get(erp)
        viewer_live = client.get(f"{erp}?refresh=true")
    with _client(ids["member"]) as client:
        member_live = client.get(f"{erp}?refresh=true")
        member_restricted = client.get(f"{vault}?refresh=true")
    with _client(ids["admin"]) as client:
        admin_restricted = client.get(f"{vault}?refresh=true")

    # Un viewer consulta la fotografia ma non fa aprire una connessione al server.
    assert viewer_snapshot.status_code == 200 and viewer_snapshot.json()["live"] is False
    assert viewer_live.status_code == 403
    assert member_live.status_code == 200 and member_live.json()["live"] is True
    # Una connessione riservata la legge dal vivo solo un admin dell'organizzazione.
    assert member_restricted.status_code == 403
    assert admin_restricted.status_code == 200 and admin_restricted.json()["live"] is True
