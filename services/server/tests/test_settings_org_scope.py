"""Test settings routes with per-organization overrides."""
import asyncio

import pytest
from fastapi import HTTPException

from src import org_settings
from src.api.deps import ActiveOrg
from src.api.routes import settings as settings_routes
from src.config import ForgeConfig
from src.org_settings import OrgSettings


def _org(role="admin"):
    return ActiveOrg(org_id=1, role=role, namespace="ns", name="Org")


def _patch(monkeypatch, current=None, probe_dims=1536):
    """Patch org_settings and config functions for test isolation."""
    if current is None:
        current = OrgSettings()

    async def mock_embed_text(text: str, org_id: int) -> list[float]:
        return [0.0] * probe_dims

    monkeypatch.setattr(settings_routes, "embed_text", mock_embed_text)

    async def mock_get_org_settings(org_id: int) -> OrgSettings:
        return current

    async def mock_persist_org_settings_overrides(org_id: int, overrides: dict) -> None:
        for k, v in overrides.items():
            if hasattr(current, k):
                setattr(current, k, v)

    async def mock_persist_org_config(org_id: int, config: ForgeConfig):
        pass

    async def mock_get_org_config(org_id: int):
        return ForgeConfig()

    async def mock_sync_repos_config(org_id: int):
        pass

    # Patch in org_settings module
    monkeypatch.setattr(org_settings, "get_org_settings", mock_get_org_settings)
    monkeypatch.setattr(
        org_settings, "persist_org_settings_overrides", mock_persist_org_settings_overrides
    )

    # Patch in settings_routes module (where they're imported)
    monkeypatch.setattr(settings_routes, "get_org_settings", mock_get_org_settings)
    monkeypatch.setattr(
        settings_routes, "persist_org_settings_overrides", mock_persist_org_settings_overrides
    )
    monkeypatch.setattr(settings_routes, "persist_org_config", mock_persist_org_config)
    monkeypatch.setattr(settings_routes, "get_org_config", mock_get_org_config)
    monkeypatch.setattr(settings_routes, "sync_repos_config", mock_sync_repos_config)
    monkeypatch.setattr(settings_routes, "reset_embedder_clients", lambda: None)
    monkeypatch.setattr(settings_routes, "reset_memory_client", lambda: None)

    async def mock_ensure_org_indexes(org_id: int, dims: int):
        return []

    monkeypatch.setattr(settings_routes, "ensure_org_indexes", mock_ensure_org_indexes)

    return current


def test_get_settings_reads_org_settings(monkeypatch):
    """GET /settings returns per-org settings_overrides."""
    current = _patch(monkeypatch)
    current.openai_api_key = "org-key-123"
    current.llm_model = "gpt-4o"

    async def run_test():
        result = await settings_routes.get_runtime_settings(org=_org("admin"))
        assert result["settings_overrides"]["openai_api_key"] == "org-key-123"
        assert result["settings_overrides"]["llm_model"] == "gpt-4o"
        assert result["settings_overrides_editable"] is True

    asyncio.run(run_test())


def test_get_settings_editable_flag_by_role(monkeypatch):
    """settings_overrides_editable reflects admin role."""
    _patch(monkeypatch)

    async def run_test():
        # Admin: editable
        result_admin = await settings_routes.get_runtime_settings(org=_org("admin"))
        assert result_admin["settings_overrides_editable"] is True

        # Member: not editable
        result_member = await settings_routes.get_runtime_settings(org=_org("member"))
        assert result_member["settings_overrides_editable"] is False

    asyncio.run(run_test())


def test_put_settings_admin_persists_overrides(monkeypatch):
    """PUT /settings by admin persists per-org overrides."""
    current = _patch(monkeypatch)

    req = settings_routes.SettingsUpdateRequest(
        forge_config={},
        settings_overrides={"llm_model": "gpt-4", "openai_api_key": "new-key"}
    )

    async def run_test():
        out = await settings_routes.update_runtime_settings(req=req, org=_org("admin"))

        # Verify persisted
        assert current.llm_model == "gpt-4"
        assert current.openai_api_key == "new-key"
        assert out["status"] == "ok"

    asyncio.run(run_test())


def test_put_settings_embeddings_change_warns(monkeypatch):
    """PUT /settings with embeddings change produces warning."""
    current = _patch(monkeypatch)
    current.embeddings_model = "text-embedding-3-small"
    current.embeddings_dims = 1536

    req = settings_routes.SettingsUpdateRequest(
        forge_config={},
        settings_overrides={"embeddings_model": "text-embedding-3-large"}
    )

    async def run_test():
        out = await settings_routes.update_runtime_settings(req=req, org=_org("admin"))

        assert "Embeddings" in " ".join(out["warnings"])
        assert out["requires_reindex"] is True

    asyncio.run(run_test())


def test_put_settings_secret_owned_by_another_org_raises_409(monkeypatch):
    """PUT /settings with a Telegram secret already owned by another org is rejected."""
    _patch(monkeypatch)

    async def mock_webhook_secret_owner(secret: str):
        return 2  # a different org than _org()'s org_id=1

    monkeypatch.setattr(settings_routes, "webhook_secret_owner", mock_webhook_secret_owner)

    req = settings_routes.SettingsUpdateRequest(
        forge_config={},
        settings_overrides={"telegram_webhook_secret": "shared-secret"}
    )

    async def run_test():
        with pytest.raises(HTTPException) as exc_info:
            await settings_routes.update_runtime_settings(req=req, org=_org("admin"))
        assert exc_info.value.status_code == 409

    asyncio.run(run_test())


def test_put_settings_own_secret_resave_allowed(monkeypatch):
    """Re-saving a Telegram secret already owned by the same org does not raise."""
    current = _patch(monkeypatch)

    async def mock_webhook_secret_owner(secret: str):
        return 1  # same org_id as _org()

    monkeypatch.setattr(settings_routes, "webhook_secret_owner", mock_webhook_secret_owner)

    req = settings_routes.SettingsUpdateRequest(
        forge_config={},
        settings_overrides={"telegram_webhook_secret": "own-secret"}
    )

    async def run_test():
        out = await settings_routes.update_runtime_settings(req=req, org=_org("admin"))
        assert out["status"] == "ok"
        assert current.telegram_webhook_secret == "own-secret"

    asyncio.run(run_test())


def test_put_settings_embeddings_dims_change_warns_reset(monkeypatch):
    """PUT /settings with embeddings dims change requires vector reset."""
    current = _patch(monkeypatch, probe_dims=1024)
    current.embeddings_dims = 1536

    req = settings_routes.SettingsUpdateRequest(
        forge_config={},
        settings_overrides={"embeddings_dims": 1024}
    )

    async def run_test():
        out = await settings_routes.update_runtime_settings(req=req, org=_org("admin"))

        assert out["requires_reindex"] is True
        assert out["requires_vector_reset"] is True

    asyncio.run(run_test())


def test_put_settings_rejects_dims_the_provider_does_not_return(monkeypatch):
    """A dimension the embedding model contradicts is refused and rolled back."""
    current = _patch(monkeypatch, probe_dims=1536)
    current.embeddings_dims = 1536
    current.embeddings_model = "text-embedding-3-small"

    req = settings_routes.SettingsUpdateRequest(
        forge_config={},
        settings_overrides={"embeddings_dims": 3072, "embeddings_model": "big-model"},
    )

    async def run_test():
        with pytest.raises(HTTPException) as exc_info:
            await settings_routes.update_runtime_settings(req=req, org=_org("admin"))
        assert exc_info.value.status_code == 400
        assert "1536" in exc_info.value.detail and "3072" in exc_info.value.detail
        # The previous overrides are persisted again, not left half applied.
        assert current.embeddings_dims == 1536
        assert current.embeddings_model == "text-embedding-3-small"

    asyncio.run(run_test())


def test_put_settings_keeps_the_save_when_the_probe_provider_is_down(monkeypatch):
    """Configuration must not depend on the provider being reachable."""
    current = _patch(monkeypatch)
    current.embeddings_model = "text-embedding-3-small"

    async def exploding_embed_text(text: str, org_id: int):
        raise RuntimeError("provider unreachable")

    monkeypatch.setattr(settings_routes, "embed_text", exploding_embed_text)

    req = settings_routes.SettingsUpdateRequest(
        forge_config={}, settings_overrides={"embeddings_model": "text-embedding-3-large"}
    )

    async def run_test():
        out = await settings_routes.update_runtime_settings(req=req, org=_org("admin"))
        assert out["status"] == "ok"
        assert current.embeddings_model == "text-embedding-3-large"

    asyncio.run(run_test())


def test_put_settings_rejects_a_non_numeric_dimension(monkeypatch):
    """A dimension that is not a positive int is refused before persisting."""
    current = _patch(monkeypatch)
    current.embeddings_dims = 1536

    req = settings_routes.SettingsUpdateRequest(
        forge_config={},
        settings_overrides={"embeddings_dims": "many", "llm_model": "gpt-4"},
    )

    async def run_test():
        with pytest.raises(HTTPException) as exc_info:
            await settings_routes.update_runtime_settings(req=req, org=_org("admin"))
        assert exc_info.value.status_code == 400
        assert current.embeddings_dims == 1536
        assert current.llm_model != "gpt-4"

    asyncio.run(run_test())
