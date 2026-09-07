"""Public runtime configuration for the Web UI.

Exposes the handful of deploy-time values the frontend needs at runtime but
cannot know at build time (it ships as a static bundle). Kept unauthenticated
and non-sensitive on purpose.
"""
from __future__ import annotations

from fastapi import APIRouter

from ...config import get_settings

router = APIRouter(tags=["config"])


@router.get("/config")
async def public_config():
    """Public runtime config consumed by the UI.

    ``public_mcp_url`` is the externally reachable base of the MCP endpoint
    (behind the reverse proxy), so the UI shows the real address instead of
    guessing the internal MCP port.
    """
    s = get_settings()
    return {"public_mcp_url": (s.public_mcp_url or "").rstrip("/")}
