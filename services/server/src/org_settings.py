"""Risoluzione dei settings runtime per organizzazione.

Gli override dell'org (org_runtime_config.settings_overrides) si stratificano
sopra i default env/bootstrap di ``config.Settings``. Le chiavi infrastrutturali
(database, OIDC, encryption, auth-mode) restano solo env e non passano di qui.
"""
from __future__ import annotations

import hmac
import json
import logging
from typing import Optional

from pydantic import BaseModel

from .config import RUNTIME_OVERRIDE_FIELDS, get_settings
from .db import get_pool

logger = logging.getLogger(__name__)

# The Telegram channel now resolves its org from the webhook secret rather
# than a global override, so every RUNTIME_OVERRIDE_FIELDS entry is org-scoped.
ORG_OVERRIDE_FIELDS: tuple[str, ...] = RUNTIME_OVERRIDE_FIELDS


class OrgSettings(BaseModel):
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    deepseek_api_key: str = ""
    embeddings_provider: str = "openai"
    embeddings_model: str = "text-embedding-3-small"
    embeddings_dims: int = 1536
    embeddings_api_key: str = ""
    embeddings_base_url: str = ""
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"
    github_token: str = ""
    gitlab_token: str = ""
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    telegram_allowed_chat_ids: str = ""


_cache: dict[int, OrgSettings] = {}


async def _fetch_org_overrides(org_id: int) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        raw = await conn.fetchval(
            "SELECT settings_overrides FROM org_runtime_config WHERE org_id = $1",
            org_id,
        )
    if raw is None:
        return {}
    if isinstance(raw, str):
        return json.loads(raw)
    return dict(raw)


async def get_org_settings(org_id: int) -> OrgSettings:
    cached = _cache.get(org_id)
    if cached is not None:
        return cached
    base = get_settings()
    values = {field: getattr(base, field) for field in ORG_OVERRIDE_FIELDS}
    overrides = await _fetch_org_overrides(org_id)
    for key, value in overrides.items():
        if key in values and value is not None:
            values[key] = value
    resolved = OrgSettings(**values)
    _cache[org_id] = resolved
    return resolved


async def persist_org_settings_overrides(org_id: int, overrides: dict) -> None:
    clean = {k: v for k, v in overrides.items() if k in ORG_OVERRIDE_FIELDS}
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO org_runtime_config (org_id, settings_overrides, updated_at)
            VALUES ($1, $2::jsonb, NOW())
            ON CONFLICT (org_id) DO UPDATE
            SET settings_overrides = EXCLUDED.settings_overrides, updated_at = NOW()
            """,
            org_id,
            json.dumps(clean),
        )
    invalidate_org_settings(org_id)


def invalidate_org_settings(org_id: Optional[int] = None) -> None:
    if org_id is None:
        _cache.clear()
    else:
        _cache.pop(org_id, None)


async def _fetch_telegram_secrets() -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT org_id, settings_overrides->>'telegram_webhook_secret' AS secret
            FROM org_runtime_config
            WHERE COALESCE(settings_overrides->>'telegram_webhook_secret', '') <> ''
            """
        )
    return [dict(r) for r in rows]


async def resolve_org_by_webhook_secret(secret: str) -> Optional[int]:
    """Org whose Telegram channel registered this webhook secret.

    Fails closed on ambiguity: if more than one org matches the secret, no
    org can be reliably resolved, so ``None`` is returned rather than
    guessing (which risks routing messages to the wrong tenant).
    """
    if not secret:
        return None
    matches = [
        int(row["org_id"])
        for row in await _fetch_telegram_secrets()
        if hmac.compare_digest(str(row["secret"]), secret)
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        logger.warning("Ambiguous Telegram webhook secret matches multiple orgs: %s", matches)
    return None


async def webhook_secret_owner(secret: str) -> Optional[int]:
    """org_id that currently owns this telegram webhook secret, if any."""
    return await resolve_org_by_webhook_secret(secret)
