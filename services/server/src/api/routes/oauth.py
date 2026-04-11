"""OAuth 2.0 REST endpoints for MCP authentication."""
from __future__ import annotations

from fastapi import APIRouter, Request

from ...mcp.oauth import (
    oauth_authorize,
    oauth_callback,
    oauth_setup_client,
    oauth_token,
    oauth_validate,
)

router = APIRouter(prefix="/oauth", tags=["oauth"])


@router.get("/authorize")
async def authorize(request: Request):
    return await oauth_authorize(request)


@router.post("/token")
async def token(request: Request):
    return await oauth_token(request)


@router.get("/callback")
async def callback(request: Request):
    return await oauth_callback(request)


@router.post("/validate")
async def validate(request: Request):
    return await oauth_validate(request)


@router.post("/setup-client")
async def setup_client():
    return await oauth_setup_client()
