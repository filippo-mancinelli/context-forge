"""Tests for per-organization settings overrides."""
from __future__ import annotations

import asyncio

import pytest

from src import org_settings
from src.org_settings import OrgSettings, get_org_settings, invalidate_org_settings


def test_org_override_fields_exclude_telegram_org_id():
    assert "telegram_org_id" not in org_settings.ORG_OVERRIDE_FIELDS
    assert "openai_api_key" in org_settings.ORG_OVERRIDE_FIELDS
    assert set(org_settings.ORG_OVERRIDE_FIELDS) == set(OrgSettings.model_fields)


def test_org_overrides_layer_over_env_defaults(monkeypatch):
    invalidate_org_settings()

    async def fake_fetch(org_id):
        assert org_id == 1
        return {"llm_model": "gpt-5-mini", "embeddings_dims": 1024}

    monkeypatch.setattr(org_settings, "_fetch_org_overrides", fake_fetch)
    s = asyncio.run(get_org_settings(1))
    assert s.llm_model == "gpt-5-mini"
    assert s.embeddings_dims == 1024
    # Campo non overridato: resta il default di bootstrap.
    assert s.embeddings_provider == "openai"


def test_org_settings_cached_until_invalidated(monkeypatch):
    invalidate_org_settings()
    calls = []

    async def fake_fetch(org_id):
        calls.append(org_id)
        return {}

    monkeypatch.setattr(org_settings, "_fetch_org_overrides", fake_fetch)
    asyncio.run(get_org_settings(2))
    asyncio.run(get_org_settings(2))
    assert calls == [2]
    invalidate_org_settings(2)
    asyncio.run(get_org_settings(2))
    assert calls == [2, 2]
