import pytest

from src import config
from src.catalog import selections
from src.datasources.secrets import encrypt_secret
from src.ssh_sources import service
from tests.pgutil import fetchval, requires_pg, run_db, seed_org, seed_project

pytestmark = requires_pg


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    monkeypatch.setattr(config.get_settings(), "encryption_key", "test-key")


async def _machine(org_id, name="astercare@10.0.0.6"):
    return await fetchval(
        "INSERT INTO machines (org_id, name, host, username, auth_method, private_key_enc) "
        "VALUES ($1, $2, '10.0.0.6', 'astercare', 'key', $3) RETURNING id",
        org_id, name, encrypt_secret("PEM"),
    )


def _folder(machine_id, **over):
    data = {"name": "middleware-logs", "machine_id": machine_id, "root_path": "/srv/astercare/logs/",
            "include_globs": None, "exclude_globs": "*.pdf", "description": None, "restricted": False}
    data.update(over)
    return data


def test_create_normalizes_the_root_and_carries_the_machine(pg_database):
    async def scenario():
        org = await seed_org("acme")
        machine = await _machine(org)
        return await service.create_source(org, _folder(machine))

    folder = run_db(scenario)
    assert folder["root_path"] == "/srv/astercare/logs"
    assert folder["machine_name"] == "astercare@10.0.0.6"
    assert folder["has_secret"] is True
    assert "private_key_enc" not in folder


def test_machine_of_another_org_is_refused(pg_database):
    async def scenario():
        acme = await seed_org("acme")
        other = await seed_org("other")
        foreign = await _machine(other)
        with pytest.raises(ValueError, match="Machine not found"):
            await service.create_source(acme, _folder(foreign))

    run_db(scenario)


def test_project_sees_only_selected_folders(pg_database):
    async def scenario():
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        machine = await _machine(org)
        chosen = await service.create_source(org, _folder(machine, name="logs"))
        await service.create_source(org, _folder(machine, name="compose", root_path="/var/lib/compose"))
        await selections.select_resource(org, project, "folders", chosen["id"], None, False)
        catalog = await service.list_catalog(org)
        return chosen, catalog, await service.list_sources(org, project)

    chosen, catalog, listed = run_db(scenario)
    assert {f["name"]: f["project_count"] for f in catalog} == {"compose": 0, "logs": 1}
    assert [f["id"] for f in listed] == [chosen["id"]]


def test_resolve_distinguishes_not_selected_from_missing(pg_database):
    async def scenario():
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        machine = await _machine(org)
        chosen = await service.create_source(org, _folder(machine, name="Logs"))
        await service.create_source(org, _folder(machine, name="compose", root_path="/var/lib/compose"))
        await selections.select_resource(org, project, "folders", chosen["id"], None, False)
        by_name = await service.resolve_source(org, project, "logs", include_secret=True)
        by_id = await service.resolve_source(org, project, str(chosen["id"]))
        with pytest.raises(service.SSHSourceNotSelectedError, match="not available in this project"):
            await service.resolve_source(org, project, "compose")
        with pytest.raises(service.SSHSourceNotFoundError) as missing:
            await service.resolve_source(org, project, "nowhere")
        return by_name, by_id, missing.value

    by_name, by_id, missing = run_db(scenario)
    assert by_name["private_key_enc"]
    assert service.decrypted_conn(by_name)["private_key"] == "PEM"
    assert by_id["name"] == "Logs"
    assert not isinstance(missing, service.SSHSourceNotSelectedError)


def test_get_source_scopes_by_project_and_org(pg_database):
    async def scenario():
        org = await seed_org("acme")
        other_org = await seed_org("other")
        project = await seed_project(org, "alpha")
        other_project = await seed_project(org, "beta")
        machine = await _machine(org)
        chosen = await service.create_source(org, _folder(machine, name="logs"))
        not_selected = await service.create_source(
            org, _folder(machine, name="compose", root_path="/var/lib/compose")
        )
        await selections.select_resource(org, project, "folders", chosen["id"], None, False)

        foreign_machine = await _machine(other_org)
        foreign = await service.create_source(other_org, _folder(foreign_machine, name="logs"))

        selected = await service.get_source(org, project, chosen["id"])
        with_secret = await service.get_source(org, project, chosen["id"], include_secret=True)
        unselected = await service.get_source(org, other_project, not_selected["id"])
        wrong_org = await service.get_source(org, project, foreign["id"])
        return selected, with_secret, unselected, wrong_org

    selected, with_secret, unselected, wrong_org = run_db(scenario)
    assert selected["machine_name"] == "astercare@10.0.0.6"
    assert selected["host"] == "10.0.0.6"
    assert selected["has_secret"] is True
    assert "private_key_enc" not in selected
    assert with_secret["private_key_enc"]
    assert unselected is None
    assert wrong_org is None


def test_find_update_and_delete(pg_database):
    async def scenario():
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        machine = await _machine(org)
        created = await service.create_source(org, _folder(machine))
        found = await service.find_source(org, machine, "/srv/astercare/logs/")
        updated = await service.update_source(org, created["id"], _folder(machine, restricted=True))
        await selections.select_resource(org, project, "folders", created["id"], None, True)
        deleted = await service.delete_source(org, created["id"])
        remaining = await fetchval("SELECT count(*) FROM project_ssh_sources")
        return created, found, updated, deleted, remaining

    created, found, updated, deleted, remaining = run_db(scenario)
    assert found["id"] == created["id"]
    assert updated["restricted"] is True
    assert deleted is True and remaining == 0
