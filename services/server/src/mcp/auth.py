"""MCP authentication middleware."""
from __future__ import annotations

import logging
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from ..api.security import validate_mcp_api_key, validate_oauth_token
from ..config import get_settings
from .context import set_current_namespace, set_current_org_id

logger = logging.getLogger(__name__)


async def _resolve_org(org_id, user_id=None):
    """Resolve (org_id, memory namespace) for an authenticated MCP request."""
    from .. import tenancy

    if org_id is not None:
        return org_id, await tenancy.get_namespace_for_org(org_id)
    if user_id is not None:
        orgs = await tenancy.list_organizations_for_user(user_id)
        if orgs:
            return orgs[0]["id"], orgs[0]["memory_namespace"]
    return None, None


class MCPAuthMiddleware(BaseHTTPMiddleware):
    """Middleware to validate MCP API keys or OAuth tokens on incoming requests."""

    async def dispatch(self, request: Request, call_next: Callable) -> JSONResponse:
        # Skip auth for health checks, OPTIONS, and OAuth endpoints
        skip_paths = ["/health", "/oauth/", "/mcp/oauth/"]
        if request.method == "OPTIONS" or any(
            request.url.path.startswith(path) for path in skip_paths
        ):
            return await call_next(request)

        # Check if auth is enabled
        settings = get_settings()
        if settings.mcp_auth_mode == "disabled":
            return await call_next(request)

        # Try OAuth Bearer token first
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.removeprefix("Bearer ").strip()
            token_info = await validate_oauth_token(token)
            if token_info:
                # Valid OAuth token
                request.state.mcp_auth_info = {"type": "oauth", **token_info}
                oid, ns = await _resolve_org(token_info.get("org_id"), token_info.get("user_id"))
                set_current_org_id(oid)
                set_current_namespace(ns)
                return await call_next(request)
            else:
                logger.warning(f"MCP request with invalid OAuth token from {request.client.host}")
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or expired OAuth token"},
                )

        # Fall back to X-API-Key header
        api_key = request.headers.get("X-API-Key")
        if api_key:
            key_info = await validate_mcp_api_key(api_key)
            if key_info:
                # Valid API key
                request.state.mcp_auth_info = {"type": "api_key", **key_info}
                oid, ns = await _resolve_org(key_info.get("org_id"))
                set_current_org_id(oid)
                set_current_namespace(ns)
                return await call_next(request)
            else:
                logger.warning(f"MCP request with invalid API key from {request.client.host}")
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or expired API key"},
                )

        # No valid auth found
        logger.warning(f"MCP request missing authentication from {request.client.host}")
        return JSONResponse(
            status_code=401,
            content={"detail": "Missing authentication. Provide either X-API-Key header or Authorization: Bearer token."},
        )


def add_auth_middleware(app, auth_mode: str = "enabled"):
    """Add authentication middleware to the MCP app.

    Args:
        app: The ASGI app to wrap
        auth_mode: "disabled", "enabled", or "transition" (log warning but allow)
    """
    if auth_mode == "disabled":
        logger.info("MCP authentication is DISABLED - all requests allowed")
        return app

    middleware = MCPAuthMiddleware(app)
    logger.info(f"MCP authentication is {auth_mode.upper()}")

    # Create a new app with middleware
    async def wrapped_app(scope, receive, send):
        await middleware(scope, receive, send)

    return wrapped_app
