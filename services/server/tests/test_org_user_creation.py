import asyncio

import pytest
from fastapi import HTTPException

from src.api.routes import organizations
from src.api.routes.organizations import CreateUserRequest, ProjectGrant


def _patch(monkeypatch, caller_role, existing_email_user=None):
    state = {"members": [], "project_members": [], "created": []}

    async def fake_get_membership_role(org_id, user_id):
        return caller_role

    async def fake_get_user_id_by_email(email):
        return existing_email_user

    async def fake_create_admin_user(username, password, email=None):
        state["created"].append((username, email))
        return 99

    async def fake_add_member(org_id, user_id, role):
        state["members"].append((org_id, user_id, role))

    async def fake_get_project(project_id):
        # Il progetto 7 appartiene all'org 1; il 42 a un'altra org.
        if project_id == 7:
            return {"id": 7, "org_id": 1}
        if project_id == 42:
            return {"id": 42, "org_id": 2}
        return None

    async def fake_add_project_member(project_id, user_id, role):
        state["project_members"].append((project_id, user_id, role))

    from src import projects, tenancy
    monkeypatch.setattr(tenancy, "get_membership_role", fake_get_membership_role)
    monkeypatch.setattr(tenancy, "add_member", fake_add_member)
    monkeypatch.setattr(projects, "get_project", fake_get_project)
    monkeypatch.setattr(projects, "add_project_member", fake_add_project_member)
    monkeypatch.setattr(organizations, "get_user_id_by_email", fake_get_user_id_by_email)
    monkeypatch.setattr(organizations, "create_admin_user", fake_create_admin_user)
    return state


def _req(**kwargs):
    base = dict(username="carla", password="secret-pass", email="c@x.it", role="member")
    base.update(kwargs)
    return CreateUserRequest(**base)


def test_owner_creates_user_with_requested_roles(monkeypatch):
    state = _patch(monkeypatch, "owner")
    out = asyncio.run(organizations.create_org_user(
        org_id=1,
        req=_req(role="admin", projects=[ProjectGrant(project_id=7, role="admin")]),
        user_id=10,
    ))
    assert state["created"] == [("carla", "c@x.it")]
    assert state["members"] == [(1, 99, "admin")]
    assert state["project_members"] == [(7, 99, "admin")]
    assert out["user"]["role"] == "admin"


def test_admin_roles_are_forced_to_member(monkeypatch):
    state = _patch(monkeypatch, "admin")
    asyncio.run(organizations.create_org_user(
        org_id=1,
        req=_req(role="admin", projects=[ProjectGrant(project_id=7, role="admin")]),
        user_id=20,
    ))
    assert state["members"] == [(1, 99, "member")]
    assert state["project_members"] == [(7, 99, "member")]


def test_owner_role_is_never_grantable(monkeypatch):
    _patch(monkeypatch, "owner")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(organizations.create_org_user(org_id=1, req=_req(role="owner"), user_id=10))
    assert exc.value.status_code == 422


def test_duplicate_email_is_rejected(monkeypatch):
    state = _patch(monkeypatch, "owner", existing_email_user=5)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(organizations.create_org_user(org_id=1, req=_req(), user_id=10))
    assert exc.value.status_code == 409
    assert state["created"] == []


def test_project_of_another_org_is_rejected_before_creation(monkeypatch):
    state = _patch(monkeypatch, "owner")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(organizations.create_org_user(
            org_id=1,
            req=_req(projects=[ProjectGrant(project_id=42)]),
            user_id=10,
        ))
    assert exc.value.status_code == 404
    assert state["created"] == []
    assert state["members"] == []


def test_member_caller_is_forbidden(monkeypatch):
    _patch(monkeypatch, "member")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(organizations.create_org_user(org_id=1, req=_req(), user_id=30))
    assert exc.value.status_code == 403
