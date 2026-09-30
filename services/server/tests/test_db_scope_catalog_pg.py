import json
import time

import pytest

from src import config
from src.datasources import engines, introspect, scope_catalog, service
from tests.pgutil import execute, fetch, requires_pg, run_db, seed_org

pytestmark = requires_pg


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    monkeypatch.setattr(config.get_settings(), "encryption_key", "test-key")


def _data(**over):
    data = {"name": "erp", "engine": "postgresql", "host": "127.0.0.1", "port": 5432,
            "database_name": "app", "username": "reader", "password": "secret",
            "options": {}, "description": None, "ssh_machine_id": None, "restricted": False}
    data.update(over)
    return data


def _live(monkeypatch, answer):
    """Il server remoto è un fake: risponde con ``answer`` o lo solleva.
    La lista restituita raccoglie la scadenza passata a ogni lettura."""
    calls = []

    async def fake_resolve(record):
        return object()

    def fake_list(engine, open_database, deadline=None):
        calls.append(deadline)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(service, "_resolve_engine", fake_resolve)
    monkeypatch.setattr(introspect, "list_scopes", fake_list)
    return calls


def test_refresh_reads_the_server_and_remembers_the_answer(pg_database, monkeypatch):
    deadlines = _live(monkeypatch, [{"database": "app", "schema": "public"},
                                    {"database": "app", "schema": "vendite"}])

    async def scenario():
        org = await seed_org("acme")
        conn = await service.create_connection(org, _data())
        live = await scope_catalog.list_available_scopes(org, conn["id"], refresh=True)
        stored = await scope_catalog.list_available_scopes(org, conn["id"], refresh=False)
        return live, stored

    live, stored = run_db(scenario)
    # La lettura dal vivo porta con sé la scadenza che la limita.
    assert len(deadlines) == 1 and deadlines[0] > time.monotonic()
    assert live["live"] is True and live["error"] is None
    assert [s["label"] for s in live["scopes"]] == ["app.public", "app.vendite"]
    assert live["checked_at"] is not None
    # Senza refresh si legge la fotografia dell'ultimo controllo, dichiarata come tale.
    assert stored["live"] is False and stored["error"] is None
    assert stored["scopes"] == live["scopes"] and stored["checked_at"] == live["checked_at"]


def test_an_unreachable_server_returns_the_last_snapshot(pg_database, monkeypatch):
    _live(monkeypatch, RuntimeError("could not connect to server"))

    async def scenario():
        org = await seed_org("acme")
        conn = await service.create_connection(org, _data())
        await execute(
            "UPDATE db_connections SET available_scopes = $2::jsonb, "
            "scopes_checked_at = '2026-09-18T08:00:00+00' WHERE id = $1",
            conn["id"], json.dumps([{"database": "app", "schema": "public"}]),
        )
        result = await scope_catalog.list_available_scopes(org, conn["id"], refresh=True)
        kept = await fetch("SELECT available_scopes FROM db_connections WHERE id = $1", conn["id"])
        return result, kept

    result, kept = run_db(scenario)
    assert result["live"] is False and "could not connect" in result["error"]
    assert result["scopes"] == [{"database": "app", "schema": "public", "label": "app.public"}]
    assert result["checked_at"].startswith("2026-09-18T08:00:00")
    # Un fallimento non cancella la fotografia precedente.
    assert json.loads(kept[0]["available_scopes"]) == [{"database": "app", "schema": "public"}]


def test_unknown_connection_is_not_found(pg_database):
    async def scenario():
        org = await seed_org("acme")
        await scope_catalog.list_available_scopes(org, 999999, refresh=False)

    with pytest.raises(service.ConnectionNotFoundError):
        run_db(scenario)


def test_a_successful_connection_test_refreshes_the_scopes(pg_database, monkeypatch):
    _live(monkeypatch, [{"database": "crm", "schema": None}])
    monkeypatch.setattr(engines, "ping", lambda engine: None)

    async def scenario():
        org = await seed_org("acme")
        conn = await service.create_connection(
            org, _data(name="crm", engine="mysql", port=3306, database_name="crm")
        )
        result = await service.test_connection(org, conn["id"])
        stored = await fetch(
            "SELECT available_scopes, scopes_checked_at FROM db_connections WHERE id = $1",
            conn["id"],
        )
        return result, stored[0]

    result, stored = run_db(scenario)
    assert result["status"] == "ok"
    assert json.loads(stored["available_scopes"]) == [{"database": "crm", "schema": None}]
    assert stored["scopes_checked_at"] is not None


def test_a_failed_connection_test_leaves_the_snapshot_alone(pg_database, monkeypatch):
    calls = _live(monkeypatch, [{"database": "crm", "schema": None}])

    def refuse(engine):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(engines, "ping", refuse)

    async def scenario():
        org = await seed_org("acme")
        conn = await service.create_connection(org, _data())
        result = await service.test_connection(org, conn["id"])
        stored = await fetch(
            "SELECT scopes_checked_at FROM db_connections WHERE id = $1", conn["id"]
        )
        return result, stored[0]

    result, stored = run_db(scenario)
    assert result["status"] == "error"
    assert calls == [] and stored["scopes_checked_at"] is None


def test_a_broken_scope_refresh_does_not_spoil_a_successful_test(pg_database, monkeypatch):
    """Un guasto locale nel refresh (non il server remoto) resta un guasto secondario."""

    async def fake_resolve(record):
        return object()

    monkeypatch.setattr(service, "_resolve_engine", fake_resolve)
    monkeypatch.setattr(engines, "ping", lambda engine: None)

    async def broken_refresh(org_id, connection_id, refresh):
        raise RuntimeError("scope refresh exploded")

    monkeypatch.setattr(service, "list_available_scopes", broken_refresh)

    async def scenario():
        org = await seed_org("acme")
        conn = await service.create_connection(org, _data())
        result = await service.test_connection(org, conn["id"])
        stored = await fetch(
            "SELECT status, error_message FROM db_connections WHERE id = $1", conn["id"]
        )
        return result, stored[0]

    result, stored = run_db(scenario)
    assert result["status"] == "ok"
    assert stored["status"] == "ok" and stored["error_message"] is None
