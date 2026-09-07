import asyncio

import pytest
from fastapi import HTTPException

from src.api.routes import organizations
from src.api.routes.organizations import UpdateMemberRequest, UpdateOrgRequest


def _patch_roles(monkeypatch, roles_by_user):
    """roles_by_user: dict user_id -> ruolo nell'org 1."""
    async def fake_get_membership_role(org_id, user_id):
        return roles_by_user.get(user_id)

    async def fake_count_owners(org_id):
        return sum(1 for r in roles_by_user.values() if r == "owner")

    calls = {"update_member_role": [], "remove_member": [], "update_organization": [],
             "set_org_role_permissions": [], "clear_org_role_permissions": []}

    async def fake_update_member_role(org_id, user_id, role):
        calls["update_member_role"].append((org_id, user_id, role))
        return True

    async def fake_remove_member(org_id, user_id):
        calls["remove_member"].append((org_id, user_id))
        return True

    async def fake_update_organization(org_id, name):
        calls["update_organization"].append((org_id, name))
        return {"id": org_id, "name": name, "slug": "org", "memory_namespace": "ns",
                "created_at": None}

    async def fake_set_org_role_permissions(org_id, matrix):
        calls["set_org_role_permissions"].append((org_id, matrix))

    async def fake_clear_org_role_permissions(org_id):
        calls["clear_org_role_permissions"].append(org_id)

    async def fake_get_org_role_permissions(org_id):
        return {}

    from src import tenancy
    monkeypatch.setattr(tenancy, "get_membership_role", fake_get_membership_role)
    monkeypatch.setattr(tenancy, "count_owners", fake_count_owners)
    monkeypatch.setattr(tenancy, "update_member_role", fake_update_member_role)
    monkeypatch.setattr(tenancy, "remove_member", fake_remove_member)
    monkeypatch.setattr(tenancy, "update_organization", fake_update_organization)
    monkeypatch.setattr(tenancy, "set_org_role_permissions", fake_set_org_role_permissions)
    monkeypatch.setattr(tenancy, "clear_org_role_permissions", fake_clear_org_role_permissions)
    monkeypatch.setattr(tenancy, "get_org_role_permissions", fake_get_org_role_permissions)
    return calls


ROLES = {10: "owner", 20: "admin", 30: "member"}


def test_admin_cannot_change_member_role(monkeypatch):
    _patch_roles(monkeypatch, ROLES)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(organizations.update_member(
            org_id=1, member_id=30, req=UpdateMemberRequest(role="admin"), user_id=20))
    assert exc.value.status_code == 403


def test_owner_can_change_member_role(monkeypatch):
    calls = _patch_roles(monkeypatch, ROLES)
    out = asyncio.run(organizations.update_member(
        org_id=1, member_id=30, req=UpdateMemberRequest(role="admin"), user_id=10))
    assert out == {"status": "ok"}
    assert calls["update_member_role"] == [(1, 30, "admin")]


def test_admin_cannot_remove_other_member(monkeypatch):
    _patch_roles(monkeypatch, ROLES)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(organizations.remove_member(org_id=1, member_id=30, user_id=20))
    assert exc.value.status_code == 403


def test_member_can_still_leave(monkeypatch):
    calls = _patch_roles(monkeypatch, ROLES)
    out = asyncio.run(organizations.remove_member(org_id=1, member_id=30, user_id=30))
    assert out == {"status": "ok"}
    assert calls["remove_member"] == [(1, 30)]


def test_admin_cannot_rename_org(monkeypatch):
    _patch_roles(monkeypatch, ROLES)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(organizations.update_org(
            org_id=1, req=UpdateOrgRequest(name="Nuovo"), user_id=20))
    assert exc.value.status_code == 403


def test_admin_cannot_read_or_write_mcp_matrix(monkeypatch):
    _patch_roles(monkeypatch, ROLES)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(organizations.get_mcp_permissions(org_id=1, user_id=20))
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        asyncio.run(organizations.reset_mcp_permissions(org_id=1, user_id=20))
    assert exc.value.status_code == 403


def test_owner_can_read_mcp_matrix(monkeypatch):
    _patch_roles(monkeypatch, ROLES)
    out = asyncio.run(organizations.get_mcp_permissions(org_id=1, user_id=10))
    assert "roles" in out
