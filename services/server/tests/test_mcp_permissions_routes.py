# services/server/tests/test_mcp_permissions_routes.py
import asyncio

import pytest
from fastapi import HTTPException

from src.api.routes import organizations as org_routes


def test_validate_matrix_requires_all_roles():
    with pytest.raises(HTTPException) as exc:
        org_routes._validate_matrix({"viewer": ["context-read"]})
    assert exc.value.status_code == 422


def test_validate_matrix_rejects_unknown_permission():
    matrix = {
        "viewer": ["bogus"],
        "member": [],
        "admin": ["*"],
        "owner": ["*"],
    }
    with pytest.raises(HTTPException) as exc:
        org_routes._validate_matrix(matrix)
    assert exc.value.status_code == 422


def test_validate_matrix_star_is_exclusive():
    matrix = {
        "viewer": ["*", "context-read"],
        "member": [],
        "admin": ["*"],
        "owner": ["*"],
    }
    with pytest.raises(HTTPException) as exc:
        org_routes._validate_matrix(matrix)
    assert exc.value.status_code == 422


def test_validate_matrix_dedupes_and_accepts_valid():
    matrix = {
        "viewer": ["context-read", "context-read"],
        "member": ["context-read", "db-query"],
        "admin": ["*"],
        "owner": ["*"],
    }
    clean = org_routes._validate_matrix(matrix)
    assert clean["viewer"] == ["context-read"]


def test_get_mcp_permissions_defaults_when_no_customization(monkeypatch):
    async def fake_require(org_id, user_id, minimum):
        assert minimum == "owner"
        return "owner"

    async def fake_get(org_id):
        return {}

    monkeypatch.setattr(org_routes, "_require_role_in", fake_require)
    monkeypatch.setattr(org_routes.tenancy, "get_org_role_permissions", fake_get, raising=False)

    out = asyncio.run(org_routes.get_mcp_permissions(org_id=1, user_id=7))
    assert out["roles"]["viewer"] == {"permissions": ["context-read"], "customized": False}
    assert out["roles"]["member"] == {
        "permissions": ["context-read", "context-write", "db-query", "ssh-read"],
        "customized": False,
    }
    assert set(out["roles"]["admin"]["permissions"]) == {
        "context-read",
        "context-write",
        "db-query",
        "jobs",
        "ssh-read",
        "projects-write",
        "sources-write",
    }
    assert out["roles"]["admin"]["customized"] is False
    assert out["roles"]["owner"]["permissions"] == ["*"]
    assert out["roles"]["owner"]["customized"] is False


def test_get_mcp_permissions_partial_storage_shows_empty_customized_role(monkeypatch):
    """Once the org has ANY stored rows, a role absent from storage must show up as an
    explicitly empty, customized role — not silently fall back to the hardcoded default
    (see C2)."""
    async def fake_require(org_id, user_id, minimum):
        assert minimum == "owner"
        return "owner"

    async def fake_get(org_id):
        return {"viewer": ["jobs"]}

    monkeypatch.setattr(org_routes, "_require_role_in", fake_require)
    monkeypatch.setattr(org_routes.tenancy, "get_org_role_permissions", fake_get, raising=False)

    out = asyncio.run(org_routes.get_mcp_permissions(org_id=1, user_id=7))
    assert out["roles"]["viewer"] == {"permissions": ["jobs"], "customized": True}
    # member has no stored rows, but the org IS customized -> empty, not the default.
    assert out["roles"]["member"] == {"permissions": [], "customized": True}
    assert out["roles"]["admin"] == {"permissions": [], "customized": True}
    assert out["roles"]["owner"] == {"permissions": [], "customized": True}


def test_put_mcp_permissions_saves_clean_matrix(monkeypatch):
    saved = {}

    async def fake_require(org_id, user_id, minimum):
        assert minimum == "owner"
        return "owner"

    async def fake_set(org_id, matrix):
        saved[org_id] = matrix

    monkeypatch.setattr(org_routes, "_require_role_in", fake_require)
    monkeypatch.setattr(org_routes.tenancy, "set_org_role_permissions", fake_set, raising=False)

    req = org_routes.McpPermissionsRequest(
        roles={
            "viewer": ["context-read"],
            "member": ["context-read", "db-query"],
            "admin": ["*"],
            "owner": ["*"],
        }
    )
    out = asyncio.run(org_routes.put_mcp_permissions(org_id=3, req=req, user_id=7))
    assert out == {"status": "ok"}
    assert saved[3]["member"] == ["context-read", "db-query"]


def test_put_mcp_permissions_rejects_all_empty_matrix(monkeypatch):
    """See C2: a matrix that leaves every role with zero permissions must be rejected
    (422), not silently accepted and fall back to defaults."""
    async def fake_require(org_id, user_id, minimum):
        assert minimum == "owner"
        return "owner"

    monkeypatch.setattr(org_routes, "_require_role_in", fake_require)
    monkeypatch.setattr(
        org_routes.tenancy, "set_org_role_permissions",
        lambda org_id, matrix: pytest.fail("must not persist an all-empty matrix"),
        raising=False,
    )

    req = org_routes.McpPermissionsRequest(
        roles={"viewer": [], "member": [], "admin": [], "owner": []}
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(org_routes.put_mcp_permissions(org_id=3, req=req, user_id=7))
    assert exc.value.status_code == 422


def test_reset_mcp_permissions(monkeypatch):
    cleared = []

    async def fake_require(org_id, user_id, minimum):
        assert minimum == "owner"
        return "owner"

    async def fake_clear(org_id):
        cleared.append(org_id)

    monkeypatch.setattr(org_routes, "_require_role_in", fake_require)
    monkeypatch.setattr(
        org_routes.tenancy, "clear_org_role_permissions", fake_clear, raising=False
    )

    out = asyncio.run(org_routes.reset_mcp_permissions(org_id=3, user_id=7))
    assert out == {"status": "ok"}
    assert cleared == [3]


# ===== I2: the admin gate itself, exercised with the REAL _require_role_in =====

def test_get_mcp_permissions_403_for_non_admin_member(monkeypatch):
    async def fake_role(org_id, user_id):
        return "member"

    monkeypatch.setattr(org_routes.tenancy, "get_membership_role", fake_role, raising=False)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(org_routes.get_mcp_permissions(org_id=1, user_id=7))
    assert exc.value.status_code == 403


def test_get_mcp_permissions_404_for_non_member(monkeypatch):
    async def fake_role(org_id, user_id):
        return None

    monkeypatch.setattr(org_routes.tenancy, "get_membership_role", fake_role, raising=False)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(org_routes.get_mcp_permissions(org_id=1, user_id=7))
    assert exc.value.status_code == 404


def test_put_mcp_permissions_403_for_non_admin_member(monkeypatch):
    async def fake_role(org_id, user_id):
        return "member"

    monkeypatch.setattr(org_routes.tenancy, "get_membership_role", fake_role, raising=False)
    req = org_routes.McpPermissionsRequest(
        roles={
            "viewer": ["context-read"],
            "member": ["context-read"],
            "admin": ["*"],
            "owner": ["*"],
        }
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(org_routes.put_mcp_permissions(org_id=1, req=req, user_id=7))
    assert exc.value.status_code == 403


def test_put_mcp_permissions_404_for_non_member(monkeypatch):
    async def fake_role(org_id, user_id):
        return None

    monkeypatch.setattr(org_routes.tenancy, "get_membership_role", fake_role, raising=False)
    req = org_routes.McpPermissionsRequest(
        roles={
            "viewer": ["context-read"],
            "member": ["context-read"],
            "admin": ["*"],
            "owner": ["*"],
        }
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(org_routes.put_mcp_permissions(org_id=1, req=req, user_id=7))
    assert exc.value.status_code == 404


def test_reset_mcp_permissions_403_for_non_admin_member(monkeypatch):
    async def fake_role(org_id, user_id):
        return "member"

    monkeypatch.setattr(org_routes.tenancy, "get_membership_role", fake_role, raising=False)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(org_routes.reset_mcp_permissions(org_id=1, user_id=7))
    assert exc.value.status_code == 403


def test_reset_mcp_permissions_404_for_non_member(monkeypatch):
    async def fake_role(org_id, user_id):
        return None

    monkeypatch.setattr(org_routes.tenancy, "get_membership_role", fake_role, raising=False)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(org_routes.reset_mcp_permissions(org_id=1, user_id=7))
    assert exc.value.status_code == 404
