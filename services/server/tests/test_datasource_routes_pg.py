import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src import config, db
from src.api import deps
from src.api.routes import datasources as datasource_routes
from src.datasources import introspect, service
from tests.pgutil import fetch, requires_pg, run_db, seed_user
from tests.test_db_scope_isolation_pg import seed_two_projects

pytestmark = requires_pg


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    monkeypatch.setattr(config.get_settings(), "encryption_key", "test-key")


@pytest.fixture
def no_remote(monkeypatch):
    """Nessun server remoto: engine, introspezione ed esecuzione sono finti."""

    async def fake_engine(record):
        return object()

    monkeypatch.setattr(service, "_resolve_engine", fake_engine)
    monkeypatch.setattr(
        introspect, "get_overview",
        lambda engine, schema=None: {"schema": schema, "schemas": [schema], "tables": [], "views": []},
    )
    monkeypatch.setattr(
        service, "_execute_readonly", lambda engine, sql, max_rows: (["n"], [{"n": 1}], False)
    )


def _client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(datasource_routes.router, prefix="/api")
    app.dependency_overrides[deps.get_current_user_id] = lambda: user_id
    app.router.add_event_handler("shutdown", db.close_db)
    db._pool = None
    return TestClient(app)


def _seed() -> dict:
    async def scenario():
        ids = await seed_two_projects()
        ids["admin"] = await seed_user("admin", ids["org"], "admin")
        return ids

    return run_db(scenario)


def test_detail_routes_take_the_scope_id(pg_database, no_remote):
    ids = _seed()
    headers = {"X-Project-Id": str(ids["alpha"])}
    with _client(ids["admin"]) as client:
        by_scope = client.get(f"/api/datasources/{ids['acquisti']}/schema", headers=headers)
        by_single_connection = client.get(f"/api/datasources/{ids['crm']}/schema", headers=headers)
        by_shared_connection = client.get(f"/api/datasources/{ids['erp']}/schema", headers=headers)
        other_project = client.get(f"/api/datasources/{ids['crm_beta']}/schema", headers=headers)
    assert by_scope.status_code == 200
    assert by_scope.json()["alias"] == "erp-acquisti" and by_scope.json()["schema"] == "acquisti"
    # Compatibilità: l'id di una connessione con un solo perimetro apre quel perimetro.
    assert by_single_connection.status_code == 200 and by_single_connection.json()["alias"] == "crm"
    assert by_shared_connection.status_code == 409
    assert "erp-vendite" in by_shared_connection.json()["detail"]
    assert other_project.status_code == 404


def test_out_of_scope_requests_get_400(pg_database, no_remote):
    ids = _seed()
    headers = {"X-Project-Id": str(ids["alpha"])}
    with _client(ids["admin"]) as client:
        schema = client.get(f"/api/datasources/{ids['vendite']}/schema?schema=acquisti",
                            headers=headers)
        table = client.get(f"/api/datasources/{ids['vendite']}/tables/ordini?schema=acquisti",
                           headers=headers)
        query = client.post(f"/api/datasources/{ids['vendite']}/query",
                            json={"sql": "SELECT * FROM acquisti.ordini"}, headers=headers)
        catalog = client.post(f"/api/datasources/{ids['crm']}/query",
                              json={"sql": "SELECT * FROM information_schema.tables"},
                              headers=headers)
        inside = client.post(f"/api/datasources/{ids['vendite']}/query",
                             json={"sql": "SELECT * FROM vendite.ordini"}, headers=headers)
    assert schema.status_code == 400 and "app.vendite" in schema.json()["detail"]
    assert table.status_code == 400 and "app.vendite" in table.json()["detail"]
    assert query.status_code == 400 and "acquisti.ordini" in query.json()["detail"]
    assert catalog.status_code == 400 and "system catalogs" in catalog.json()["detail"]
    assert inside.status_code == 200 and inside.json()["rows"] == [{"n": 1}]


def test_annotations_and_log_go_through_the_scope(pg_database, no_remote):
    ids = _seed()
    headers = {"X-Project-Id": str(ids["alpha"])}
    base = f"/api/datasources/{ids['vendite']}"
    with _client(ids["admin"]) as client:
        saved = client.put(f"{base}/annotations", headers=headers, json={"annotations": [
            {"schema_name": "", "table_name": "ordini", "description": "Ordini"},
        ]})
        refused = client.put(f"{base}/annotations", headers=headers, json={"annotations": [
            {"schema_name": "acquisti", "table_name": "fornitori", "description": "x"},
        ]})
        listed = client.get(f"{base}/annotations", headers=headers).json()
        client.post(f"{base}/query", json={"sql": "SELECT 1"}, headers=headers)
        log = client.get(f"{base}/log", headers=headers).json()["log"]
    assert saved.status_code == 200 and refused.status_code == 400
    assert [(a["schema_name"], a["table_name"]) for a in listed["annotations"]] == [("vendite", "ordini")]
    assert log[0]["schema_name"] == "vendite"
    rows = run_db(lambda: fetch("SELECT schema_name FROM db_annotations"))
    assert rows == [{"schema_name": "vendite"}]
