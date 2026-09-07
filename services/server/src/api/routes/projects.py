"""Gestione progetti: contenitori di risorse con endpoint MCP dedicato."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ... import projects
from ...tenancy import ROLES, get_membership_role
from ..deps import ActiveOrg, get_active_org, get_current_user_id, require_role
from ..security import get_user_id_by_email

router = APIRouter(prefix="/projects", tags=["projects"])

# Ruoli assegnabili a un membro di progetto (non si assegna 'owner' per progetto).
PROJECT_MEMBER_ROLES = frozenset({"viewer", "member", "admin"})


def _serialize(record: dict) -> dict:
    out = dict(record)
    if out.get("created_at") is not None and hasattr(out["created_at"], "isoformat"):
        out["created_at"] = out["created_at"].isoformat()
    return out


def _mask_member_roles(records: list[dict], caller_role: str) -> list[dict]:
    """Il campo role è informazione di governance: visibile solo all'owner."""
    if caller_role == "owner":
        return records
    return [{k: v for k, v in r.items() if k != "role"} for r in records]


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class UpdateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class ProjectMemberRequest(BaseModel):
    user_id: Optional[int] = None
    email: Optional[str] = None
    role: str = "member"


@router.get("")
async def list_projects_route(
    org: ActiveOrg = Depends(get_active_org),
    user_id: int = Depends(get_current_user_id),
):
    """Solo i progetti a cui l'utente ha accesso (owner/admin org: tutti)."""
    items = await projects.list_accessible_projects(org.org_id, user_id)
    return {"projects": [_serialize(p) for p in items]}


async def _project_in_org(project_id: int, org_id: int) -> dict:
    project = await projects.get_project(project_id)
    if project is None or project["org_id"] != org_id:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("/{project_id}/members")
async def list_project_members_route(
    project_id: int, org: ActiveOrg = Depends(require_role("admin"))
):
    await _project_in_org(project_id, org.org_id)
    members = await projects.list_project_members(project_id)
    return {"members": _mask_member_roles([_serialize(m) for m in members], org.role)}


@router.post("/{project_id}/members")
async def add_project_member_route(
    project_id: int,
    req: ProjectMemberRequest,
    org: ActiveOrg = Depends(require_role("admin")),
):
    await _project_in_org(project_id, org.org_id)
    if req.role not in PROJECT_MEMBER_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"role must be one of {', '.join(sorted(PROJECT_MEMBER_ROLES))}",
        )
    target_id = req.user_id
    if target_id is None and req.email:
        target_id = await get_user_id_by_email(req.email)
    if target_id is None:
        raise HTTPException(status_code=404, detail="User not found")
    # Solo membri dell'org possono essere abilitati su un suo progetto.
    if await get_membership_role(org.org_id, target_id) is None:
        raise HTTPException(
            status_code=400, detail="User is not a member of this organization"
        )
    # Solo l'owner sceglie il ruolo di progetto; per gli altri si entra come member.
    role = req.role if org.role == "owner" else "member"
    await projects.add_project_member(project_id, target_id, role)
    return {"status": "ok", "member": {"user_id": target_id, "role": role}}


@router.delete("/{project_id}/members/{member_user_id}")
async def remove_project_member_route(
    project_id: int,
    member_user_id: int,
    org: ActiveOrg = Depends(require_role("owner")),
):
    if org.role != "owner":
        raise HTTPException(
            status_code=403,
            detail="Requires 'owner' role or higher in this organization",
        )
    await _project_in_org(project_id, org.org_id)
    removed = await projects.remove_project_member(project_id, member_user_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Project member not found")
    return {"status": "ok"}


@router.post("")
async def create_project_route(
    req: CreateProjectRequest, org: ActiveOrg = Depends(require_role("admin"))
):
    try:
        project = await projects.create_project(org.org_id, req.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok", "project": _serialize(project)}


@router.patch("/{project_id}")
async def update_project_route(
    project_id: int,
    req: UpdateProjectRequest,
    org: ActiveOrg = Depends(require_role("admin")),
):
    existing = await projects.get_project(project_id)
    if existing is None or existing["org_id"] != org.org_id:
        raise HTTPException(status_code=404, detail="Project not found")
    project = await projects.update_project(project_id, req.name)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"status": "ok", "project": _serialize(project)}


@router.delete("/{project_id}")
async def delete_project_route(
    project_id: int, org: ActiveOrg = Depends(require_role("admin"))
):
    existing = await projects.get_project(project_id)
    if existing is None or existing["org_id"] != org.org_id:
        raise HTTPException(status_code=404, detail="Project not found")
    if await projects.count_projects(org.org_id) <= 1:
        raise HTTPException(
            status_code=400, detail="Cannot delete the last project of the organization"
        )
    await projects.delete_project(project_id)
    return {"status": "ok"}
