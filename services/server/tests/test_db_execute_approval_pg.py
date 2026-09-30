"""An approved db_execute runs only on the scope it was proposed and previewed on.

Real metadata tables; the external database is replaced by a run_write recorder.
"""
import pytest

from src import config, write_previews, write_requests
from src.datasources import project_scopes, service
from src.mcp import approvals as mcp_approvals
from src.mcp import datasources as mcp_datasources
from src.mcp.context import set_current_org_id, set_current_project_id
from src.mcp.permissions import set_current_permissions
from tests.pgutil import fetchval, requires_pg, run_db, seed_org, seed_project, seed_user

pytestmark = requires_pg

SQL = "UPDATE t SET a = 1 WHERE id = 1"


@pytest.fixture(autouse=True)
def _environment(monkeypatch):
    monkeypatch.setattr(config.get_settings(), "encryption_key", "test-key")

    async def fake_preview(org_id, project_id, connection, sql):
        return {"statement": sql, "plan_rows": 1, "plan_text": "{}"}

    monkeypatch.setattr(write_previews, "sql_preview", fake_preview)
    monkeypatch.setattr(mcp_approvals, "get_current_principal", lambda: None, raising=False)
    yield
    set_current_org_id(None)
    set_current_project_id(None)
    set_current_permissions(None)


@pytest.fixture
def executed(monkeypatch):
    calls = []

    async def recorder(org_id, project_id, ref, sql, source="mcp", *, by_scope_id=False,
                       expected_scope=None):
        calls.append((ref, sql, source, by_scope_id))
        return {"connection": str(ref), "sql": sql, "row_count": 1, "duration_ms": 1}

    monkeypatch.setattr(service, "run_write", recorder)
    return calls


async def _propose_on_two_scopes():
    """Connection 'erp' linked on app.vendite (alias 'erp') and app.acquisti;
    a context-write caller proposes SQL on 'erp'."""
    org = await seed_org("acme")
    project = await seed_project(org, "alpha")
    owner = await seed_user("boss", org, role="owner")
    conn = await service.create_connection(org, {
        "name": "erp", "engine": "postgresql", "host": "127.0.0.1", "port": 5432,
        "database_name": "app", "username": "writer", "password": "secret", "options": {},
        "description": None, "ssh_machine_id": None, "restricted": False,
    })
    erp = await project_scopes.create_scope(org, project, conn["id"], "app", "vendite", None, owner, True)
    other = await project_scopes.create_scope(
        org, project, conn["id"], "app", "acquisti", "erp/app.acquisti", owner, True
    )
    set_current_org_id(org)
    set_current_project_id(project)
    set_current_permissions(frozenset({"context-write"}))
    proposed = await getattr(mcp_datasources.db_execute, "fn", mcp_datasources.db_execute)(
        connection="erp", sql=SQL
    )
    return org, project, owner, erp, other, proposed["request_id"]


def test_the_proposal_pins_the_previewed_scope(pg_database, executed):
    async def scenario():
        org, project, owner, erp, _other, request_id = await _propose_on_two_scopes()
        stored = await write_requests.get(request_id)
        outcome = await write_requests.approve(request_id, owner)
        return erp, stored, outcome

    erp, stored, outcome = run_db(scenario)
    assert erp["alias"] == "erp"
    assert stored["target"] == "erp"
    assert stored["payload"] == {"sql": SQL, "scope_id": erp["scope_id"],
                                 "database": "app", "schema": "vendite"}
    assert outcome["status"] == "executed"
    assert executed == [(erp["scope_id"], SQL, "approval", True)]


@pytest.mark.parametrize("change, text", [
    ("rename", "renamed to 'erp-old'"),
    ("delete", "no longer in this project"),
    ("repoint", "from 'app.vendite' to 'app.acquisti'"),
])
def test_a_changed_scope_fails_the_approval_and_runs_nothing(pg_database, executed, change, text):
    async def scenario():
        org, project, owner, erp, other, request_id = await _propose_on_two_scopes()
        if change == "rename":
            await project_scopes.update_scope(org, project, erp["scope_id"], "app", "vendite", "erp-old")
        elif change == "delete":
            # The other scope still exists on the same connection, whose name is 'erp'.
            await project_scopes.delete_scope(org, project, erp["scope_id"])
        else:
            # Free schema acquisti, then point 'erp' at it keeping the alias.
            await project_scopes.delete_scope(org, project, other["scope_id"])
            await project_scopes.update_scope(org, project, erp["scope_id"], "app", "acquisti", "erp")
        outcome = await write_requests.approve(request_id, owner)
        status = await fetchval("SELECT status FROM write_requests WHERE id = $1", request_id)
        return outcome, status

    outcome, status = run_db(scenario)
    assert outcome["status"] == status == "failed"
    assert text in outcome["error"] and "propose the change again" in outcome["error"]
    assert executed == []


def test_a_request_without_a_pinned_scope_is_refused(pg_database, executed):
    async def scenario():
        org, project, owner, _erp, _other, request_id = await _propose_on_two_scopes()
        # A request stored before scopes: only the statement in the payload.
        await fetchval(
            "UPDATE write_requests SET payload = jsonb_build_object('sql', payload->>'sql') "
            "WHERE id = $1 RETURNING id",
            request_id,
        )
        return await write_requests.approve(request_id, owner)

    outcome = run_db(scenario)
    assert outcome["status"] == "failed"
    assert "before database scopes existed" in outcome["error"]
    assert executed == []


def test_run_write_by_scope_id_never_falls_back_to_a_name(pg_database):
    """by_scope_id reads only the scope id: a missing scope is an error even
    when a connection with that id or name exists in the project."""
    async def scenario():
        org, project, _owner, erp, other, _request_id = await _propose_on_two_scopes()
        await project_scopes.delete_scope(org, project, erp["scope_id"])
        with pytest.raises(service.ConnectionNotFoundError):
            await service.get_scope_by_id(org, project, erp["scope_id"])
        with pytest.raises(service.ConnectionNotFoundError):
            await service.run_write(org, project, erp["scope_id"], SQL, source="approval",
                                    by_scope_id=True)
        # Another project never sees the scope.
        beta = await seed_project(org, "beta")
        with pytest.raises(service.ConnectionNotFoundError):
            await service.get_scope_by_id(org, beta, other["scope_id"])

    run_db(scenario)


def test_scope_by_id_reads_the_current_scope(pg_database):
    async def scenario():
        org, project, _owner, _erp, other, _request_id = await _propose_on_two_scopes()
        scope = await service.get_scope_by_id(org, project, other["scope_id"])
        return other, scope, service.effective_scope(scope)

    other, scope, effective = run_db(scenario)
    assert scope["alias"] == "erp/app.acquisti" and scope["scope_id"] == other["scope_id"]
    assert effective == ("app", "acquisti")

