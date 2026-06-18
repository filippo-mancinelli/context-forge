"""Public invitation acceptance routes (no session required).

These power the invite-link flow: a prospective member opens the link, sees
which organization invited them, and creates their account in one step.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ... import tenancy
from ..security import create_admin_user, create_session, get_user_id_by_email

router = APIRouter(prefix="/invitations", tags=["invitations"])


def _serialize(record: dict) -> dict:
    out = dict(record)
    for key in ("expires_at",):
        if out.get(key) is not None and hasattr(out[key], "isoformat"):
            out[key] = out[key].isoformat()
    return out


class AcceptInvitationRequest(BaseModel):
    token: str
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=256)


@router.get("/{token}")
async def preview_invitation(token: str):
    """Return the organization name/role for a pending invite token."""
    invite = await tenancy.get_invitation_by_token(token)
    if not invite:
        raise HTTPException(status_code=404, detail="Invitation not found or expired")
    return {
        "email": invite["email"],
        "role": invite["role"],
        "org_name": invite["org_name"],
        "expires_at": invite["expires_at"].isoformat() if invite.get("expires_at") else None,
    }


@router.post("/accept")
async def accept_invitation(req: AcceptInvitationRequest):
    """Accept an invitation: create the account, join the org, return a session."""
    invite = await tenancy.get_invitation_by_token(req.token)
    if not invite:
        raise HTTPException(status_code=404, detail="Invitation not found or expired")

    if await get_user_id_by_email(invite["email"]) is not None:
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists. Log in to accept.",
        )

    try:
        user_id = await create_admin_user(req.username, req.password, email=invite["email"])
    except Exception as e:  # e.g. duplicate username
        raise HTTPException(status_code=409, detail=f"Could not create account: {e}")

    await tenancy.add_member(invite["org_id"], user_id, invite["role"])
    await tenancy.mark_invitation_accepted(invite["id"])

    token = await create_session(user_id)
    return {"status": "ok", "token": token, "token_type": "bearer"}
