"""Runtime config persistence and bootstrap helpers."""
from __future__ import annotations

import logging
from typing import Any

from .config import ForgeConfig, set_runtime_config
from .db import get_pool

logger = logging.getLogger(__name__)


async def load_runtime_state() -> bool:
    """Load DB-backed runtime config and apply it in-memory."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT forge_config, settings_overrides FROM app_runtime_config WHERE id = 1"
        )
    if not row:
        return False

    forge_raw = row["forge_config"] or {}
    settings_overrides = row["settings_overrides"] or {}
    set_runtime_config(ForgeConfig(**forge_raw), settings_overrides)
    logger.info("Loaded runtime configuration from database")
    return True


async def save_runtime_state(forge_config: dict[str, Any], settings_overrides: dict[str, Any]) -> None:
    """Persist runtime config in DB."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_runtime_config (id, forge_config, settings_overrides, updated_at)
            VALUES (1, $1::jsonb, $2::jsonb, NOW())
            ON CONFLICT (id) DO UPDATE
            SET forge_config = EXCLUDED.forge_config,
                settings_overrides = EXCLUDED.settings_overrides,
                updated_at = NOW()
            """,
            forge_config,
            settings_overrides,
        )

