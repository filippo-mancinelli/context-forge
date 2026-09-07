import asyncio

import pytest

from src import config
from src.mcp import context
from src.mcp.permissions import ToolError


def _reset():
    context.set_current_org_id(1)
    context.set_current_project_id(None)


def test_no_fallback_when_auth_enabled(monkeypatch):
    _reset()
    monkeypatch.setattr(config.get_settings(), "mcp_auth_mode", "enabled", raising=False)

    async def fail_default(org_id):
        raise AssertionError("default project must not be consulted with auth enabled")

    import src.projects as projects_mod
    monkeypatch.setattr(projects_mod, "get_default_project_id", fail_default)
    assert asyncio.run(context.resolve_project_id()) is None


def test_fallback_survives_only_in_disabled_mode(monkeypatch):
    _reset()
    monkeypatch.setattr(config.get_settings(), "mcp_auth_mode", "disabled", raising=False)

    async def fake_default(org_id):
        return 3

    import src.projects as projects_mod
    monkeypatch.setattr(projects_mod, "get_default_project_id", fake_default)
    assert asyncio.run(context.resolve_project_id()) == 3


def test_require_project_id_raises_with_guidance(monkeypatch):
    _reset()
    monkeypatch.setattr(config.get_settings(), "mcp_auth_mode", "enabled", raising=False)
    with pytest.raises(ToolError) as exc:
        asyncio.run(context.require_project_id())
    assert "use_project" in str(exc.value)


def test_require_project_id_returns_selected(monkeypatch):
    context.set_current_org_id(1)
    context.set_current_project_id(7)
    assert asyncio.run(context.require_project_id()) == 7
