import asyncpg
import pytest

from src import config
from src.catalog import machines
from tests.pgutil import execute, fetchval, requires_pg, run_db, seed_org

pytestmark = requires_pg


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    monkeypatch.setattr(config.get_settings(), "encryption_key", "test-key")


def _data(**over):
    base = {"name": "astercare@10.0.0.6", "host": "10.0.0.6", "port": 22,
            "username": "astercare", "auth_method": "key", "private_key": "PEM"}
    base.update(over)
    return base


def test_create_hides_the_secret_and_flags_it(pg_database):
    async def scenario():
        org = await seed_org("acme")
        created = await machines.create_machine(org, _data())
        stored = await machines.get_machine(org, created["id"], include_secret=True)
        return created, stored

    created, stored = run_db(scenario)
    assert created["has_secret"] is True
    assert "private_key_enc" not in created and "password_enc" not in created
    assert machines.ssh_config(stored)["private_key"] == "PEM"


def test_names_are_unique_per_org_ignoring_case(pg_database):
    async def scenario():
        org = await seed_org("acme")
        await machines.create_machine(org, _data())
        with pytest.raises(asyncpg.UniqueViolationError):
            await machines.create_machine(org, _data(name="ASTERCARE@10.0.0.6", host="10.0.0.7"))

    run_db(scenario)


def test_find_by_address_and_by_name(pg_database):
    async def scenario():
        org = await seed_org("acme")
        created = await machines.create_machine(org, _data())
        return (
            created["id"],
            (await machines.find_machine(org, "10.0.0.6", 22, "astercare"))["id"],
            (await machines.get_machine_by_name(org, "AsterCare@10.0.0.6"))["id"],
            await machines.find_machine(org, "10.0.0.6", 2222, "astercare"),
        )

    machine_id, by_address, by_name, other_port = run_db(scenario)
    assert by_address == by_name == machine_id
    assert other_port is None


def test_update_keeps_the_secret_when_left_empty(pg_database):
    async def scenario():
        org = await seed_org("acme")
        created = await machines.create_machine(org, _data())
        updated = await machines.update_machine(org, created["id"], _data(name="chat-host", private_key=None))
        stored = await machines.get_machine(org, created["id"], include_secret=True)
        return updated, stored

    updated, stored = run_db(scenario)
    assert updated["name"] == "chat-host"
    assert machines.ssh_config(stored)["private_key"] == "PEM"


def test_delete_is_refused_while_a_folder_uses_the_machine(pg_database):
    async def scenario():
        org = await seed_org("acme")
        machine = await machines.create_machine(org, _data())
        folder = await fetchval(
            "INSERT INTO ssh_sources (org_id, name, machine_id, root_path) VALUES ($1, 'logs', $2, '/srv') RETURNING id",
            org, machine["id"],
        )
        with pytest.raises(machines.MachineInUseError) as refused:
            await machines.delete_machine(org, machine["id"])
        listed = await machines.list_machines(org)
        await execute("DELETE FROM ssh_sources WHERE id = $1", folder)
        deleted = await machines.delete_machine(org, machine["id"])
        missing = await machines.delete_machine(org, machine["id"])
        return refused.value, listed, deleted, missing

    refused, listed, deleted, missing = run_db(scenario)
    assert (refused.folders, refused.databases) == (1, 0)
    assert (listed[0]["folder_count"], listed[0]["database_count"]) == (1, 0)
    assert deleted is True and missing is False


def test_mark_pending_secret(pg_database):
    async def scenario():
        org = await seed_org("acme")
        created = await machines.create_machine(org, _data(private_key=None))
        await machines.mark_pending_secret(org, created["id"])
        return await machines.get_machine(org, created["id"])

    machine = run_db(scenario)
    assert machine["status"] == "pending_secret"
    assert machine["has_secret"] is False
