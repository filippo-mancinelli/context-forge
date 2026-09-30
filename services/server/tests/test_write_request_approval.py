"""Approvazione, esecuzione e scadenza delle write request (nessun DB reale)."""
import asyncio
import inspect
import json

import pytest

from src import scheduler, tenancy, write_requests
from tests.fake_db import FakeConn, FakePool


def _patch_pool(monkeypatch, conn):
    async def fake_pool():
        return FakePool(conn)

    monkeypatch.setattr(write_requests, "get_pool", fake_pool)


class _Stamp:
    def __init__(self, text):
        self.text = text

    def isoformat(self):
        return self.text


def _row(**over):
    row = {
        "id": 12, "org_id": 1, "project_id": 2, "kind": "db_execute",
        "status": "pending", "target": "erp",
        "payload": json.dumps({"sql": "UPDATE t SET a=1 WHERE id=1"}),
        "preview": json.dumps({"plan_rows": 3}), "reason": "fix a bad row",
        "requested_by_kind": "api_key", "requested_by_id": 5, "requested_by": "ci-bot",
        "decided_by": None, "decided_at": None, "decision_note": None,
        "result": None, "error": None,
        "created_at": _Stamp("2026-09-07T10:00:00+00:00"),
        "expires_at": _Stamp("2026-09-14T10:00:00+00:00"),
    }
    row.update(over)
    return row


def _patch_role(monkeypatch, role, perms=frozenset({"*"})):
    async def fake_role(org_id, user_id):
        return role

    async def fake_perms(org_id, r):
        return perms

    monkeypatch.setattr(tenancy, "get_membership_role", fake_role)
    monkeypatch.setattr(tenancy, "resolve_role_permissions", fake_perms)


def test_approve_executes_and_records_the_result(monkeypatch):
    _patch_role(monkeypatch, "owner")
    executed = {}

    async def fake_db_execute(record):
        executed.update(record["payload"])
        return {"connection": "erp", "row_count": 1, "duration_ms": 4}

    monkeypatch.setattr(write_requests, "_run_db_execute", fake_db_execute)
    conn = FakeConn(fetchrow_results=[
        _row(),                                              # get()
        _row(status="approved", decided_by=9),               # UPDATE -> approved
        _row(status="executed", result=json.dumps({"row_count": 1})),  # _finish
    ])
    _patch_pool(monkeypatch, conn)

    out = asyncio.run(write_requests.approve(12, user_id=9, note="looks fine"))

    assert out["status"] == "executed"
    assert out["result"] == {"row_count": 1}
    assert executed == {"sql": "UPDATE t SET a=1 WHERE id=1"}
    approve_sql, approve_args = conn.executed[1]
    assert "SET status='approved'" in approve_sql
    assert "AND status='pending'" in approve_sql
    assert "expires_at > NOW()" in approve_sql
    assert approve_args == (12, 9, "looks fine")



_PINNED = {"sql": "UPDATE t SET a=1 WHERE id=1", "scope_id": 31,
           "database": "app", "schema": "vendite"}


def _pinned_scope(**over):
    """Scope 31 as service.get_scope_by_id returns it at approval time."""
    scope = {"id": 4, "name": "erp", "engine": "postgresql", "database_name": "app",
             "scope_id": 31, "alias": "erp-vendite", "scope_database": "app",
             "scope_schema": "vendite", "scope_inferred": False}
    scope.update(over)
    return scope


def _approve_pinned(monkeypatch, scope, payload=_PINNED):
    """Approve a db_execute request on 'erp-vendite'; returns (outcome, run_write calls)."""
    from src.datasources import service

    _patch_role(monkeypatch, "owner")
    calls, loaded = [], []

    async def fake_scope_by_id(org_id, project_id, scope_id, include_secret=False):
        loaded.append((org_id, project_id, scope_id))
        if scope is None:
            raise service.ConnectionNotFoundError(f"Data source #{scope_id} is not in this project")
        return scope

    async def fake_run_write(org_id, project_id, ref, sql, source="mcp", *, by_scope_id=False,
                             expected_scope=None):
        calls.append((org_id, project_id, ref, sql, source, by_scope_id, expected_scope))
        return {"connection": "erp-vendite", "sql": sql, "row_count": 1, "duration_ms": 2}

    monkeypatch.setattr(service, "get_scope_by_id", fake_scope_by_id)
    monkeypatch.setattr(service, "run_write", fake_run_write)
    fields = {"target": "erp-vendite", "payload": json.dumps(payload)}
    conn = FakeConn(fetchrow_results=[
        _row(**fields),
        _row(status="approved", **fields),
        _row(status="finished", **fields),  # _finish: the status sent is read from its args
    ])
    _patch_pool(monkeypatch, conn)
    asyncio.run(write_requests.approve(12, user_id=9))
    _finish_sql, finish_args = conn.executed[2]
    if payload.get("scope_id") is not None:
        assert loaded == [(1, 2, payload["scope_id"])]
    return finish_args[1], finish_args[3], calls


def test_approve_runs_the_write_on_the_pinned_scope_id(monkeypatch):
    status, error, calls = _approve_pinned(monkeypatch, _pinned_scope())

    assert status == "executed" and error is None
    # The scope id is the reference: no alias or name lookup at approval.
    # run_write re-checks the record it loads against the pinned alias and scope.
    assert calls == [(1, 2, 31, "UPDATE t SET a=1 WHERE id=1", "approval", True,
                      ("erp-vendite", "app", "vendite"))]


def test_run_write_refuses_a_scope_that_changed_after_the_check(monkeypatch):
    """The pinned check passed, then the scope moved before run_write loaded it."""
    from src.datasources import service

    touched = []

    async def moved_scope(org_id, project_id, scope_id, include_secret=False):
        return _pinned_scope(scope_schema="acquisti")

    def no_validation(*args, **kwargs):
        touched.append("validate")
        raise AssertionError("must not validate a statement for a changed scope")

    async def no_engine(record):
        touched.append("engine")
        raise AssertionError("must not open the connection of a changed scope")

    monkeypatch.setattr(service, "get_scope_by_id", moved_scope)
    monkeypatch.setattr(service, "validate_write_query", no_validation)
    monkeypatch.setattr(service, "_resolve_engine", no_engine)
    monkeypatch.setattr(service, "_execute_write", lambda *a: touched.append("execute"))

    with pytest.raises(service.ScopeChangedError, match="propose the change again"):
        asyncio.run(service.run_write(
            1, 2, 31, "UPDATE t SET a=1 WHERE id=1", source="approval", by_scope_id=True,
            expected_scope=("erp-vendite", "app", "vendite"),
        ))
    assert touched == []


@pytest.mark.parametrize("scope, payload, text", [
    (_pinned_scope(), {"sql": "UPDATE t SET a=1 WHERE id=1"}, "before database scopes existed"),
    (None, _PINNED, "no longer in this project"),
    (_pinned_scope(alias="erp-archive"), _PINNED, "renamed to 'erp-archive'"),
    (_pinned_scope(scope_schema="acquisti"), _PINNED, "from 'app.vendite' to 'app.acquisti'"),
    (_pinned_scope(scope_database="archive"), _PINNED, "from 'app.vendite' to 'archive.vendite'"),
], ids=["no-scope-id", "scope-gone", "alias-changed", "schema-changed", "database-changed"])
def test_approve_refuses_when_the_pinned_scope_changed(monkeypatch, scope, payload, text):
    status, error, calls = _approve_pinned(monkeypatch, scope, payload)

    assert status == "failed"
    assert text in error and "propose the change again" in error
    assert calls == []


def test_approve_records_a_failed_execution(monkeypatch):
    _patch_role(monkeypatch, "owner")

    async def boom(record):
        raise RuntimeError("deadlock detected")

    monkeypatch.setattr(write_requests, "_run_db_execute", boom)
    conn = FakeConn(fetchrow_results=[
        _row(),
        _row(status="approved"),
        _row(status="failed", error="deadlock detected"),
    ])
    _patch_pool(monkeypatch, conn)

    out = asyncio.run(write_requests.approve(12, user_id=9))
    assert out["status"] == "failed"
    finish_sql, finish_args = conn.executed[2]
    assert "SET status=$2" in finish_sql
    assert finish_args[1] == "failed"
    assert finish_args[3] == "deadlock detected"


def test_approve_scrubs_credentials_from_the_stored_error(monkeypatch):
    _patch_role(monkeypatch, "owner")

    async def boom(record):
        raise RuntimeError("connect failed: postgresql://user:secret@host/db")

    monkeypatch.setattr(write_requests, "_run_db_execute", boom)
    conn = FakeConn(fetchrow_results=[
        _row(),
        _row(status="approved"),
        _row(status="failed", error="connect failed: postgresql://host/db"),
    ])
    _patch_pool(monkeypatch, conn)

    out = asyncio.run(write_requests.approve(12, user_id=9))
    assert out["status"] == "failed"
    finish_sql, finish_args = conn.executed[2]
    assert finish_args[3] == "connect failed: postgresql://host/db"
    assert "secret" not in finish_args[3]


def test_approve_requires_the_write_permission(monkeypatch):
    _patch_role(monkeypatch, "admin", perms=frozenset({"context-read", "jobs"}))
    _patch_pool(monkeypatch, FakeConn(fetchrow_results=[_row()]))
    with pytest.raises(write_requests.WriteRequestForbidden, match="db-write"):
        asyncio.run(write_requests.approve(12, user_id=9))


def test_approve_allows_an_admin_granted_the_permission(monkeypatch):
    _patch_role(monkeypatch, "admin", perms=frozenset({"db-write"}))

    async def fake_db_execute(record):
        return {"row_count": 1}

    monkeypatch.setattr(write_requests, "_run_db_execute", fake_db_execute)
    _patch_pool(monkeypatch, FakeConn(fetchrow_results=[
        _row(), _row(status="approved"), _row(status="executed"),
    ]))
    out = asyncio.run(write_requests.approve(12, user_id=9))
    assert out["status"] == "executed"


def test_approve_rejects_a_non_member(monkeypatch):
    _patch_role(monkeypatch, None)
    _patch_pool(monkeypatch, FakeConn(fetchrow_results=[_row()]))
    with pytest.raises(write_requests.WriteRequestForbidden):
        asyncio.run(write_requests.approve(12, user_id=9))


def test_approve_missing_request(monkeypatch):
    _patch_role(monkeypatch, "owner")
    _patch_pool(monkeypatch, FakeConn(fetchrow_results=[None]))
    with pytest.raises(write_requests.WriteRequestNotFound):
        asyncio.run(write_requests.approve(12, user_id=9))


def test_approve_refuses_a_non_pending_request(monkeypatch):
    _patch_role(monkeypatch, "owner")
    _patch_pool(monkeypatch, FakeConn(fetchrow_results=[_row(status="executed")]))
    with pytest.raises(write_requests.WriteRequestState, match="executed"):
        asyncio.run(write_requests.approve(12, user_id=9))


def test_approve_loses_the_race_when_the_guarded_update_matches_nothing(monkeypatch):
    """Doppia approvazione o richiesta scaduta: la UPDATE guardata non trova righe."""
    _patch_role(monkeypatch, "owner")

    async def no_exec(record):
        raise AssertionError("must not execute when the guarded update matched nothing")

    monkeypatch.setattr(write_requests, "_run_db_execute", no_exec)
    _patch_pool(monkeypatch, FakeConn(fetchrow_results=[_row(), None]))
    with pytest.raises(write_requests.WriteRequestState):
        asyncio.run(write_requests.approve(12, user_id=9))


def test_approve_dispatches_ssh_writes(monkeypatch):
    _patch_role(monkeypatch, "owner")
    seen = {}

    async def fake_ssh(record):
        seen.update(record["payload"])
        return {"path": "app.yml", "bytes_written": 11}

    monkeypatch.setattr(write_requests, "_run_ssh_write", fake_ssh)
    ssh_row = _row(
        kind="ssh_write_file", target="web1:app.yml",
        payload=json.dumps({"source": "web1", "path": "app.yml", "content": "key: value\n"}),
    )
    _patch_pool(monkeypatch, FakeConn(fetchrow_results=[
        ssh_row, dict(ssh_row, status="approved"), dict(ssh_row, status="executed"),
    ]))
    out = asyncio.run(write_requests.approve(12, user_id=9))
    assert out["status"] == "executed"
    assert seen["path"] == "app.yml"


_SSH_PINNED = {"source": "web1", "ssh_source_id": 3, "machine_id": 5, "root_path": "/etc/app",
               "path": "app.yml", "content": "key: value\n"}


def _folder(**over):
    """Folder 3 as ssh_sources.service.get_source returns it at approval time."""
    folder = {"id": 3, "org_id": 1, "name": "web1", "machine_id": 5, "root_path": "/etc/app",
              "host": "h", "port": 22, "username": "u", "auth_method": "password",
              "password_enc": "", "private_key_enc": ""}
    folder.update(over)
    return folder


def _approve_ssh(monkeypatch, folder, payload=_SSH_PINNED):
    """Approve an ssh_write_file request; returns (status, error, writes, by-id loads)."""
    from src.ssh_sources import client
    from src.ssh_sources import service as ssh_service

    _patch_role(monkeypatch, "owner")
    writes, loaded = [], []

    async def fake_get_source(org_id, project_id, source_id, include_secret=False):
        loaded.append((org_id, project_id, source_id))
        return folder

    async def no_name_lookup(*args, **kwargs):
        raise AssertionError("approval must not resolve the folder by name")

    def fake_write(conn_params, path, content):
        writes.append((conn_params["root_path"], path, content))
        return {"path": path, "bytes_written": len(content)}

    monkeypatch.setattr(ssh_service, "get_source", fake_get_source)
    monkeypatch.setattr(ssh_service, "resolve_source", no_name_lookup)
    monkeypatch.setattr(ssh_service, "decrypted_conn", lambda r: {"root_path": r["root_path"]})
    monkeypatch.setattr(client, "write_file", fake_write)
    fields = {"kind": "ssh_write_file", "target": "web1:app.yml", "payload": json.dumps(payload)}
    conn = FakeConn(fetchrow_results=[
        _row(**fields), _row(status="approved", **fields), _row(status="finished", **fields),
    ])
    _patch_pool(monkeypatch, conn)
    asyncio.run(write_requests.approve(12, user_id=9))
    _finish_sql, finish_args = conn.executed[2]
    return finish_args[1], finish_args[3], writes, loaded


def test_approve_writes_on_the_pinned_folder_by_id(monkeypatch):
    status, error, writes, loaded = _approve_ssh(monkeypatch, _folder())

    assert status == "executed" and error is None
    assert loaded == [(1, 2, 3)]
    assert writes == [("/etc/app", "app.yml", "key: value\n")]


_LEGACY_SSH = {"source": "web1", "path": "app.yml", "content": "key: value\n"}


@pytest.mark.parametrize("folder, payload, text", [
    (_folder(), _LEGACY_SSH, "proposed before approvals were pinned"),
    (None, _SSH_PINNED, "no longer in this project"),
    (_folder(name="web2"), _SSH_PINNED, "renamed to 'web2'"),
    (_folder(machine_id=6), _SSH_PINNED, "on machine #6"),
    (_folder(root_path="/"), _SSH_PINNED, "to '/' on machine #5"),
], ids=["no-folder-id", "folder-gone-or-deselected", "renamed", "other-machine", "other-root"])
def test_approve_refuses_when_the_pinned_folder_changed(monkeypatch, folder, payload, text):
    status, error, writes, loaded = _approve_ssh(monkeypatch, folder, payload)

    assert status == "failed"
    assert text in error and "propose the change again" in error
    assert writes == []
    if payload is _LEGACY_SSH:
        assert loaded == []  # no fallback to the name


def test_reject_marks_the_request_rejected(monkeypatch):
    _patch_role(monkeypatch, "owner")
    conn = FakeConn(fetchrow_results=[_row(), _row(status="rejected", decision_note="too risky")])
    _patch_pool(monkeypatch, conn)

    out = asyncio.run(write_requests.reject(12, user_id=9, note="too risky"))
    assert out["status"] == "rejected"
    sql, args = conn.executed[1]
    assert "SET status='rejected'" in sql
    assert args == (12, 9, "too risky")


def test_reject_refuses_a_decided_request(monkeypatch):
    _patch_role(monkeypatch, "owner")
    _patch_pool(monkeypatch, FakeConn(fetchrow_results=[_row(status="rejected")]))
    with pytest.raises(write_requests.WriteRequestState):
        asyncio.run(write_requests.reject(12, user_id=9))


def test_scheduler_registers_the_expiry_job():
    source = inspect.getsource(scheduler)
    assert "expire_pending" in source
    assert "write_requests_expiry" in source
