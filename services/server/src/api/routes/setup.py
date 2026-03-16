"""First-run onboarding routes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...config import ForgeConfig, get_settings, set_runtime_config
from ...indexer.indexer import sync_repos_config
from ...indexer.embedder import reset_embedder_clients
from ...mcp.memory import reset_memory_client
from ...runtime_state import save_runtime_state
from ..security import create_admin_user, has_admin_user, has_runtime_config, is_configured, is_legacy_mode_available

router = APIRouter(prefix="/setup", tags=["setup"])


class SetupInitRequest(BaseModel):
    bootstrap_token: str
    admin_username: str = Field(min_length=3, max_length=64)
    admin_password: str = Field(min_length=8, max_length=256)
    forge_config: dict[str, Any]
    settings_overrides: dict[str, Any] = Field(default_factory=dict)


@router.get("/status")
async def setup_status():
    """Report whether onboarding has already been completed."""
    configured = await is_configured()
    legacy_mode = False
    if not configured:
        legacy_mode = await is_legacy_mode_available()
    return {
        "is_configured": configured,
        "legacy_mode": legacy_mode,
        "has_admin": await has_admin_user(),
        "has_runtime_config": await has_runtime_config(),
    }


@router.post("/init")
async def setup_init(req: SetupInitRequest):
    """Initialize admin and runtime settings on first boot."""
    if await is_configured():
        raise HTTPException(status_code=409, detail="Setup already completed")

    # Explicit bootstrap token from env is required for remote-safe setup.
    expected_bootstrap = get_settings().setup_bootstrap_token
    if not expected_bootstrap:
        raise HTTPException(status_code=500, detail="SETUP_BOOTSTRAP_TOKEN is not configured")
    if req.bootstrap_token != expected_bootstrap:
        raise HTTPException(status_code=401, detail="Invalid bootstrap token")

    forge = ForgeConfig(**req.forge_config)
    await create_admin_user(req.admin_username, req.admin_password)
    await save_runtime_state(forge.model_dump(), req.settings_overrides)
    set_runtime_config(forge, req.settings_overrides)
    reset_embedder_clients()
    reset_memory_client()
    await sync_repos_config()

    return {"status": "ok", "message": "Onboarding completed"}

