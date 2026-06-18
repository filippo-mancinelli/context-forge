"""Per-organization runtime configuration (repositories + indexing settings).

Each organization owns an independent ``ForgeConfig`` persisted in
``org_runtime_config``. Infrastructure settings (providers, embeddings, LLM)
remain global in :mod:`runtime_state` because they are bound to the shared
vector store dimension.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from .config import ForgeConfig
from .db import get_pool

logger = logging.getLogger(__name__)

# Process-wide cache (single process runs both MCP and REST servers).
_cache: dict[int, ForgeConfig] = {}


def _decode(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    return {}


def invalidate(org_id: Optional[int] = None) -> None:
    if org_id is None:
        _cache.clear()
    else:
        _cache.pop(org_id, None)


async def load_org_config(org_id: int) -> ForgeConfig:
    """Load an organization's config from the DB (creating defaults if absent)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT forge_config FROM org_runtime_config WHERE org_id = $1", org_id
        )
    if row is None:
        cfg = ForgeConfig()
        await persist_org_config(org_id, cfg)
    else:
        cfg = ForgeConfig(**_decode(row["forge_config"]))
        _cache[org_id] = cfg
    return cfg


async def get_org_config(org_id: int) -> ForgeConfig:
    """Return the cached org config, loading it on first access."""
    cached = _cache.get(org_id)
    if cached is not None:
        return cached
    return await load_org_config(org_id)


async def persist_org_config(org_id: int, cfg: ForgeConfig) -> None:
    """Persist and cache an organization's config."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO org_runtime_config (org_id, forge_config, updated_at)
            VALUES ($1, $2::jsonb, NOW())
            ON CONFLICT (org_id) DO UPDATE
            SET forge_config = EXCLUDED.forge_config, updated_at = NOW()
            """,
            org_id,
            json.dumps(cfg.model_dump()),
        )
    _cache[org_id] = cfg


async def seed_org_config_if_absent(org_id: int, cfg: ForgeConfig) -> bool:
    """Seed an org's config only if it has none yet. Returns True when seeded."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM org_runtime_config WHERE org_id = $1", org_id
        )
        if exists:
            return False
    await persist_org_config(org_id, cfg)
    return True


async def all_org_ids() -> list[int]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id FROM organizations ORDER BY id")
    return [int(r["id"]) for r in rows]


async def iter_org_configs() -> list[tuple[int, ForgeConfig]]:
    """Return (org_id, config) for every organization."""
    result: list[tuple[int, ForgeConfig]] = []
    for org_id in await all_org_ids():
        result.append((org_id, await get_org_config(org_id)))
    return result
