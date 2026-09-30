"""An approved ssh_write_file writes only on the folder it was proposed and previewed on.

Real catalog tables; the SFTP client is replaced by a recorder.
"""
import pytest

from src import config, write_previews, write_requests
from src.catalog import selections
from src.datasources.secrets import encrypt_secret
from src.mcp import approvals as mcp_approvals
from src.mcp import ssh_files as mcp_ssh
from src.mcp.context import set_current_org_id, set_current_project_id
from src.mcp.permissions import set_current_permissions
from src.ssh_sources import client
from src.ssh_sources import service
from tests.pgutil import fetchval, requires_pg, run_db, seed_org, seed_project, seed_user

pytestmark = requires_pg

CONTENT = "port: 8080\n"


@pytest.fixture(autouse=True)
def _environment(monkeypatch):
    monkeypatch.setattr(config.get_settings(), "encryption_key", "test-key")

    def fake_preview(conn_params, path, content):
        return {"path": path, "content_bytes": len(content), "content_sha256": "abc",
                "existing_sha256": None, "existing_size": None, "is_new": True}

    monkeypatch.setattr(write_previews, "file_preview", fake_preview)
    monkeypatch.setattr(mcp_approvals, "get_current_principal", lambda: None, raising=False)
    yield
    set_current_org_id(None)
    set_current_project_id(None)
    set_current_permissions(None)


@pytest.fixture
def written(monkeypatch):
    calls = []

    def recorder(conn_params, path, content):
        calls.append((conn_params["host"], conn_params["root_path"], path, content))
        return {"path": path, "bytes_written": len(content)}

    monkeypatch.setattr(client, "write_file", recorder)
    return calls


async def _machine(org_id, name, host):
    return await fetchval(
        "INSERT INTO machines (org_id, name, host, username, auth_method, private_key_enc) "
        "VALUES ($1, $2, $3, 'deploy', 'key', $4) RETURNING id",
        org_id, name, host, encrypt_secret("PEM"),
    )


async def _folder(org, project, machine_id, name, root):
    folder = await service.create_source(org, {
        "name": name, "machine_id": machine_id, "root_path": root,
        "include_globs": None, "exclude_globs": None, "description": None, "restricted": False,
    })
    await selections.select_resource(org, project, "folders", folder["id"], None, True)
    return folder


async def _setup():
    org = await seed_org("acme")
    project = await seed_project(org, "alpha")
    owner = await seed_user("boss", org, role="owner")
    web = await _machine(org, "deploy@web", "10.0.0.1")
    other = await _machine(org, "deploy@other", "10.0.0.2")
    set_current_org_id(org)
    set_current_project_id(project)
    set_current_permissions(frozenset({"context-write"}))
    return org, project, owner, web, other


async def _propose(source: str) -> int:
    tool = getattr(mcp_ssh.ssh_write_file, "fn", mcp_ssh.ssh_write_file)
    proposed = await tool(source=source, path="app.yml", content=CONTENT)
    assert proposed["status"] == "pending_approval", proposed
    return proposed["request_id"]


async def _update(org, folder_id, **changes):
    current = await service.get_catalog_source(org, folder_id)
    await service.update_source(org, folder_id, {**current, **changes})


def test_the_proposal_pins_the_previewed_folder(pg_database, written):
    async def scenario():
        org, project, owner, web, _other = await _setup()
        folder = await _folder(org, project, web, "web1", "/etc/app")
        request_id = await _propose("web1")
        stored = await write_requests.get(request_id)
        return folder, stored, await write_requests.approve(request_id, owner)

    folder, stored, outcome = run_db(scenario)
    assert stored["payload"] == {"source": "web1", "ssh_source_id": folder["id"],
                                 "machine_id": folder["machine_id"], "root_path": "/etc/app",
                                 "path": "app.yml", "content": CONTENT}
    assert outcome["status"] == "executed"
    assert written == [("10.0.0.1", "/etc/app", "app.yml", CONTENT)]


@pytest.mark.parametrize("change, text", [
    ("rename", "renamed to 'web1-old'"),
    ("root", "to '/' on machine"),
    ("machine", "after the proposal"),
    ("recreate", "no longer in this project"),
    ("deselect", "no longer in this project"),
])
def test_a_changed_folder_fails_the_approval_and_writes_nothing(pg_database, written, change, text):
    async def scenario():
        org, project, owner, web, other = await _setup()
        folder = await _folder(org, project, web, "web1", "/etc/app")
        request_id = await _propose("web1")
        if change == "rename":
            await _update(org, folder["id"], name="web1-old")
        elif change == "root":
            await _update(org, folder["id"], root_path="/")
        elif change == "machine":
            await _update(org, folder["id"], machine_id=other)
        elif change == "recreate":
            # Same name, same place, but not the folder the approver saw.
            await service.delete_source(org, folder["id"])
            await _folder(org, project, web, "web1", "/etc/app")
        else:
            await selections.deselect_resource(org, project, "folders", folder["id"])
        outcome = await write_requests.approve(request_id, owner)
        status = await fetchval("SELECT status FROM write_requests WHERE id = $1", request_id)
        return outcome, status

    outcome, status = run_db(scenario)
    assert outcome["status"] == status == "failed"
    assert text in outcome["error"] and "propose the change again" in outcome["error"]
    assert written == []


def test_a_numeric_folder_name_never_resolves_to_another_folder(pg_database, written):
    """Folder A is named with folder B's id: the approval still writes on A."""
    async def scenario():
        org, project, owner, web, other = await _setup()
        b = await _folder(org, project, other, "billing", "/srv/billing")
        a = await _folder(org, project, web, str(b["id"]), "/etc/app")
        request_id = await _propose(str(a["id"]))
        stored = await write_requests.get(request_id)
        return a, b, stored, await write_requests.approve(request_id, owner)

    a, b, stored, outcome = run_db(scenario)
    assert stored["payload"]["source"] == str(b["id"])
    assert stored["payload"]["ssh_source_id"] == a["id"]
    assert outcome["status"] == "executed"
    assert written == [("10.0.0.1", "/etc/app", "app.yml", CONTENT)]


def test_a_request_without_a_pinned_folder_is_refused(pg_database, written):
    async def scenario():
        org, project, owner, web, _other = await _setup()
        await _folder(org, project, web, "web1", "/etc/app")
        request_id = await _propose("web1")
        # A request stored before pinning: name, path and content only.
        await fetchval(
            "UPDATE write_requests SET payload = payload - 'ssh_source_id' - 'machine_id' - 'root_path' "
            "WHERE id = $1 RETURNING id",
            request_id,
        )
        return await write_requests.approve(request_id, owner)

    outcome = run_db(scenario)
    assert outcome["status"] == "failed"
    assert "proposed before approvals were pinned" in outcome["error"]
    assert written == []
