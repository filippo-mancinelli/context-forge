import asyncio

import pytest
from fastapi import HTTPException

from src.api import deps


def _org(org_id=1, role="member"):
    return deps.ActiveOrg(org_id=org_id, role=role, namespace="org-ns", name="Org")


def _patch_project(monkeypatch, access_role):
    async def fake_get_project(project_id):
        return {"id": project_id, "org_id": 1, "name": "Webshop", "slug": "webshop",
                "memory_namespace": "acme--webshop"}

    async def fake_default(org_id):
        return 3

    async def fake_resolve_project_access(org_id, user_id, project_id):
        return access_role

    import src.projects as projects_mod
    monkeypatch.setattr(projects_mod, "get_project", fake_get_project)
    monkeypatch.setattr(projects_mod, "get_default_project_id", fake_default)
    monkeypatch.setattr(projects_mod, "resolve_project_access", fake_resolve_project_access)


def test_project_member_gets_project_role(monkeypatch):
    # Ruolo org member, ma sul progetto è admin: vince il ruolo di progetto.
    _patch_project(monkeypatch, access_role="admin")
    project = asyncio.run(deps.get_active_project(org=_org(), user_id=30, x_project_id=7))
    assert project.project_id == 7
    assert project.role == "admin"
    assert project.namespace == "acme--webshop"


def test_org_member_without_project_membership_is_403(monkeypatch):
    _patch_project(monkeypatch, access_role=None)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(deps.get_active_project(org=_org(), user_id=30, x_project_id=7))
    assert exc.value.status_code == 403


def test_default_project_fallback_still_checks_access(monkeypatch):
    _patch_project(monkeypatch, access_role=None)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(deps.get_active_project(org=_org(), user_id=30, x_project_id=None))
    assert exc.value.status_code == 403


def test_org_admin_passes_with_org_role(monkeypatch):
    _patch_project(monkeypatch, access_role="admin")
    project = asyncio.run(deps.get_active_project(
        org=_org(role="admin"), user_id=20, x_project_id=7))
    assert project.role == "admin"


def test_header_project_of_other_org_is_404(monkeypatch):
    async def fake_get_project(project_id):
        return {"id": 7, "org_id": 2, "name": "Other", "slug": "other",
                "memory_namespace": "other--default"}

    import src.projects as projects_mod
    monkeypatch.setattr(projects_mod, "get_project", fake_get_project)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(deps.get_active_project(org=_org(), user_id=30, x_project_id=7))
    assert exc.value.status_code == 404


def test_require_project_role_uses_effective_role(monkeypatch):
    # Sul progetto è solo viewer: le route member+ devono rifiutare.
    _patch_project(monkeypatch, access_role="viewer")
    checker = deps.require_project_role("member")

    async def run():
        project = await deps.get_active_project(org=_org(), user_id=30, x_project_id=7)
        return await checker(project=project)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(run())
    assert exc.value.status_code == 403
