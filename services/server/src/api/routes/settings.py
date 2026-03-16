"""Runtime settings management routes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header
from pydantic import BaseModel, Field

from ...config import ForgeConfig, get_forge_config, get_settings, set_runtime_config
from ...indexer.embedder import reset_embedder_clients
from ...indexer.indexer import sync_repos_config
from ...mcp.memory import reset_memory_client
from ...runtime_state import save_runtime_state
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
        "settings_overrides": {
            "openai_api_key": settings.openai_api_key,
            "anthropic_api_key": settings.anthropic_api_key,
            "deepseek_api_key": settings.deepseek_api_key,
            "embeddings_provider": settings.embeddings_provider,
            "embeddings_model": settings.embeddings_model,
            "embeddings_dims": settings.embeddings_dims,
            "embeddings_api_key": settings.embeddings_api_key,
            "embeddings_base_url": settings.embeddings_base_url,
            "llm_provider": settings.llm_provider,
            "llm_model": settings.llm_model,
            "github_token": settings.github_token,
            "gitlab_token": settings.gitlab_token,
        },
    }


@router.put("")
async def update_runtime_settings(req: SettingsUpdateRequest, authorization: str | None = Header(default=None)):
    """Update runtime settings and apply them without manual file edits."""
    await require_valid_token_or_raise(authorization)
    forge = ForgeConfig(**req.forge_config)
    await save_runtime_state(forge.model_dump(), req.settings_overrides)
    set_runtime_config(forge, req.settings_overrides)
    reset_embedder_clients()
    reset_memory_client()
    await sync_repos_config()
    return {"status": "ok"}

