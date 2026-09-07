"""Stato e persistenza delle write request, con pool finto (nessun DB reale)."""
import asyncio
import json

from src import write_requests
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
        "preview": json.dumps({"plan_rows": 3}),
        "reason": "fix a bad row",
        "requested_by_kind": "api_key", "requested_by_id": 5, "requested_by": "ci-bot",
        "decided_by": None, "decided_at": None, "decision_note": None,
        "result": None, "error": None,
        "created_at": _Stamp("2026-09-07T10:00:00+00:00"),
        "expires_at": _Stamp("2026-09-14T10:00:00+00:00"),
    }
    row.update(over)
    return row


def test_statuses_and_kind_permissions():
    assert write_requests.STATUSES == (
        "pending", "approved", "rejected", "executed", "failed", "expired"
    )
    assert write_requests.KIND_PERMISSION == {
        "db_execute": "db-write", "ssh_write_file": "ssh-write"
    }


def test_create_inserts_and_returns_a_json_safe_record(monkeypatch):
    conn = FakeConn(fetchrow_results=[_row()])
    _patch_pool(monkeypatch, conn)

    record = asyncio.run(write_requests.create(
        org_id=1, project_id=2, kind="db_execute", target="erp",
        payload={"sql": "UPDATE t SET a=1 WHERE id=1"}, preview={"plan_rows": 3},
        reason="fix a bad row", requested_by_kind="api_key",
        requested_by_id=5, requested_by="ci-bot",
    ))

    sql, args = conn.executed[0]
    assert "INSERT INTO write_requests" in sql
    assert args[0] == 1 and args[1] == 2 and args[2] == "db_execute"
    # payload/preview arrivano serializzati, come per le altre colonne jsonb
    assert json.loads(args[4]) == {"sql": "UPDATE t SET a=1 WHERE id=1"}
    assert record["status"] == "pending"
    assert record["payload"] == {"sql": "UPDATE t SET a=1 WHERE id=1"}
    assert record["preview"] == {"plan_rows": 3}
    assert record["created_at"] == "2026-09-07T10:00:00+00:00"


def test_get_returns_none_for_another_org(monkeypatch):
    _patch_pool(monkeypatch, FakeConn(fetchrow_results=[_row(org_id=1, project_id=2)]))
    assert asyncio.run(write_requests.get(12, org_id=99)) is None


def test_get_returns_none_for_another_project(monkeypatch):
    _patch_pool(monkeypatch, FakeConn(fetchrow_results=[_row(org_id=1, project_id=2)]))
    assert asyncio.run(write_requests.get(12, org_id=1, project_id=77)) is None


def test_get_returns_the_record_in_scope(monkeypatch):
    _patch_pool(monkeypatch, FakeConn(fetchrow_results=[_row()]))
    record = asyncio.run(write_requests.get(12, org_id=1, project_id=2))
    assert record["id"] == 12
    assert record["payload"]["sql"] == "UPDATE t SET a=1 WHERE id=1"


def test_get_missing_row_is_none(monkeypatch):
    _patch_pool(monkeypatch, FakeConn(fetchrow_results=[None]))
    assert asyncio.run(write_requests.get(12)) is None


def test_list_for_org_filters_by_status_and_counts(monkeypatch):
    conn = FakeConn(fetch_rows=[_row(), _row(id=13)], fetchval_results=[4, 4])
    _patch_pool(monkeypatch, conn)

    out = asyncio.run(write_requests.list_for_org(1, status="pending", limit=50, offset=0))

    assert out["total"] == 4 and out["pending"] == 4
    assert [r["id"] for r in out["requests"]] == [12, 13]
    sql, args = conn.executed[0]
    assert "WHERE org_id=$1 AND status=$2" in sql
    assert args == (1, "pending", 50, 0)


def test_list_for_org_without_status_lists_everything(monkeypatch):
    conn = FakeConn(fetch_rows=[_row()], fetchval_results=[1, 1])
    _patch_pool(monkeypatch, conn)
    out = asyncio.run(write_requests.list_for_org(1))
    sql, args = conn.executed[0]
    assert "AND status=" not in sql
    assert args == (1, 50, 0)
    assert out["requests"][0]["id"] == 12


def test_list_for_org_caps_the_limit(monkeypatch):
    conn = FakeConn(fetch_rows=[], fetchval_results=[0, 0])
    _patch_pool(monkeypatch, conn)
    asyncio.run(write_requests.list_for_org(1, limit=5000, offset=-3))
    _sql, args = conn.executed[0]
    assert args == (1, 200, 0)


def test_expire_pending_parses_the_updated_count(monkeypatch):
    conn = FakeConn(execute_result="UPDATE 7")
    _patch_pool(monkeypatch, conn)
    assert asyncio.run(write_requests.expire_pending()) == 7
    sql, _args = conn.executed[0]
    assert "SET status='expired'" in sql
    assert "status='pending'" in sql
    assert "expires_at < NOW()" in sql


def test_expire_pending_unparsable_result_is_zero(monkeypatch):
    _patch_pool(monkeypatch, FakeConn(execute_result=""))
    assert asyncio.run(write_requests.expire_pending()) == 0
