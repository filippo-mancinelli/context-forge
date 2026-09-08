# services/server/tests/test_mcp_keys_route.py
import pytest
from fastapi import HTTPException

from src.api.routes.mcp_keys import (
    CreateKeyRequest,
    _enforce_creator_cap,
    _resolve_permissions_csv,
)


def test_explicit_permissions_win():
    req = CreateKeyRequest(name="k", permissions=["db-query", "jobs"])
    assert _resolve_permissions_csv(req) == "db-query,jobs"


def test_star_is_exclusive():
    with pytest.raises(HTTPException) as exc:
        _resolve_permissions_csv(CreateKeyRequest(name="k", permissions=["*", "jobs"]))
    assert exc.value.status_code == 422


def test_star_alone_is_valid():
    assert _resolve_permissions_csv(CreateKeyRequest(name="k", permissions=["*"])) == "*"


def test_unknown_permission_rejected():
    with pytest.raises(HTTPException) as exc:
        _resolve_permissions_csv(CreateKeyRequest(name="k", permissions=["bogus"]))
    assert exc.value.status_code == 422


def test_empty_permissions_rejected():
    with pytest.raises(HTTPException) as exc:
        _resolve_permissions_csv(CreateKeyRequest(name="k", permissions=[]))
    assert exc.value.status_code == 422


def test_legacy_scope_alias():
    req = CreateKeyRequest(name="k", scope="admin")
    assert _resolve_permissions_csv(req) == "*"


def test_default_when_nothing_given():
    req = CreateKeyRequest(name="k")
    assert _resolve_permissions_csv(req) == "context-read,context-write"


# ===== I1: key permissions capped by the creator's own effective role permissions =====

def test_creator_cap_member_requesting_star_rejected():
    allowed = frozenset({"context-read", "context-write", "db-query"})
    with pytest.raises(HTTPException) as exc:
        _enforce_creator_cap("*", allowed)
    assert exc.value.status_code == 403


def test_creator_cap_member_requesting_out_of_scope_permission_rejected():
    allowed = frozenset({"context-read", "context-write", "db-query"})
    with pytest.raises(HTTPException) as exc:
        _enforce_creator_cap("repo-write,jobs", allowed)
    assert exc.value.status_code == 403


def test_creator_cap_member_requesting_subset_allowed():
    allowed = frozenset({"context-read", "context-write", "db-query"})
    _enforce_creator_cap("context-read,db-query", allowed)  # no raise


def test_creator_cap_admin_requesting_star_allowed():
    allowed = frozenset({"*"})
    _enforce_creator_cap("*", allowed)  # no raise


def test_creator_cap_admin_requesting_anything_allowed():
    allowed = frozenset({"*"})
    _enforce_creator_cap("repo-write,jobs", allowed)  # no raise


from datetime import datetime, timezone

from src.api.routes.mcp_keys import _serialize_key


def test_serialize_key_passes_through_project_fields():
    ts = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
    row = {
        "id": 1, "name": "k", "scope": "read,write",
        "permissions": "context-read", "created_at": ts,
        "last_used_at": None, "expires_at": None,
        "created_by": 3, "org_id": 2,
        "project_id": 9, "project_slug": "billing",
    }
    out = _serialize_key(row)
    assert out["project_id"] == 9
    assert out["project_slug"] == "billing"
    assert out["created_at"] == "2026-07-22T12:00:00+00:00"
    assert out["last_used_at"] is None


# ===== rate_limit_per_minute: create_key response returns it (consistency with list/update) =====

def test_create_key_response_includes_the_rate_limit(monkeypatch):
    import asyncio

    from src.api.deps import ActiveOrg
    from src.api.routes import mcp_keys as routes

    async def fake_resolve_role_permissions(org_id, role):
        return frozenset({"*"})

    captured = {}

    async def fake_create_mcp_api_key(**kwargs):
        captured.update(kwargs)
        return "forge_rawkey"

    async def fake_list_mcp_api_keys(org_id):
        return [{
            "id": 1, "name": "k", "scope": "read,write",
            "permissions": "context-read,context-write", "expires_at": None,
            "rate_limit_per_minute": 60,
        }]

    monkeypatch.setattr(routes, "resolve_role_permissions", fake_resolve_role_permissions)
    monkeypatch.setattr(routes, "create_mcp_api_key", fake_create_mcp_api_key)
    monkeypatch.setattr(routes, "list_mcp_api_keys", fake_list_mcp_api_keys)

    req = routes.CreateKeyRequest(name="k", rate_limit_per_minute=60, all_projects=True)
    org = ActiveOrg(org_id=1, role="admin", namespace="ns", name="Org")
    out = asyncio.run(routes.create_key(req, user_id=7, org=org))

    assert out.rate_limit_per_minute == 60
    assert captured["rate_limit_per_minute"] == 60


# ===== PUT /api/mcp/keys/{key_id}: creator-or-admin gating, null clears the limit =====

def test_update_key_request_rejects_a_zero_rate_limit():
    from pydantic import ValidationError

    from src.api.routes.mcp_keys import UpdateKeyRequest

    with pytest.raises(ValidationError):
        UpdateKeyRequest(rate_limit_per_minute=0)


def _wire_update(monkeypatch, key, update_result=True):
    import asyncio as _asyncio

    from src.api.routes import mcp_keys as routes

    async def fake_get_mcp_api_key(key_id):
        return key

    calls = []

    async def fake_update_mcp_api_key_rate_limit(key_id, org_id, rate_limit_per_minute):
        calls.append((key_id, org_id, rate_limit_per_minute))
        return update_result

    monkeypatch.setattr(routes, "get_mcp_api_key", fake_get_mcp_api_key)
    monkeypatch.setattr(routes, "update_mcp_api_key_rate_limit", fake_update_mcp_api_key_rate_limit)
    return routes, calls, _asyncio


def test_update_key_404_when_key_not_found(monkeypatch):
    from src.api.deps import ActiveOrg

    routes, _calls, aio = _wire_update(monkeypatch, key=None)
    org = ActiveOrg(org_id=1, role="admin", namespace="ns", name="Org")
    req = routes.UpdateKeyRequest(rate_limit_per_minute=10)
    with pytest.raises(HTTPException) as exc:
        aio.run(routes.update_key(key_id=5, req=req, user_id=7, org=org))
    assert exc.value.status_code == 404


def test_update_key_404_when_key_belongs_to_another_org(monkeypatch):
    from src.api.deps import ActiveOrg

    routes, _calls, aio = _wire_update(
        monkeypatch, key={"id": 5, "org_id": 2, "created_by": 7}
    )
    org = ActiveOrg(org_id=1, role="admin", namespace="ns", name="Org")
    req = routes.UpdateKeyRequest(rate_limit_per_minute=10)
    with pytest.raises(HTTPException) as exc:
        aio.run(routes.update_key(key_id=5, req=req, user_id=7, org=org))
    assert exc.value.status_code == 404


def test_update_key_403_for_non_creator_non_admin(monkeypatch):
    from src.api.deps import ActiveOrg

    routes, _calls, aio = _wire_update(
        monkeypatch, key={"id": 5, "org_id": 1, "created_by": 99}
    )
    org = ActiveOrg(org_id=1, role="member", namespace="ns", name="Org")
    req = routes.UpdateKeyRequest(rate_limit_per_minute=10)
    with pytest.raises(HTTPException) as exc:
        aio.run(routes.update_key(key_id=5, req=req, user_id=7, org=org))
    assert exc.value.status_code == 403


def test_update_key_allows_the_creator(monkeypatch):
    from src.api.deps import ActiveOrg

    routes, calls, aio = _wire_update(
        monkeypatch, key={"id": 5, "org_id": 1, "created_by": 7}
    )
    org = ActiveOrg(org_id=1, role="member", namespace="ns", name="Org")
    req = routes.UpdateKeyRequest(rate_limit_per_minute=10)
    out = aio.run(routes.update_key(key_id=5, req=req, user_id=7, org=org))
    assert out == {"status": "ok", "rate_limit_per_minute": 10}
    assert calls == [(5, 1, 10)]


def test_update_key_allows_an_org_admin(monkeypatch):
    from src.api.deps import ActiveOrg

    routes, calls, aio = _wire_update(
        monkeypatch, key={"id": 5, "org_id": 1, "created_by": 99}
    )
    org = ActiveOrg(org_id=1, role="admin", namespace="ns", name="Org")
    req = routes.UpdateKeyRequest(rate_limit_per_minute=None)
    out = aio.run(routes.update_key(key_id=5, req=req, user_id=7, org=org))
    assert out == {"status": "ok", "rate_limit_per_minute": None}
    assert calls == [(5, 1, None)]


def test_update_key_404_when_write_matches_no_row(monkeypatch):
    from src.api.deps import ActiveOrg

    routes, _calls, aio = _wire_update(
        monkeypatch, key={"id": 5, "org_id": 1, "created_by": 7}, update_result=False
    )
    org = ActiveOrg(org_id=1, role="member", namespace="ns", name="Org")
    req = routes.UpdateKeyRequest(rate_limit_per_minute=10)
    with pytest.raises(HTTPException) as exc:
        aio.run(routes.update_key(key_id=5, req=req, user_id=7, org=org))
    assert exc.value.status_code == 404
