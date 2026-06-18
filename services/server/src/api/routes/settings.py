"""Runtime settings management routes.

Repository and indexing settings (``forge_config``) are per-organization.
Provider/embeddings/LLM settings (``settings_overrides``) are global because
they are bound to the shared vector store, and may only be changed by an
organization admin.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...config import (
    ForgeConfig,
    RUNTIME_OVERRIDE_FIELDS,
    get_forge_config,
    get_settings,
)
from ...indexer.embedder import reset_embedder_clients
from ...indexer.indexer import sync_repos_config
from ...mcp.memory import reset_memory_client
from ...org_config import get_org_config, persist_org_config
from ...runtime_state import persist_runtime_config
from ...tenancy import role_at_least
from ..deps import ActiveOrg, get_active_org, require_role

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsUpdateRequest(BaseModel):
    forge_config: dict[str, Any]
    settings_overrides: dict[str, Any] = Field(default_factory=dict)


@router.get("")
async def get_runtime_settings(org: ActiveOrg = Depends(get_active_org)):
    """Return the active organization's config plus global model settings."""
    forge = (await get_org_config(org.org_id)).model_dump()
    settings = get_settings()
    return {
        "forge_config": forge,
        "settings_overrides": {field: getattr(settings, field) for field in RUNTIME_OVERRIDE_FIELDS},
        "settings_overrides_editable": role_at_least(org.role, "admin"),
    }


@router.put("")
async def update_runtime_settings(
    req: SettingsUpdateRequest, org: ActiveOrg = Depends(require_role("member"))
):
    """Update the active org's repos/indexing and (admin only) global settings."""
    warnings: list[str] = []

    # --- Per-organization forge config (repos + indexing) ---
    forge = ForgeConfig(**req.forge_config)
    await persist_org_config(org.org_id, forge)
    await sync_repos_config(org.org_id)
    # Re-apply per-org schedule changes to the scheduler.
    try:
        from ...scheduler import sync_scheduler_jobs

        await sync_scheduler_jobs()
    except Exception:
        pass

    # --- Global model/provider settings (admin only) ---
    current_settings = get_settings()
    next_overrides = {
        field: req.settings_overrides.get(field, getattr(current_settings, field))
        for field in RUNTIME_OVERRIDE_FIELDS
    }
    overrides_changed = any(
        next_overrides[field] != getattr(current_settings, field)
        for field in RUNTIME_OVERRIDE_FIELDS
    )

    embeddings_dims_changed = False
    if overrides_changed:
        if not role_at_least(org.role, "admin"):
            raise HTTPException(
                status_code=403,
                detail="Changing global model/provider settings requires admin role",
            )
        embeddings_provider_changed = (
            next_overrides["embeddings_provider"] != current_settings.embeddings_provider
        )
        embeddings_model_changed = (
            next_overrides["embeddings_model"] != current_settings.embeddings_model
        )
        embeddings_dims_changed = (
            next_overrides["embeddings_dims"] != current_settings.embeddings_dims
        )
        if embeddings_provider_changed or embeddings_model_changed:
            warnings.append(
                "Embeddings provider/model changed. Re-index repositories so semantic search uses the new embeddings."
            )
        if embeddings_dims_changed:
            warnings.append(
                "Embedding dimensions changed. Existing vector data was indexed with the previous dimension. "
                "Reset vector-backed data, restart the stack, and re-index repositories before relying on search or memory."
            )
        # Persist global overrides (the global forge_config column is retained as-is).
        await persist_runtime_config(get_forge_config(), next_overrides)
        reset_embedder_clients()
        reset_memory_client()

    return {
        "status": "ok",
        "warnings": warnings,
        "requires_reindex": bool(warnings),
        "requires_vector_reset": embeddings_dims_changed,
    }
