import asyncio

import pytest
from fastapi import HTTPException

from src.api.deps import ActiveOrg
from src.api.routes import projects as projects_routes
from src.api.routes.projects import ProjectMemberRequest


def _org(role):
    return ActiveOrg(org_id=1, role=role, namespace="ns", name="Org")


def _patch(monkeypatch):
    added = []

    async def fake_get_project(project_id):
        return {"id": project_id, "org_id": 1, "name": "P", "slug": "p",
                "memory_namespace": "org--p"}

    async def fake_list_project_members(project_id):
        return [{"user_id": 2, "role": "member", "created_at": None,
                 "username": "beppe", "email": "b@x.it"}]

    async def fake_add_project_member(project_id, user_id, role):
        added.append((project_id, user_id, role))

    async def fake_remove_project_member(project_id, user_id):
        return True

    async def fake_get_membership_role(org_id, user_id):
        return "member"

    from src import projects as projects_mod
    monkeypatch.setattr(projects_mod, "get_project", fake_get_project)
    monkeypatch.setattr(projects_mod, "list_project_members", fake_list_project_members)
    monkeypatch.setattr(projects_mod, "add_project_member", fake_add_project_member)
    monkeypatch.setattr(projects_mod, "remove_project_member", fake_remove_project_member)
    monkeypatch.setattr(projects_routes, "get_membership_role", fake_get_membership_role)
    return added


def test_admin_lists_members_without_roles(monkeypatch):
    _patch(monkeypatch)
    out = asyncio.run(projects_routes.list_project_members_route(
        project_id=7, org=_org("admin")))
    assert all("role" not in m for m in out["members"])


def test_owner_lists_members_with_roles(monkeypatch):
    _patch(monkeypatch)
    out = asyncio.run(projects_routes.list_project_members_route(
        project_id=7, org=_org("owner")))
    assert all(m["role"] == "member" for m in out["members"])


def test_admin_add_is_forced_to_member(monkeypatch):
    added = _patch(monkeypatch)
    asyncio.run(projects_routes.add_project_member_route(
        project_id=7, req=ProjectMemberRequest(user_id=2, role="admin"),
        org=_org("admin")))
    assert added == [(7, 2, "member")]


def test_owner_add_keeps_requested_role(monkeypatch):
    added = _patch(monkeypatch)
    asyncio.run(projects_routes.add_project_member_route(
        project_id=7, req=ProjectMemberRequest(user_id=2, role="admin"),
        org=_org("owner")))
    assert added == [(7, 2, "admin")]


def test_admin_cannot_remove_project_member(monkeypatch):
    _patch(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(projects_routes.remove_project_member_route(
            project_id=7, member_user_id=2, org=_org("admin")))
    assert exc.value.status_code == 403
