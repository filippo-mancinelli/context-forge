"""Organization, membership and invitation management routes."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ... import tenancy
from ..deps import get_current_user_id
from ..security import get_user_id_by_email

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
    await _require_role_in(org_id, user_id, "admin")
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
    await _require_role_in(org_id, user_id, "viewer")
    members = await tenancy.list_members(org_id)
    return {"members": [_serialize(m) for m in members]}


@router.patch("/{org_id}/members/{member_id}")
async def update_member(
    org_id: int,
    member_id: int,
    req: UpdateMemberRequest,
    user_id: int = Depends(get_current_user_id),
):
    await _require_role_in(org_id, user_id, "admin")
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
    # Members may remove themselves (leave); otherwise admin+ is required.
    if member_id != user_id:
        await _require_role_in(org_id, user_id, "admin")
    else:
        await _require_role_in(org_id, user_id, "viewer")
    target_role = await tenancy.get_membership_role(org_id, member_id)
    if target_role is None:
        raise HTTPException(status_code=404, detail="Member not found")
    if target_role == "owner" and await tenancy.count_owners(org_id) <= 1:
        raise HTTPException(status_code=400, detail="Organization must keep at least one owner")
    await tenancy.remove_member(org_id, member_id)
    return {"status": "ok"}


# ===== Invitations =====

class CreateInvitationRequest(BaseModel):
    email: str = Field(pattern=_EMAIL_PATTERN, max_length=320)
    role: str = Field(default="member", pattern=_ROLE_PATTERN)


@router.get("/{org_id}/invitations")
async def list_org_invitations(org_id: int, user_id: int = Depends(get_current_user_id)):
    await _require_role_in(org_id, user_id, "admin")
    invitations = await tenancy.list_invitations(org_id)
    return {"invitations": [_serialize(i) for i in invitations]}


@router.post("/{org_id}/invitations")
async def create_org_invitation(
    org_id: int,
    req: CreateInvitationRequest,
    user_id: int = Depends(get_current_user_id),
):
    await _require_role_in(org_id, user_id, "admin")
    email = str(req.email)

    # If the invitee already has an account, add them directly.
    existing_user = await get_user_id_by_email(email)
    if existing_user is not None:
        if await tenancy.get_membership_role(org_id, existing_user) is not None:
            raise HTTPException(status_code=409, detail="User is already a member")
        await tenancy.add_member(org_id, existing_user, req.role)
        return {"status": "ok", "added_existing_user": True}

    token, invitation = await tenancy.create_invitation(org_id, email, req.role, invited_by=user_id)
    return {
        "status": "ok",
        "invitation": _serialize(invitation),
        # Self-hosted: no email delivery, so the inviter shares this link directly.
        "invite_token": token,
    }


@router.delete("/{org_id}/invitations/{invitation_id}")
async def revoke_org_invitation(
    org_id: int, invitation_id: int, user_id: int = Depends(get_current_user_id)
):
    await _require_role_in(org_id, user_id, "admin")
    ok = await tenancy.revoke_invitation(org_id, invitation_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Invitation not found")
    return {"status": "ok"}
