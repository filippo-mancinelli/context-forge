import pytest

from src.catalog import selections
from tests.pgutil import execute, fetchval, requires_pg, run_db, seed_org, seed_project

pytestmark = requires_pg


async def _folder(org_id, name="logs", restricted=False):
    machine = await fetchval(
        "INSERT INTO machines (org_id, name, host, username) VALUES ($1, $2, '10.0.0.6', $2) RETURNING id",
        org_id, f"m-{name}",
    )
    return await fetchval(
        "INSERT INTO ssh_sources (org_id, name, machine_id, root_path, restricted) "
        "VALUES ($1, $2, $3, '/srv', $4) RETURNING id",
        org_id, name, machine, restricted,
    )


def test_select_then_list_and_select_again(pg_database):
    async def scenario():
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        folder = await _folder(org)
        first = await selections.select_resource(org, project, "folders", folder, None, False)
        second = await selections.select_resource(org, project, "folders", folder, None, False)
        return folder, first, second, await selections.selected_ids(project, "folders")

    folder, first, second, ids = run_db(scenario)
    assert first == {"kind": "folders", "resource_id": folder, "name": "logs", "already_selected": False}
    assert second["already_selected"] is True
    assert ids == {folder}


def test_select_tolerates_a_row_inserted_concurrently(pg_database):
    async def scenario():
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        folder = await _folder(org)
        # Simula il vincitore di una selezione concorrente: la riga di link
        # esiste già quando select_resource controlla se è presente.
        await execute(
            "INSERT INTO project_ssh_sources (project_id, ssh_source_id) VALUES ($1, $2)",
            project, folder,
        )
        result = await selections.select_resource(org, project, "folders", folder, None, False)
        count = await fetchval(
            "SELECT count(*) FROM project_ssh_sources WHERE project_id = $1 AND ssh_source_id = $2",
            project, folder,
        )
        return result, count

    result, count = run_db(scenario)
    assert result["already_selected"] is True
    assert count == 1


def test_restricted_resource_needs_the_right(pg_database):
    async def scenario():
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        folder = await _folder(org, restricted=True)
        with pytest.raises(selections.RestrictedResourceError):
            await selections.select_resource(org, project, "folders", folder, None, False)
        return await selections.select_resource(org, project, "folders", folder, None, True)

    assert run_db(scenario)["already_selected"] is False


def test_resources_and_projects_of_other_orgs_are_not_found(pg_database):
    async def scenario():
        acme = await seed_org("acme")
        other = await seed_org("other")
        acme_project = await seed_project(acme, "alpha")
        other_project = await seed_project(other, "beta")
        foreign_folder = await _folder(other)
        acme_folder = await _folder(acme, "mine")
        with pytest.raises(selections.ResourceNotFoundError):
            await selections.select_resource(acme, acme_project, "folders", foreign_folder, None, True)
        with pytest.raises(selections.ResourceNotFoundError):
            await selections.select_resource(acme, other_project, "folders", acme_folder, None, True)

    run_db(scenario)


def test_deselect_reports_whether_a_selection_was_removed(pg_database):
    async def scenario():
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        folder = await _folder(org)
        await selections.select_resource(org, project, "folders", folder, None, False)
        return (
            await selections.deselect_resource(org, project, "folders", folder),
            await selections.deselect_resource(org, project, "folders", folder),
        )

    assert run_db(scenario) == (True, False)


def test_projects_using_and_find_by_name(pg_database):
    async def scenario():
        org = await seed_org("acme")
        alpha = await seed_project(org, "alpha")
        beta = await seed_project(org, "beta")
        folder = await _folder(org, "Logs")
        await selections.select_resource(org, alpha, "folders", folder, None, False)
        await selections.select_resource(org, beta, "folders", folder, None, False)
        found = await selections.find_resource_by_name(org, "folders", "logs")
        using = await selections.projects_using(org, "folders", folder)
        return folder, found, using

    folder, found, using = run_db(scenario)
    assert found == {"id": folder, "name": "Logs", "restricted": False}
    assert [p["slug"] for p in using] == ["alpha", "beta"]


def test_deleting_a_project_drops_its_selections_but_not_the_resource(pg_database):
    async def scenario():
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        folder = await _folder(org)
        await selections.select_resource(org, project, "folders", folder, None, False)
        await execute("DELETE FROM projects WHERE id = $1", project)
        return (
            await fetchval("SELECT count(*) FROM project_ssh_sources"),
            await fetchval("SELECT count(*) FROM ssh_sources WHERE id = $1", folder),
        )

    assert run_db(scenario) == (0, 1)
