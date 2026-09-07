import asyncio

import pytest

from src.api.routes import organizations


MEMBERS = [
    {"user_id": 1, "role": "owner", "created_at": None, "username": "anna", "email": "a@x.it"},
    {"user_id": 2, "role": "member", "created_at": None, "username": "beppe", "email": "b@x.it"},
]

def _patch(monkeypatch, caller_role):
    async def fake_get_membership_role(org_id, user_id):
        return caller_role

    async def fake_list_members(org_id):
        return [dict(m) for m in MEMBERS]

    from src import tenancy
    monkeypatch.setattr(tenancy, "get_membership_role", fake_get_membership_role)
    monkeypatch.setattr(tenancy, "list_members", fake_list_members)


def test_owner_sees_member_roles(monkeypatch):
    _patch(monkeypatch, "owner")
    out = asyncio.run(organizations.list_members(org_id=1, user_id=1))
    assert all("role" in m for m in out["members"])


@pytest.mark.parametrize("caller_role", ["admin", "member", "viewer"])
def test_non_owner_does_not_see_member_roles(monkeypatch, caller_role):
    _patch(monkeypatch, caller_role)
    out = asyncio.run(organizations.list_members(org_id=1, user_id=1))
    assert out["members"], "la lista resta visibile"
    assert all("role" not in m for m in out["members"])
    assert all("email" in m and "username" in m for m in out["members"])
