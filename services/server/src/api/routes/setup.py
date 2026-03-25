"""First-run onboarding routes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...config import ForgeConfig, get_settings
from ...indexer.indexer import sync_repos_config
from ...indexer.embedder import reset_embedder_clients
from ...mcp.memory import reset_memory_client
from ...runtime_state import persist_runtime_config
from ..security import create_admin_user, has_admin_user, has_runtime_config, is_configured

router = APIRouter(prefix="/setup", tags=["setup"])


class SetupInitRequest(BaseModel):
    bootstrap_token: str
    admin_username: str = Field(min_length=3, max_length=64)
    admin_password: str = Field(min_length=8, max_length=256)
    forge_config: dict[str, Any] = Field(default_factory=dict)
    settings_overrides: dict[str, Any] = Field(default_factory=dict)


@router.get("/status")
async def setup_status():
    """Report whether onboarding has already been completed."""
    has_admin = await has_admin_user()
    has_runtime = await has_runtime_config()
    configured = await is_configured()
    mode = "configured" if configured else "admin" if has_runtime else "full"
    return {
        "is_configured": configured,
        "mode": mode,
        "has_admin": has_admin,
        "has_runtime_config": has_runtime,
    }


@router.post("/init")
async def setup_init(req: SetupInitRequest):
    """Initialize admin and runtime settings on first boot."""
    if await has_admin_user():
        raise HTTPException(status_code=409, detail="Admin setup already completed")

    # Explicit bootstrap token from env is required for remote-safe setup.
    expected_bootstrap = get_settings().setup_bootstrap_token
    if not expected_bootstrap:
        raise HTTPException(status_code=500, detail="SETUP_BOOTSTRAP_TOKEN is not configured")
    if req.bootstrap_token != expected_bootstrap:
        raise HTTPException(status_code=401, detail="Invalid bootstrap token")

    await create_admin_user(req.admin_username, req.admin_password)

    created_runtime_config = False
    if not await has_runtime_config():
        forge = ForgeConfig(**req.forge_config)
        await persist_runtime_config(forge, req.settings_overrides)
        created_runtime_config = True

    reset_embedder_clients()
    reset_memory_client()
    await sync_repos_config()

    return {
        "status": "ok",
        "mode": "full" if created_runtime_config else "admin",
        "message": (
            "Onboarding completed"
            if created_runtime_config
            else "Admin account created. Imported runtime configuration is now active."
        ),
    }
