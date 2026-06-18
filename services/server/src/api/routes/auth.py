"""Admin authentication routes."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from ... import tenancy
from ..deps import get_current_user_id
from ..security import (
    authenticate_admin,
    create_session,
    delete_session,
    get_admin_user,
    is_configured,
    require_valid_token_or_raise,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


@router.post("/login")
async def login(req: LoginRequest):
    """Login admin and issue bearer token."""
    if not await is_configured():
        logger.warning("Login attempt rejected: setup not completed")
        raise HTTPException(status_code=423, detail="Setup required")
    user_id = await authenticate_admin(req.username, req.password)
    if not user_id:
        logger.warning("Failed login attempt for username=%r", req.username)
        raise HTTPException(status_code=401, detail="Invalid credentials")
    logger.info("Successful login for username=%r (user_id=%d)", req.username, user_id)
    token = await create_session(user_id)
    return {"token": token, "token_type": "bearer"}


@router.get("/session")
async def session(authorization: str | None = Header(default=None)):
    """Validate current bearer token."""
    await require_valid_token_or_raise(authorization)
    return {"status": "ok"}


@router.get("/me")
async def me(user_id: int = Depends(get_current_user_id)):
    """Return the current user and the organizations they belong to."""
    user = await get_admin_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    orgs = await tenancy.list_organizations_for_user(user_id)
    if not orgs:
        # Self-heal legacy installs that predate organizations.
        await tenancy.ensure_default_org()
        orgs = await tenancy.list_organizations_for_user(user_id)
    serialized = []
    for o in orgs:
        item = dict(o)
        if item.get("created_at") is not None and hasattr(item["created_at"], "isoformat"):
            item["created_at"] = item["created_at"].isoformat()
        serialized.append(item)
    return {
        "user": {"id": user["id"], "username": user["username"], "email": user.get("email")},
        "organizations": serialized,
    }


@router.post("/logout")
async def logout(authorization: str | None = Header(default=None)):
    """Invalidate current bearer token."""
    await require_valid_token_or_raise(authorization)
    token = authorization.removeprefix("Bearer ").strip()
    await delete_session(token)
    return {"status": "ok"}

