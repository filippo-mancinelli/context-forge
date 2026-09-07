"""Organization, membership and user management routes."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ... import projects, tenancy
from ...mcp.permissions import DEFAULT_ROLE_PERMISSIONS, ORG_ROLES, PERMISSIONS
from ..deps import get_current_user_id
from ..security import create_admin_user, get_user_id_by_email

router = APIRouter(prefix="/organizations", tags=["organizations"])

_ROLE_PATTERN = r"^(viewer|member|admin|owner)$"
# Pragmatic email check — avoids pulling in the email-validator dependency.
_EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


def _serialize(record: dict) -> dict:
    out = dict(record)
    for key in ("created_at", "expires_at", "accepted_at"):
        if out.get(key) is not None and hasattr(out[key], "isoformat"):
            out[key] = out[key].isoformat()
    return out


def _mask_roles(records: list[dict], caller_role: str) -> list[dict]:
    """Il campo role è informazione di governance: visibile solo all'owner."""
    if caller_role == "owner":
        return records
    return [{k: v for k, v in r.items() if k != "role"} for r in records]


async def _require_role_in(org_id: int, user_id: int, minimum: str) -> str:
    role = await tenancy.get_membership_role(org_id, user_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    if not tenancy.role_at_least(role, minimum):
        raise HTTPException(status_code=403, detail=f"Requires '{minimum}' role or higher")
    return role


# ===== Organizations =====

class CreateOrgRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class UpdateOrgRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)


@router.get("")
async def list_orgs(user_id: int = Depends(get_current_user_id)):
    orgs = await tenancy.list_organizations_for_user(user_id)
    return {"organizations": [_serialize(o) for o in orgs]}


@router.post("")
async def create_org(req: CreateOrgRequest, user_id: int = Depends(get_current_user_id)):
    org = await tenancy.create_organization(req.name, owner_user_id=user_id)
    return {"status": "ok", "organization": _serialize(org)}


@router.get("/{org_id}")
async def get_org(org_id: int, user_id: int = Depends(get_current_user_id)):
    await _require_role_in(org_id, user_id, "viewer")
    org = await tenancy.get_organization(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return {"organization": _serialize(org)}


@router.patch("/{org_id}")
async def update_org(org_id: int, req: UpdateOrgRequest, user_id: int = Depends(get_current_user_id)):
    await _require_role_in(org_id, user_id, "owner")
    org = await tenancy.update_organization(org_id, req.name)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return {"status": "ok", "organization": _serialize(org)}


@router.delete("/{org_id}")
async def delete_org(org_id: int, user_id: int = Depends(get_current_user_id)):
    await _require_role_in(org_id, user_id, "owner")
    if await tenancy.count_organizations() <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete the last organization")
    await tenancy.delete_organization(org_id)
    return {"status": "ok"}


# ===== Members =====

class UpdateMemberRequest(BaseModel):
    role: str = Field(pattern=_ROLE_PATTERN)


@router.get("/{org_id}/members")
async def list_members(org_id: int, user_id: int = Depends(get_current_user_id)):
    caller_role = await _require_role_in(org_id, user_id, "viewer")
    members = await tenancy.list_members(org_id)
    return {"members": _mask_roles([_serialize(m) for m in members], caller_role)}


@router.patch("/{org_id}/members/{member_id}")
async def update_member(
    org_id: int,
    member_id: int,
    req: UpdateMemberRequest,
    user_id: int = Depends(get_current_user_id),
):
    await _require_role_in(org_id, user_id, "owner")
    current = await tenancy.get_membership_role(org_id, member_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Member not found")
    # Prevent demoting the last remaining owner.
    if current == "owner" and req.role != "owner" and await tenancy.count_owners(org_id) <= 1:
        raise HTTPException(status_code=400, detail="Organization must keep at least one owner")
    await tenancy.update_member_role(org_id, member_id, req.role)
    return {"status": "ok"}


@router.delete("/{org_id}/members/{member_id}")
async def remove_member(org_id: int, member_id: int, user_id: int = Depends(get_current_user_id)):
    # Members may remove themselves (leave); otherwise owner+ is required.
    if member_id != user_id:
        await _require_role_in(org_id, user_id, "owner")
    else:
        await _require_role_in(org_id, user_id, "viewer")
    target_role = await tenancy.get_membership_role(org_id, member_id)
    if target_role is None:
        raise HTTPException(status_code=404, detail="Member not found")
    if target_role == "owner" and await tenancy.count_owners(org_id) <= 1:
        raise HTTPException(status_code=400, detail="Organization must keep at least one owner")
    await tenancy.remove_member(org_id, member_id)
    return {"status": "ok"}


# ===== Users =====

# Ruoli assegnabili a un membro di progetto (non esiste 'owner' di progetto).
_PROJECT_ROLE_PATTERN = r"^(viewer|member|admin)$"


class ProjectGrant(BaseModel):
    project_id: int
    role: str = Field(default="member", pattern=_PROJECT_ROLE_PATTERN)


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=256)
    email: str = Field(pattern=_EMAIL_PATTERN, max_length=320)
    role: str = Field(default="member", pattern=_ROLE_PATTERN)
    projects: list[ProjectGrant] = Field(default_factory=list)


@router.post("/{org_id}/users")
async def create_org_user(
    org_id: int,
    req: CreateUserRequest,
    user_id: int = Depends(get_current_user_id),
):
    """Crea un'utenza locale già membro dell'org e abilitata sui progetti indicati."""
    caller_role = await _require_role_in(org_id, user_id, "admin")
    if req.role == "owner":
        raise HTTPException(
            status_code=422,
            detail="The owner role cannot be granted at user creation",
        )
    # Solo l'owner sceglie i ruoli; per gli altri il nuovo utente entra come member.
    role = req.role if caller_role == "owner" else "member"
    email = str(req.email).lower().strip()

    if await get_user_id_by_email(email) is not None:
        raise HTTPException(status_code=409, detail="A user with this email already exists")

    # Valida i progetti prima di creare qualunque record.
    grants: dict[int, str] = {}
    for grant in req.projects:
        project = await projects.get_project(grant.project_id)
        if project is None or project["org_id"] != org_id:
            raise HTTPException(status_code=404, detail=f"Project {grant.project_id} not found")
        grants[grant.project_id] = grant.role if caller_role == "owner" else "member"

    try:
        new_user_id = await create_admin_user(req.username, req.password, email=email)
    except Exception as e:  # e.g. duplicate username
        raise HTTPException(status_code=409, detail=f"Could not create account: {e}")

    await tenancy.add_member(org_id, new_user_id, role)
    for project_id, project_role in grants.items():
        await projects.add_project_member(project_id, new_user_id, project_role)

    return {
        "status": "ok",
        "user": {"user_id": new_user_id, "username": req.username, "email": email, "role": role},
        "projects": [
            {"project_id": pid, "role": prole} for pid, prole in grants.items()
        ],
    }


# ===== Permessi MCP per ruolo =====

class McpPermissionsRequest(BaseModel):
    roles: dict[str, list[str]]


def _validate_matrix(roles: dict[str, list[str]]) -> dict[str, list[str]]:
    """Valida e normalizza la matrice ruolo->permessi (dedup, '*' esclusivo)."""
    if set(roles) != set(ORG_ROLES):
        raise HTTPException(
            status_code=422,
            detail=f"Matrix must define exactly the roles: {', '.join(ORG_ROLES)}",
        )
    clean: dict[str, list[str]] = {}
    for role, perms in roles.items():
        unique = list(dict.fromkeys(perms))
        for perm in unique:
            if perm != "*" and perm not in PERMISSIONS:
                raise HTTPException(status_code=422, detail=f"Unknown permission '{perm}'")
        if "*" in unique and len(unique) > 1:
            raise HTTPException(
                status_code=422,
                detail=f"Role '{role}': '*' cannot be combined with other permissions",
            )
        clean[role] = unique
    if all(len(perms) == 0 for perms in clean.values()):
        raise HTTPException(
            status_code=422,
            detail=(
                "Matrix cannot leave every role with zero permissions — "
                "use DELETE /organizations/{org_id}/mcp-permissions to restore defaults"
            ),
        )
    return clean


@router.get("/{org_id}/mcp-permissions")
async def get_mcp_permissions(org_id: int, user_id: int = Depends(get_current_user_id)):
    await _require_role_in(org_id, user_id, "owner")
    customized = await tenancy.get_org_role_permissions(org_id)
    roles = {}
    # An org with ANY stored rows has fully taken over its matrix (see C2/tenancy.
    # resolve_role_permissions): every role is "customized", and a role with no rows
    # of its own is genuinely empty — it must NOT show the hardcoded defaults, or the
    # GET response would misrepresent what's actually enforced at runtime.
    org_has_customization = bool(customized)
    for role in ORG_ROLES:
        if org_has_customization:
            roles[role] = {"permissions": sorted(customized.get(role, [])), "customized": True}
        else:
            roles[role] = {
                "permissions": sorted(DEFAULT_ROLE_PERMISSIONS[role]),
                "customized": False,
            }
    return {"roles": roles, "available_permissions": list(PERMISSIONS)}


@router.put("/{org_id}/mcp-permissions")
async def put_mcp_permissions(
    org_id: int, req: McpPermissionsRequest, user_id: int = Depends(get_current_user_id)
):
    await _require_role_in(org_id, user_id, "owner")
    clean = _validate_matrix(req.roles)
    await tenancy.set_org_role_permissions(org_id, clean)
    return {"status": "ok"}


@router.delete("/{org_id}/mcp-permissions")
async def reset_mcp_permissions(org_id: int, user_id: int = Depends(get_current_user_id)):
    await _require_role_in(org_id, user_id, "owner")
    await tenancy.clear_org_role_permissions(org_id)
    return {"status": "ok"}
