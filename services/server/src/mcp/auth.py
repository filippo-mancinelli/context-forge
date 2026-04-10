"""MCP authentication middleware."""
from __future__ import annotations

import logging
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from ..api.security import validate_mcp_api_key
from ..config import get_settings

logger = logging.getLogger(__name__)


class MCPAuthMiddleware(BaseHTTPMiddleware):
    """Middleware to validate MCP API keys on incoming requests."""

    async def dispatch(self, request: Request, call_next: Callable) -> JSONResponse:
        # Skip auth for health checks and OPTIONS
        if request.url.path == "/health" or request.method == "OPTIONS":
            return await call_next(request)

        # Check if auth is enabled
        settings = get_settings()
        if settings.mcp_auth_mode == "disabled":
            return await call_next(request)

        # Extract API key from header
        api_key = request.headers.get("X-API-Key")

        if not api_key:
            logger.warning(f"MCP request missing API key from {request.client.host}")
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing X-API-Key header. Provide a valid MCP API key."},
            )

        # Validate the key
        key_info = await validate_mcp_api_key(api_key)
        if not key_info:
            logger.warning(f"MCP request with invalid API key from {request.client.host}")
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or expired API key"},
            )

        # Add key info to request state for tools to access
        request.state.mcp_key_info = key_info

        return await call_next(request)


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
