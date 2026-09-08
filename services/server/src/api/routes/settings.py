"""Runtime settings management routes.

Both ``forge_config`` (repos + indexing) and ``settings_overrides`` (providers,
models, embeddings, tokens, Telegram) are per-organization; changing
requires admin role in active organization. Changing embeddings
configuration requires re-embedding organization's content (see
org_reembed job).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...config import ForgeConfig
from ...indexer.embedder import embed_text, reset_embedder_clients
from ...org_settings import (
    ORG_OVERRIDE_FIELDS,
    get_org_settings,
    persist_org_settings_overrides,
    webhook_secret_owner,
)
from ...indexer.indexer import sync_repos_config
from ...tenancy import role_at_least
from ...mcp.memory import reset_memory_client
from ...org_config import get_org_config, persist_org_config
from ...vector_index import ensure_org_indexes
from ..deps import ActiveOrg, get_active_org, require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])

_background_tasks: set = set()


def _schedule(coro) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _positive_dims(value: Any) -> int:
    """embeddings_dims as a positive int, or 400: it is interpolated into DDL."""
    try:
        dims = int(value)
    except (TypeError, ValueError):
        dims = 0
    if dims <= 0:
        raise HTTPException(
            status_code=400, detail="embeddings_dims must be a positive integer"
        )
    return dims


async def _probe_embedding_dims(org_id: int) -> int | None:
    """Width of one vector from the configured provider; None if it is unreachable."""
    try:
        return len(await embed_text("dimension probe", org_id))
    except Exception:
        logger.warning(
            "Embeddings probe failed (org=%s): dimension left unverified", org_id
        )
        return None


class SettingsUpdateRequest(BaseModel):
    forge_config: dict[str, Any]
    settings_overrides: dict[str, Any] = Field(default_factory=dict)


@router.get("")
async def get_runtime_settings(org: ActiveOrg = Depends(get_active_org)):
    """Return the active organization's config plus per-org model settings."""
    forge = (await get_org_config(org.org_id)).model_dump()
    settings = await get_org_settings(org.org_id)
    return {
        "forge_config": forge,
        "settings_overrides": {field: getattr(settings, field) for field in ORG_OVERRIDE_FIELDS},
        "settings_overrides_editable": role_at_least(org.role, "admin"),
    }


@router.put("")
async def update_runtime_settings(
    req: SettingsUpdateRequest, org: ActiveOrg = Depends(require_role("admin"))
):
    """Update the active org's repos/indexing and per-org model/provider settings."""
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

    # --- Per-organization model/provider settings (admin only) ---
    current = await get_org_settings(org.org_id)
    next_overrides = {
        field: req.settings_overrides.get(field, getattr(current, field))
        for field in ORG_OVERRIDE_FIELDS
    }
    if next_overrides.get("telegram_webhook_secret"):
        owner = await webhook_secret_owner(next_overrides["telegram_webhook_secret"])
        if owner is not None and owner != org.org_id:
            raise HTTPException(
                status_code=409,
                detail="This Telegram webhook secret is already used by another organization",
            )
    dims = _positive_dims(next_overrides.get("embeddings_dims"))
    overrides_changed = any(
        next_overrides[field] != getattr(current, field)
        for field in ORG_OVERRIDE_FIELDS
    )

    embeddings_dims_changed = False
    requires_reembed = False
    if overrides_changed:
        embeddings_dims_changed = (
            next_overrides["embeddings_dims"] != current.embeddings_dims
        )
        requires_reembed = embeddings_dims_changed or any(
            next_overrides[f] != getattr(current, f)
            for f in ("embeddings_provider", "embeddings_model", "embeddings_base_url")
        )
        if requires_reembed:
            warnings.append(
                "Embeddings configuration changed organization. "
                "Run re-embed job so search memory use new embeddings."
            )
        embeddings_changed = embeddings_dims_changed or any(
            next_overrides[f] != getattr(current, f)
            for f in ("embeddings_provider", "embeddings_model",
                      "embeddings_base_url", "embeddings_api_key")
        )
        previous_overrides = {f: getattr(current, f) for f in ORG_OVERRIDE_FIELDS}
        await persist_org_settings_overrides(org.org_id, next_overrides)
        reset_embedder_clients()
        reset_memory_client()
        if embeddings_changed:
            probed = await _probe_embedding_dims(org.org_id)
            if probed is not None and probed != dims:
                await persist_org_settings_overrides(org.org_id, previous_overrides)
                reset_embedder_clients()
                reset_memory_client()
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Embedding model returned {probed} dimensions, "
                        f"but embeddings_dims is {dims}"
                    ),
                )
        # A changed dimension is handled by the re-embed, which ensures too.
        if not embeddings_dims_changed:
            _schedule(ensure_org_indexes(org.org_id, dims))

    return {
        "status": "ok",
        "warnings": warnings,
        "requires_reindex": requires_reembed,
        "requires_vector_reset": embeddings_dims_changed,
    }


@router.post("/reembed")
async def start_reembed(org: ActiveOrg = Depends(require_role("admin"))):
    """Avvia il ricalcolo degli embedding dell'organizzazione (job asincrono)."""
    from ...db import get_pool
    from ...projects import get_default_project_id
    from ...reembed import reembed_org

    project_id = await get_default_project_id(org.org_id)
    pool = await get_pool()
    async with pool.acquire() as conn:
        job_id = await conn.fetchval(
            "INSERT INTO jobs (tool, status, org_id, project_id) "
            "VALUES ('org_reembed', 'pending', $1, $2) RETURNING id",
            org.org_id, project_id,
        )
    _schedule(reembed_org(org.org_id, str(job_id)))
    return {"status": "ok", "job_id": str(job_id)}
