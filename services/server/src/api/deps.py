"""Request-scoped auth & tenancy dependencies for the REST API.

These resolve the authenticated user from the bearer session token and the
"active" organization from the optional ``X-Org-Id`` header, enforcing
membership and role-based access control.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Header, HTTPException

from .. import tenancy
from .security import resolve_session_user


@dataclass
class ActiveOrg:
    org_id: int
    role: str
    namespace: str
    name: str


async def get_current_user_id(authorization: Optional[str] = Header(default=None)) -> int:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    user_id = await resolve_session_user(token) if token else None
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user_id


async def get_active_org(
    user_id: int = Depends(get_current_user_id),
    x_org_id: Optional[int] = Header(default=None, alias="X-Org-Id"),
) -> ActiveOrg:
    """Resolve the organization the request operates on.

    Uses the ``X-Org-Id`` header when provided (verifying membership), otherwise
    falls back to the user's first organization. Auto-provisions the default
    organization on legacy installs that predate multi-tenancy.
    """
    orgs = await tenancy.list_organizations_for_user(user_id)
    if not orgs:
        # Legacy/first-run safety net: attribute existing data to a default org.
        await tenancy.ensure_default_org()
        orgs = await tenancy.list_organizations_for_user(user_id)
    if not orgs:
        raise HTTPException(status_code=403, detail="User is not a member of any organization")

    chosen = None
    if x_org_id is not None:
        chosen = next((o for o in orgs if o["id"] == x_org_id), None)
        if chosen is None:
            raise HTTPException(status_code=403, detail="Not a member of the requested organization")
    else:
        chosen = orgs[0]

    return ActiveOrg(
        org_id=chosen["id"],
        role=chosen["role"],
        namespace=chosen["memory_namespace"],
        name=chosen["name"],
    )


def require_role(minimum: str):
    """Dependency factory enforcing a minimum role in the active organization."""

    async def _checker(org: ActiveOrg = Depends(get_active_org)) -> ActiveOrg:
        if not tenancy.role_at_least(org.role, minimum):
            raise HTTPException(
                status_code=403,
                detail=f"Requires '{minimum}' role or higher in this organization",
            )
        return org

    return _checker


@dataclass
class ActiveProject:
    """The project the request operates on, within its owning organization.

    Exposes the same org-level attributes as ``ActiveOrg`` (``org_id``,
    ``role``) so content routes can migrate by swapping the dependency;
    ``namespace`` is the memory namespace of the PROJECT. ``role`` is the
    caller's effective role on the project (org role for org admin/owner,
    project membership role otherwise).
    """

    org_id: int
    project_id: int
    role: str
    namespace: str
    name: str
    org_name: str


async def get_active_project(
    org: ActiveOrg = Depends(get_active_org),
    user_id: int = Depends(get_current_user_id),
    x_project_id: Optional[int] = Header(default=None, alias="X-Project-Id"),
) -> ActiveProject:
    """Resolve the project the request operates on.

    Uses the ``X-Project-Id`` header when provided (verifying that the project
    belongs to the active organization, 404 otherwise), falling back to the
    organization's default (oldest) project. In both cases the caller must have
    access to the project: org admin/owner always do, other members need an
    explicit ``project_members`` row. ``role`` is the effective project role.
    """
    from .. import projects as projects_mod

    if x_project_id is not None:
        project = await projects_mod.get_project(x_project_id)
        if project is None or project["org_id"] != org.org_id:
            raise HTTPException(status_code=404, detail="Project not found")
    else:
        default_id = await projects_mod.get_default_project_id(org.org_id)
        project = (
            await projects_mod.get_project(default_id) if default_id is not None else None
        )
        if project is None:
            raise HTTPException(status_code=500, detail="Organization has no projects")

    effective_role = await projects_mod.resolve_project_access(
        org.org_id, user_id, int(project["id"])
    )
    if effective_role is None:
        raise HTTPException(status_code=403, detail="Not a member of this project")

    return ActiveProject(
        org_id=org.org_id,
        project_id=int(project["id"]),
        role=effective_role,
        namespace=project["memory_namespace"],
        name=project["name"],
        org_name=org.name,
    )


def require_project_role(minimum: str):
    """Dependency factory: active project plus a minimum role in its organization."""

    async def _checker(
        project: ActiveProject = Depends(get_active_project),
    ) -> ActiveProject:
        if not tenancy.role_at_least(project.role, minimum):
            raise HTTPException(
                status_code=403,
                detail=f"Requires '{minimum}' role or higher in this organization",
            )
        return project

    return _checker
