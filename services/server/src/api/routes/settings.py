"""Runtime settings management routes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header
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
from ...runtime_state import persist_runtime_config
from ..security import require_valid_token_or_raise

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsUpdateRequest(BaseModel):
    forge_config: dict[str, Any]
    settings_overrides: dict[str, Any] = Field(default_factory=dict)


@router.get("")
async def get_runtime_settings(authorization: str | None = Header(default=None)):
    """Return current runtime settings (admin only)."""
    await require_valid_token_or_raise(authorization)
    forge = get_forge_config().model_dump()
    settings = get_settings()

    return {
        "forge_config": forge,
        "settings_overrides": {field: getattr(settings, field) for field in RUNTIME_OVERRIDE_FIELDS},
    }


@router.put("")
async def update_runtime_settings(req: SettingsUpdateRequest, authorization: str | None = Header(default=None)):
    """Update runtime settings and apply them without manual file edits."""
    await require_valid_token_or_raise(authorization)
    current_settings = get_settings()
    next_overrides = {
        field: req.settings_overrides.get(field, getattr(current_settings, field))
        for field in RUNTIME_OVERRIDE_FIELDS
    }

    embeddings_provider_changed = next_overrides["embeddings_provider"] != current_settings.embeddings_provider
    embeddings_model_changed = next_overrides["embeddings_model"] != current_settings.embeddings_model
    embeddings_dims_changed = next_overrides["embeddings_dims"] != current_settings.embeddings_dims

    warnings: list[str] = []
    if embeddings_provider_changed or embeddings_model_changed:
        warnings.append(
            "Embeddings provider/model changed. Re-index repositories so semantic search uses the new embeddings."
        )
    if embeddings_dims_changed:
        warnings.append(
            "Embedding dimensions changed. Existing vector data was indexed with the previous dimension. "
            "Reset vector-backed data, restart the stack, and re-index repositories before relying on search or memory."
        )

    forge = ForgeConfig(**req.forge_config)
    await persist_runtime_config(forge, req.settings_overrides)
    reset_embedder_clients()
    reset_memory_client()
    await sync_repos_config()
    return {
        "status": "ok",
        "warnings": warnings,
        "requires_reindex": bool(warnings),
        "requires_vector_reset": embeddings_dims_changed,
    }
