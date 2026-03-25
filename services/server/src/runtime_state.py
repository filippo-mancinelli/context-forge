"""Runtime config persistence and bootstrap helpers."""
from __future__ import annotations

import json
import logging
from typing import Any

from .config import (
    ForgeConfig,
    get_forge_config,
    get_runtime_settings_overrides,
    has_meaningful_file_forge_config,
    get_non_default_runtime_settings_overrides,
    set_runtime_config,
)
from .db import get_pool

logger = logging.getLogger(__name__)


def _decode_jsonb_field(value: Any) -> dict[str, Any]:
    """Normalize asyncpg json/jsonb values to dictionaries."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    raise TypeError(f"Expected JSON object mapping, got {type(value).__name__}")


async def load_runtime_state() -> bool:
    """Load DB-backed runtime config and apply it in-memory."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT forge_config, settings_overrides FROM app_runtime_config WHERE id = 1"
        )
    if not row:
        return False

    forge_raw = _decode_jsonb_field(row["forge_config"])
    settings_overrides = _decode_jsonb_field(row["settings_overrides"])
    set_runtime_config(ForgeConfig(**forge_raw), settings_overrides)
    logger.info("Loaded runtime configuration from database")
    return True


async def ensure_runtime_state() -> bool:
    """Load runtime config from DB or bootstrap it from legacy defaults."""
    if await load_runtime_state():
        return True

    has_bootstrap_config = has_meaningful_file_forge_config()
    non_default_overrides = get_non_default_runtime_settings_overrides()
    if not has_bootstrap_config and not non_default_overrides:
        return False

    forge_config = get_forge_config()
    settings_overrides = get_runtime_settings_overrides()
    await save_runtime_state(forge_config.model_dump(), settings_overrides)
    set_runtime_config(forge_config, settings_overrides)
    logger.info(
        "Imported bootstrap configuration into runtime database "
        "(file_config=%s, non_default_overrides=%s)",
        has_bootstrap_config,
        bool(non_default_overrides),
    )
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
            json.dumps(forge_config),
            json.dumps(settings_overrides),
        )


async def persist_runtime_config(
    forge_config: ForgeConfig,
    settings_overrides: dict[str, Any] | None = None,
) -> None:
    """Persist and apply runtime config using current overrides by default."""
    overrides = settings_overrides or get_runtime_settings_overrides()
    await save_runtime_state(forge_config.model_dump(), overrides)
    set_runtime_config(forge_config, overrides)
