"""REST dell'audit: gating di ruolo, filtri passati alla SQL, forma delle stats."""
import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from src.api.routes import tool_calls as routes
from tests.fake_db import FakeConn, FakePool


def _wire(monkeypatch, conn, role="admin"):
    async def fake_role(org_id, user_id):
        return role

    async def fake_pool():
        return FakePool(conn)

    monkeypatch.setattr(routes.tenancy, "get_membership_role", fake_role, raising=False)
    monkeypatch.setattr(routes, "get_pool", fake_pool)


def test_member_gets_403(monkeypatch):
    _wire(monkeypatch, FakeConn(), role="member")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.list_tool_calls(org_id=1, user_id=7))
    assert exc.value.status_code == 403


def test_non_member_gets_404(monkeypatch):
    _wire(monkeypatch, FakeConn(), role=None)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.list_tool_calls(org_id=1, user_id=7))
    assert exc.value.status_code == 404


def test_build_filters_only_org():
    where, params = routes._build_filters(3, None, None, None, None, None)
    assert where == "WHERE org_id = $1"
    assert params == [3]


def test_build_filters_all_criteria():
    since = datetime(2026, 9, 1, tzinfo=timezone.utc)
    where, params = routes._build_filters(3, "repo_search", "denied", "ci-bot", 9, since)
    assert where == (
        "WHERE org_id = $1 AND tool = $2 AND outcome = $3 "
        "AND principal = $4 AND project_id = $5 AND created_at >= $6"
    )
    assert params == [3, "repo_search", "denied", "ci-bot", 9, since]


def test_list_serializes_rows_and_total(monkeypatch):
    created = datetime(2026, 9, 7, 10, 0, tzinfo=timezone.utc)
    row = {
        "id": 1, "org_id": 3, "project_id": 9, "principal_kind": "api_key",
        "principal_id": 4, "principal": "ci-bot", "tool": "repo_search",
        "permission": "context-read", "outcome": "ok", "duration_ms": 12,
        "error": None, "args_summary": '{"query": "abc"}', "created_at": created,
    }
    conn = FakeConn(fetch_rows=[row], fetchval_results=[1])
    _wire(monkeypatch, conn)

    out = asyncio.run(routes.list_tool_calls(org_id=3, user_id=7, limit=10, offset=0))

    assert out["total"] == 1
    call = out["calls"][0]
    assert call["created_at"] == "2026-09-07T10:00:00+00:00"
    assert call["args_summary"] == {"query": "abc"}
    sql, args = conn.executed[0]
    assert "ORDER BY created_at DESC" in sql
    assert args[-2:] == (10, 0)


def test_limit_is_capped_at_200(monkeypatch):
    conn = FakeConn(fetch_rows=[], fetchval_results=[0])
    _wire(monkeypatch, conn)
    asyncio.run(routes.list_tool_calls(org_id=3, user_id=7, limit=999))
    _sql, args = conn.executed[0]
    assert args[-2] == 200


def test_filters_reach_the_query(monkeypatch):
    conn = FakeConn(fetch_rows=[], fetchval_results=[0])
    _wire(monkeypatch, conn)
    asyncio.run(
        routes.list_tool_calls(
            org_id=3, user_id=7, tool="repo_search", outcome="denied", principal="ci-bot"
        )
    )
    sql, args = conn.executed[0]
    assert "tool = $2" in sql and "outcome = $3" in sql and "principal = $4" in sql
    assert args[:4] == (3, "repo_search", "denied", "ci-bot")


def test_invalid_since_is_422(monkeypatch):
    _wire(monkeypatch, FakeConn())
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.list_tool_calls(org_id=3, user_id=7, since="not-a-date"))
    assert exc.value.status_code == 422


def test_stats_shape(monkeypatch):
    by_tool = [{"tool": "repo_search", "calls": 5, "errors": 1, "denied": 0,
                "rate_limited": 0, "p95_ms": 40}]
    by_principal = [{"principal": "ci-bot", "calls": 5, "errors": 1, "denied": 0,
                     "rate_limited": 0, "p95_ms": 40}]
    conn = FakeConn(
        fetch_rows=[by_tool, by_principal],
        fetchrow_results=[{"calls": 5, "ok": 4, "errors": 1, "denied": 0, "rate_limited": 0}],
    )
    _wire(monkeypatch, conn)

    out = asyncio.run(routes.tool_call_stats(org_id=3, user_id=7, window="7d"))

    assert out["by_tool"] == by_tool
    assert out["by_principal"] == by_principal
    assert out["totals"] == {"calls": 5, "ok": 4, "errors": 1, "denied": 0, "rate_limited": 0}
    assert out["total"] == 5
    _sql, args = conn.executed[0]
    assert args == (3, 168)


def test_stats_rejects_unknown_window(monkeypatch):
    _wire(monkeypatch, FakeConn())
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.tool_call_stats(org_id=3, user_id=7, window="1y"))
    assert exc.value.status_code == 422


def test_create_key_accepts_a_rate_limit():
    from src.api.routes.mcp_keys import CreateKeyRequest

    assert CreateKeyRequest(name="k", rate_limit_per_minute=60).rate_limit_per_minute == 60


def test_create_key_rejects_a_zero_rate_limit():
    from pydantic import ValidationError

    from src.api.routes.mcp_keys import CreateKeyRequest

    with pytest.raises(ValidationError):
        CreateKeyRequest(name="k", rate_limit_per_minute=0)


def test_serialize_key_passes_the_rate_limit_through():
    from src.api.routes.mcp_keys import _serialize_key

    out = _serialize_key({"id": 1, "name": "k", "rate_limit_per_minute": 30})
    assert out["rate_limit_per_minute"] == 30
