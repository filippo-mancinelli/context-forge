"""Admin authentication routes."""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from ..security import (
    authenticate_admin,
    create_session,
    delete_session,
    is_configured,
    require_valid_token_or_raise,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


@router.post("/login")
async def login(req: LoginRequest):
    """Login admin and issue bearer token."""
    if not await is_configured():
        raise HTTPException(status_code=423, detail="Setup required")
    user_id = await authenticate_admin(req.username, req.password)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = await create_session(user_id)
    return {"token": token, "token_type": "bearer"}


@router.get("/session")
async def session(authorization: str | None = Header(default=None)):
    """Validate current bearer token."""
    await require_valid_token_or_raise(authorization)
    return {"status": "ok"}


@router.post("/logout")
async def logout(authorization: str | None = Header(default=None)):
    """Invalidate current bearer token."""
    await require_valid_token_or_raise(authorization)
    token = authorization.removeprefix("Bearer ").strip()
    await delete_session(token)
    return {"status": "ok"}

