"""OAuth 2.0 endpoints for MCP server authentication."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from ..api.security import (
    create_authorization_code,
    create_oauth_client,
    create_oauth_token,
    get_oauth_client,
    validate_authorization_code,
    validate_session_token,
    validate_oauth_token,
)
from ..db import get_pool

logger = logging.getLogger(__name__)


async def oauth_authorize(request: Request) -> JSONResponse:
    """OAuth authorization endpoint - returns authorization URL."""
    # Parse query parameters
    client_id = request.query_params.get("client_id")
    redirect_uri = request.query_params.get("redirect_uri")
    response_type = request.query_params.get("response_type", "code")
    scope = request.query_params.get("scope", "read,write")
    state = request.query_params.get("state", "")

    if not client_id:
        return JSONResponse(status_code=400, content={"detail": "Missing client_id"})

    # Validate client
    client = await get_oauth_client(client_id)
    if not client:
        return JSONResponse(status_code=401, content={"detail": "Invalid client_id"})

    # Validate redirect URI
    if redirect_uri not in client["redirect_uris"]:
        return JSONResponse(status_code=400, content={"detail": "Invalid redirect_uri"})

    # Check if user is authenticated via session cookie
    auth_header = request.headers.get("Authorization")
    user_id = None

    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.removeprefix("Bearer ").strip()
        if await validate_session_token(token):
            # Get user ID from session
            pool = await get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT au.id FROM admin_users au
                       JOIN auth_sessions s ON s.user_id = au.id
                       JOIN (SELECT token_hash FROM auth_sessions WHERE token_hash = encode(sha256($1::bytea), 'hex')) AS t ON true
                       WHERE s.token_hash = t.token_hash""",
                    token.encode(),
                )
                if row:
                    user_id = row["id"]

    if not user_id:
        # Return auth required response
        return JSONResponse(
            status_code=401,
            content={
                "detail": "Authentication required",
                "error": "login_required",
                "auth_url": f"/oauth/login?client_id={client_id}&redirect_uri={redirect_uri}&state={state}",
            },
        )

    # Generate authorization code
    code = await create_authorization_code(client_id, user_id, redirect_uri, scope)

    # Return authorization URL with code
    auth_url = f"{redirect_uri}?code={code}"
    if state:
        auth_url += f"&state={state}"

    return JSONResponse(
        content={
            "authorization_url": auth_url,
            "code": code,
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )


async def oauth_token(request: Request) -> JSONResponse:
    """OAuth token endpoint - exchange authorization code for access token."""
    if request.method == "OPTIONS":
        return JSONResponse(content={})

    if request.method != "POST":
        return JSONResponse(status_code=405, content={"detail": "Method not allowed"})

    # Parse form data
    form_data = await request.form()
    grant_type = form_data.get("grant_type", "authorization_code")
    code = form_data.get("code")
    client_id = form_data.get("client_id")
    redirect_uri = form_data.get("redirect_uri")

    if grant_type != "authorization_code":
        return JSONResponse(status_code=400, content={"detail": "Unsupported grant_type"})

    if not code or not client_id or not redirect_uri:
        return JSONResponse(status_code=400, content={"detail": "Missing required parameters"})

    # Validate authorization code
    auth_code_data = await validate_authorization_code(code, client_id)
    if not auth_code_data:
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or expired authorization code"},
        )

    # Create access token
    access_token = await create_oauth_token(
        client_id,
        auth_code_data["user_id"],
        auth_code_data["scope"],
    )

    return JSONResponse(
        content={
            "access_token": access_token,
            "token_type": "Bearer",
            "scope": auth_code_data["scope"],
            "expires_in": 86400,  # 24 hours
        }
    )


async def oauth_callback(request: Request) -> JSONResponse:
    """OAuth callback endpoint - handles redirect after user approval."""
    code = request.query_params.get("code")
    state = request.query_params.get("state", "")
    error = request.query_params.get("error")

    if error:
        return JSONResponse(
            status_code=400,
            content={"detail": f"Authorization failed: {error}"},
        )

    if not code:
        return JSONResponse(status_code=400, content={"detail": "Missing authorization code"})

    # Return success response
    return JSONResponse(
        content={
            "code": code,
            "state": state,
            "message": "Authorization successful, please return to your application",
        }
    )


async def oauth_validate(request: Request) -> JSONResponse:
    """Validate OAuth token from Authorization header."""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return JSONResponse(status_code=401, content={"detail": "Missing Authorization header"})

    token = auth_header.removeprefix("Bearer ").strip()
    token_info = await validate_oauth_token(token)

    if not token_info:
        return JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})

    return JSONResponse(
        content={
            "valid": True,
            "scope": token_info["scope"],
            "expires_at": token_info["expires_at"].isoformat(),
        }
    )


async def oauth_setup_client() -> JSONResponse:
    """Setup default OAuth client for Claude Code."""
    try:
        # Create Claude Code client
        await create_oauth_client(
            client_id="claude-code",
            name="Claude Code CLI",
            redirect_uris=[
                "http://localhost:5173/oauth/callback",
                "http://localhost:3000/oauth/callback",
                "http://127.0.0.1:5173/oauth/callback",
                "http://127.0.0.1:3000/oauth/callback",
            ],
            scopes="read,write",
        )

        return JSONResponse(
            content={
                "status": "success",
                "message": "OAuth client created successfully",
                "client_id": "claude-code",
            }
        )
    except Exception as e:
        if "unique" in str(e).lower():
            # Client already exists
            return JSONResponse(
                content={
                    "status": "exists",
                    "message": "OAuth client already exists",
                    "client_id": "claude-code",
                }
            )
        raise
