"""Route REST della coda di approvazione: gating per ruolo e mappatura errori."""
import asyncio

import pytest
from fastapi import HTTPException

from src import write_requests as service
from src.api import deps
from src.api.routes import write_requests as routes


def _org(role="admin"):
    return deps.ActiveOrg(org_id=1, role=role, namespace="org-ns", name="Org")


def _record(**over):
    record = {
        "id": 12, "org_id": 1, "project_id": 2, "kind": "db_execute",
        "status": "pending", "target": "erp",
        "payload": {"sql": "UPDATE t SET a=1 WHERE id=1"},
        "preview": {"plan_rows": 3}, "reason": "fix a bad row",
        "requested_by_kind": "api_key", "requested_by_id": 5, "requested_by": "ci-bot",
        "decided_by": None, "decided_at": None, "decision_note": None,
        "result": None, "error": None,
        "created_at": "2026-09-07T10:00:00+00:00",
        "expires_at": "2026-09-14T10:00:00+00:00",
    }
    record.update(over)
    return record


def test_list_passes_the_filters_through(monkeypatch):
    seen = {}

    async def fake_list(org_id, status=None, limit=50, offset=0):
        seen.update(org_id=org_id, status=status, limit=limit, offset=offset)
        return {"requests": [_record()], "total": 1, "pending": 1}

    monkeypatch.setattr(routes.service, "list_for_org", fake_list)
    out = asyncio.run(routes.list_requests(status="pending", limit=25, offset=10, org=_org()))
    assert seen == {"org_id": 1, "status": "pending", "limit": 25, "offset": 10}
    assert out["pending"] == 1
    assert out["requests"][0]["id"] == 12


def test_list_rejects_an_unknown_status():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.list_requests(status="bogus", limit=50, offset=0, org=_org()))
    assert exc.value.status_code == 400


def test_list_clamps_the_limit_to_the_maximum(monkeypatch):
    seen = {}

    async def fake_list(org_id, status=None, limit=50, offset=0):
        seen["limit"] = limit
        return {"requests": [], "total": 0, "pending": 0}

    monkeypatch.setattr(routes.service, "list_for_org", fake_list)
    asyncio.run(routes.list_requests(status=None, limit=9999, offset=0, org=_org()))
    assert seen["limit"] == service.MAX_LIST_LIMIT


def test_get_returns_the_request(monkeypatch):
    async def fake_get(request_id, org_id=None, project_id=None):
        assert (request_id, org_id) == (12, 1)
        return _record()

    monkeypatch.setattr(routes.service, "get", fake_get)
    out = asyncio.run(routes.get_request(request_id=12, org=_org()))
    assert out["request"]["target"] == "erp"


def test_get_of_another_org_is_404(monkeypatch):
    async def fake_get(request_id, org_id=None, project_id=None):
        return None

    monkeypatch.setattr(routes.service, "get", fake_get)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.get_request(request_id=12, org=_org()))
    assert exc.value.status_code == 404


def test_approve_returns_the_executed_request(monkeypatch):
    async def fake_get(request_id, org_id=None, project_id=None):
        return _record()

    async def fake_approve(request_id, user_id, note=""):
        assert (request_id, user_id, note) == (12, 7, "ok by me")
        return _record(status="executed", result={"row_count": 1})

    monkeypatch.setattr(routes.service, "get", fake_get)
    monkeypatch.setattr(routes.service, "approve", fake_approve)
    out = asyncio.run(routes.approve_request(
        request_id=12, req=routes.DecisionRequest(note="ok by me"), org=_org(), user_id=7))
    assert out["status"] == "ok"
    assert out["request"]["status"] == "executed"


def test_approve_without_the_permission_is_403(monkeypatch):
    async def fake_get(request_id, org_id=None, project_id=None):
        return _record()

    async def forbidden(request_id, user_id, note=""):
        raise service.WriteRequestForbidden("requires the 'db-write' permission")

    monkeypatch.setattr(routes.service, "get", fake_get)
    monkeypatch.setattr(routes.service, "approve", forbidden)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.approve_request(
            request_id=12, req=routes.DecisionRequest(), org=_org(), user_id=7))
    assert exc.value.status_code == 403


def test_approve_of_a_decided_request_is_409(monkeypatch):
    async def fake_get(request_id, org_id=None, project_id=None):
        return _record(status="executed")

    async def bad_state(request_id, user_id, note=""):
        raise service.WriteRequestState("not pending")

    monkeypatch.setattr(routes.service, "get", fake_get)
    monkeypatch.setattr(routes.service, "approve", bad_state)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.approve_request(
            request_id=12, req=routes.DecisionRequest(), org=_org(), user_id=7))
    assert exc.value.status_code == 409


def test_approve_of_another_org_is_404(monkeypatch):
    async def fake_get(request_id, org_id=None, project_id=None):
        return None

    monkeypatch.setattr(routes.service, "get", fake_get)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.approve_request(
            request_id=12, req=routes.DecisionRequest(), org=_org(), user_id=7))
    assert exc.value.status_code == 404


def test_reject_returns_the_rejected_request(monkeypatch):
    async def fake_get(request_id, org_id=None, project_id=None):
        return _record()

    async def fake_reject(request_id, user_id, note=""):
        return _record(status="rejected", decision_note=note)

    monkeypatch.setattr(routes.service, "get", fake_get)
    monkeypatch.setattr(routes.service, "reject", fake_reject)
    out = asyncio.run(routes.reject_request(
        request_id=12, req=routes.DecisionRequest(note="too risky"), org=_org(), user_id=7))
    assert out["request"]["status"] == "rejected"
    assert out["request"]["decision_note"] == "too risky"


def test_reject_without_the_permission_is_403(monkeypatch):
    async def fake_get(request_id, org_id=None, project_id=None):
        return _record()

    async def forbidden(request_id, user_id, note=""):
        raise service.WriteRequestForbidden("requires the 'db-write' permission")

    monkeypatch.setattr(routes.service, "get", fake_get)
    monkeypatch.setattr(routes.service, "reject", forbidden)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.reject_request(
            request_id=12, req=routes.DecisionRequest(), org=_org(), user_id=7))
    assert exc.value.status_code == 403


def test_reject_of_another_org_is_404(monkeypatch):
    async def fake_get(request_id, org_id=None, project_id=None):
        return None

    monkeypatch.setattr(routes.service, "get", fake_get)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.reject_request(
            request_id=12, req=routes.DecisionRequest(), org=_org(), user_id=7))
    assert exc.value.status_code == 404


def test_member_is_denied_by_the_role_dependency():
    """require_role('admin') è il gate: un member non passa."""
    checker = deps.require_role("admin")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(checker(org=_org(role="member")))
    assert exc.value.status_code == 403


def test_router_is_mounted_on_the_api():
    from src.api.app import api

    paths = {r.path for r in api.routes}
    assert "/api/write-requests" in paths
    assert "/api/write-requests/{request_id}/approve" in paths
