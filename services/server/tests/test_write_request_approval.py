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
