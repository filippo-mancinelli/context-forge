"""First-run onboarding routes."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...config import ForgeConfig, get_settings, RUNTIME_OVERRIDE_FIELDS
from ...indexer.indexer import sync_repos_config
from ...indexer.embedder import reset_embedder_clients
from ...mcp.memory import reset_memory_client
from ...runtime_state import persist_runtime_config, save_runtime_state, load_runtime_state
from ..security import create_admin_user, has_admin_user, has_runtime_config, is_configured, reset_admin_password

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/setup", tags=["setup"])


class SetupInitRequest(BaseModel):
    bootstrap_token: str
    admin_username: str = Field(min_length=3, max_length=64)
    admin_password: str = Field(min_length=8, max_length=256)
    forge_config: dict[str, Any] = Field(default_factory=dict)
    settings_overrides: dict[str, Any] = Field(default_factory=dict)


class PatchSettingsRequest(BaseModel):
    bootstrap_token: str
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

    # Provision the default organization with the first admin as owner and
    # migrate repo storage to tenant-aware composite identity.
    from ...tenancy import ensure_tenant_storage

    await ensure_tenant_storage()

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


@router.patch("/patch-settings")
async def patch_settings(req: PatchSettingsRequest):
    """Patch runtime settings_overrides in DB without re-running full setup.

    Useful to fix corrupted values imported from a malformed env before
    the admin account is created.  Blocked once an admin user exists.
    """
    if await has_admin_user():
        raise HTTPException(status_code=409, detail="Not available after admin setup is complete")
    if not await has_runtime_config():
        raise HTTPException(status_code=400, detail="No runtime config to patch; run full setup instead")

    expected_bootstrap = get_settings().setup_bootstrap_token
    if not expected_bootstrap:
        raise HTTPException(status_code=500, detail="SETUP_BOOTSTRAP_TOKEN is not configured")
    if req.bootstrap_token != expected_bootstrap:
        raise HTTPException(status_code=401, detail="Invalid bootstrap token")

    # Only allow known override fields.
    unknown = set(req.settings_overrides) - set(RUNTIME_OVERRIDE_FIELDS)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown settings fields: {sorted(unknown)}")

    # Load current state, merge the patch, persist and reload.
    loaded = await load_runtime_state()
    if not loaded:
        raise HTTPException(status_code=500, detail="Failed to load current runtime state")

    from ...config import _runtime_forge_config, _runtime_settings_overrides  # noqa: PLC0415
    current_forge = _runtime_forge_config
    current_overrides = dict(_runtime_settings_overrides)
    current_overrides.update(req.settings_overrides)

    await save_runtime_state(current_forge.model_dump() if current_forge else {}, current_overrides)
    await load_runtime_state()
    reset_embedder_clients()
    reset_memory_client()

    return {"status": "ok", "patched_fields": sorted(req.settings_overrides.keys())}


class ResetPasswordRequest(BaseModel):
    bootstrap_token: str
    admin_username: str = Field(min_length=3, max_length=64)
    new_password: str = Field(min_length=8, max_length=256)


@router.post("/reset-password")
async def reset_password(req: ResetPasswordRequest):
    """Reset admin password using the bootstrap token.

    Requires the SETUP_BOOTSTRAP_TOKEN from the environment to authorise
    the operation, so only someone with server-level access can trigger it.
    """
    expected_bootstrap = get_settings().setup_bootstrap_token
    if not expected_bootstrap:
        raise HTTPException(status_code=500, detail="SETUP_BOOTSTRAP_TOKEN is not configured")
    if req.bootstrap_token != expected_bootstrap:
        logger.warning("Password reset rejected: invalid bootstrap token")
        raise HTTPException(status_code=401, detail="Invalid bootstrap token")

    if not await has_admin_user():
        raise HTTPException(status_code=404, detail="No admin user exists; run setup first")

    await reset_admin_password(req.admin_username, req.new_password)
    logger.info("Admin password reset for username=%r", req.admin_username)
    return {"status": "ok", "message": f"Password reset for '{req.admin_username}'"}
