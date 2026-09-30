from urllib.parse import urlparse

import pytest

from src import config
from src.catalog import selections
from src.datasources import service
from src.datasources.secrets import decrypt_secret, encrypt_secret
from tests.pgutil import TEST_DATABASE_URL, fetchval, requires_pg, run_db, seed_org, seed_project

pytestmark = requires_pg


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    monkeypatch.setattr(config.get_settings(), "encryption_key", "test-key")


def _data(**over):
    data = {"name": "erp", "engine": "mysql", "host": "127.0.0.1", "port": 3306,
            "database_name": "app", "username": "reader", "password": "secret",
            "options": {}, "description": None, "ssh_machine_id": None, "restricted": False}
    data.update(over)
    return data


async def _machine(org_id):
    return await fetchval(
        "INSERT INTO machines (org_id, name, host, username, auth_method, private_key_enc) "
        "VALUES ($1, 'astercare@10.0.0.6', '10.0.0.6', 'astercare', 'key', $2) RETURNING id",
        org_id, encrypt_secret("PEM"),
    )


def test_tunnel_fields_come_from_the_machine(pg_database):
    async def scenario():
        org = await seed_org("acme")
        machine = await _machine(org)
        direct = await service.create_connection(org, _data(name="direct"))
        tunnel = await service.create_connection(org, _data(name="tunnel", ssh_machine_id=machine))
        stored = await service.get_catalog_connection(org, tunnel["id"], include_secret=True)
        return direct, tunnel, stored

    direct, tunnel, stored = run_db(scenario)
    assert direct["ssh_enabled"] is False and direct["ssh_machine_id"] is None
    assert tunnel["ssh_enabled"] is True
    assert tunnel["ssh_machine_name"] == "astercare@10.0.0.6"
    assert tunnel["has_ssh_secret"] is True and "ssh_private_key_enc" not in tunnel
    assert decrypt_secret(stored["ssh_private_key_enc"]) == "PEM"


def test_machine_of_another_org_is_refused(pg_database):
    async def scenario():
        acme = await seed_org("acme")
        other = await seed_org("other")
        foreign = await _machine(other)
        with pytest.raises(ValueError, match="Machine not found"):
            await service.create_connection(acme, _data(ssh_machine_id=foreign))

    run_db(scenario)


def test_project_view_and_not_selected_error(pg_database):
    async def scenario():
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        chosen = await service.create_connection(org, _data(name="erp"))
        await service.create_connection(org, _data(name="crm", database_name="crm"))
        await selections.select_resource(org, project, "databases", chosen["id"], None, False)
        listed = await service.list_connections(org, project)
        found = await service.get_connection(org, project, "erp")
        with pytest.raises(service.ConnectionNotSelectedError) as not_selected:
            await service.get_connection(org, project, "crm")
        with pytest.raises(service.ConnectionNotFoundError) as missing:
            await service.get_connection(org, project, "nowhere")
        catalog = await service.list_catalog_connections(org)
        return chosen, listed, found, not_selected.value, missing.value, catalog

    chosen, listed, found, not_selected, missing, catalog = run_db(scenario)
    assert [c["id"] for c in listed] == [chosen["id"]]
    assert found["id"] == chosen["id"]
    assert "not available in this project" in str(not_selected)
    assert isinstance(not_selected, service.ConnectionNotFoundError)
    assert not isinstance(missing, service.ConnectionNotSelectedError)
    assert {c["name"]: c["project_count"] for c in catalog} == {"crm": 0, "erp": 1}


def test_find_connection_matches_null_values(pg_database):
    async def scenario():
        org = await seed_org("acme")
        machine = await _machine(org)
        tunnel = await service.create_connection(org, _data(ssh_machine_id=machine))
        return (
            tunnel["id"],
            await service.find_connection(org, machine, "127.0.0.1", 3306, "app"),
            await service.find_connection(org, None, "127.0.0.1", 3306, "app"),
        )

    tunnel_id, via_machine, direct = run_db(scenario)
    assert via_machine["id"] == tunnel_id
    assert direct is None


def test_update_keeps_the_password_and_delete_drops_selections(pg_database):
    async def scenario():
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        created = await service.create_connection(org, _data())
        updated = await service.update_connection(org, created["id"], _data(password="", restricted=True))
        stored = await service.get_catalog_connection(org, created["id"], include_secret=True)
        await selections.select_resource(org, project, "databases", created["id"], None, True)
        await service.delete_connection(org, created["id"])
        remaining = await fetchval("SELECT count(*) FROM project_db_connections")
        return updated, stored, remaining

    updated, stored, remaining = run_db(scenario)
    assert updated["restricted"] is True
    assert decrypt_secret(stored["password_enc"]) == "secret"
    assert remaining == 0


def test_connection_status_is_saved(pg_database):
    target = urlparse(TEST_DATABASE_URL)

    async def scenario():
        org = await seed_org("acme")
        created = await service.create_connection(org, _data(
            engine="postgresql", host=target.hostname, port=target.port,
            database_name=pg_database, username=target.username, password=target.password,
        ))
        result = await service.test_connection(org, created["id"])
        return result, await service.get_catalog_connection(org, created["id"])

    result, stored = run_db(scenario)
    assert result["status"] == "ok"
    assert stored["status"] == "ok"


def test_tunnelled_connection_reaches_the_database_through_the_machine(pg_database, monkeypatch):
    """Le credenziali del tunnel arrivano dalla macchina e il forward punta al database."""
    captured = {}

    def fake_tunnel(connection_id, ssh_cfg, remote_host, remote_port):
        captured["tunnel"] = (connection_id, ssh_cfg, remote_host, remote_port)
        return "127.0.0.1", 15432

    def fake_engine(connection_id, engine, url):
        captured["url"] = url
        return "engine"

    monkeypatch.setattr(service.engines, "ensure_tunnel", fake_tunnel)
    monkeypatch.setattr(service.engines, "get_engine", fake_engine)

    async def scenario():
        org = await seed_org("acme")
        machine = await _machine(org)
        tunnel = await service.create_connection(
            org, _data(name="tunnel", host="10.9.9.9", port=5433, ssh_machine_id=machine)
        )
        record = await service.get_catalog_connection(org, tunnel["id"], include_secret=True)
        return tunnel["id"], await service._resolve_engine(record)

    connection_id, engine = run_db(scenario)
    assert engine == "engine"
    tunnelled_id, ssh_cfg, remote_host, remote_port = captured["tunnel"]
    assert tunnelled_id == connection_id
    assert ssh_cfg["host"] == "10.0.0.6"
    assert ssh_cfg["username"] == "astercare"
    assert ssh_cfg["auth_method"] == "key"
    assert ssh_cfg["private_key"] == "PEM"
    assert (remote_host, remote_port) == ("10.9.9.9", 5433)
    # Il database si raggiunge sull'estremo locale del tunnel, non direttamente.
    assert (captured["url"].host, captured["url"].port) == ("127.0.0.1", 15432)


def test_resolve_connection_keeps_the_not_selected_error(pg_database):
    async def scenario():
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        await service.create_connection(org, _data(name="crm"))
        with pytest.raises(service.ConnectionNotSelectedError):
            await service.resolve_connection(org, project, "crm")

    run_db(scenario)
