"""Admin authentication routes."""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from ... import tenancy
from ...config import get_settings
from ..deps import get_current_user_id
from ..oidc import OIDCError, validate_oidc_token
from ..security import (
    authenticate_admin,
    create_session,
    delete_session,
    get_admin_user,
    is_configured,
    provision_oidc_user,
    require_valid_token_or_raise,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

_OIDC_STATES: dict[str, float] = {}


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
    """Invalidate bearer token; OIDC-born sessions also trigger IdP end-session URL."""
    await require_valid_token_or_raise(authorization)
    token = authorization.removeprefix("Bearer ").strip()
    id_token = await delete_session(token)

    # If session was OIDC-born and OIDC is enabled, construct end-session URL for UI.
    settings = get_settings()
    if id_token and settings.oidc_enabled and settings.oidc_issuer:
        params = urlencode({
            "id_token_hint": id_token,
            "post_logout_redirect_uri": settings.ui_base_url.rstrip('/'),
        })
        logout_url = f"{settings.oidc_issuer.rstrip('/')}/protocol/openid-connect/logout?{params}"
        return {"status": "ok", "logout_url": logout_url}

    return {"status": "ok"}


def _issue_state() -> str:
    """Mint a short-lived state token for the OIDC login round-trip."""
    now = time.time()
    for key in [k for k, exp in list(_OIDC_STATES.items()) if exp < now]:
        _OIDC_STATES.pop(key, None)
    state = secrets.token_urlsafe(24)
    _OIDC_STATES[state] = now + 600
    return state


def _redirect_uri() -> str:
    settings = get_settings()
    return f"{settings.public_base_url.rstrip('/')}/api/auth/oidc/callback"


@router.get("/oidc/config")
async def oidc_config():
    """Report whether OIDC login is enabled, for the login page to branch on."""
    settings = get_settings()
    return {"enabled": bool(settings.oidc_enabled and settings.oidc_issuer)}


@router.get("/oidc/login")
async def oidc_login():
    """Redirect to Keycloak's authorization endpoint."""
    settings = get_settings()
    if not (settings.oidc_enabled and settings.oidc_issuer):
        raise HTTPException(status_code=404, detail="OIDC disabled")
    params = urlencode({
        "client_id": settings.oidc_client_id,
        "response_type": "code",
        "scope": "openid profile email",
        "redirect_uri": _redirect_uri(),
        "state": _issue_state(),
    })
    return RedirectResponse(
        f"{settings.oidc_issuer.rstrip('/')}/protocol/openid-connect/auth?{params}"
    )


@router.get("/oidc/callback")
async def oidc_callback(code: str, state: str):
    """Exchange the auth code, validate the token, provision the user, issue a native session."""
    settings = get_settings()
    if not (settings.oidc_enabled and settings.oidc_issuer):
        raise HTTPException(status_code=404, detail="OIDC disabled")
    if _OIDC_STATES.pop(state, 0) < time.time():
        raise HTTPException(status_code=400, detail="invalid or expired state")
    token_url = f"{settings.oidc_issuer.rstrip('/')}/protocol/openid-connect/token"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(token_url, data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": settings.oidc_client_id,
            "client_secret": settings.oidc_client_secret,
            "redirect_uri": _redirect_uri(),
        })
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="authorization code exchange failed")
    try:
        token_data = resp.json()
        claims = await asyncio.to_thread(validate_oidc_token, token_data.get("access_token", ""))
        id_token = token_data.get("id_token")
    except OIDCError as exc:
        logger.warning("OIDC callback token validation failed: %s", exc)
        raise HTTPException(status_code=401, detail="invalid or expired token")
    user_id = await provision_oidc_user(claims)
    session = await create_session(user_id, id_token=id_token)
    return RedirectResponse(f"{settings.ui_base_url.rstrip('/')}/?oidc_token={session}")

