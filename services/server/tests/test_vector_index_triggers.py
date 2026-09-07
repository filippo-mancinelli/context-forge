"""Boot, re-embed and settings-save all refresh the HNSW indexes."""
import asyncio
import inspect

from src import main as main_module
from src import reembed
from src.api.deps import ActiveOrg
from src.api.routes import settings as settings_routes
from src.config import ForgeConfig
from src.org_settings import OrgSettings


def test_boot_schedules_index_maintenance_after_tenant_storage():
    src = inspect.getsource(main_module.main)
    assert "ensure_all_indexes" in src
    assert src.index("ensure_tenant_storage()") < src.index("ensure_all_indexes()")
    # It must not block boot: the ensure runs as a background task.
    assert "asyncio.create_task(ensure_all_indexes())" in src


def test_reembed_ensures_the_indexes_once_with_the_org_dimension(monkeypatch):
    calls = []

    async def fake_iter_chunks(table, org_id, batch_size):
        yield [(10, "hello")]

    async def fake_embed_batch(texts, org_id):
        return [[0.1] * 4 for _ in texts]

    async def fake_update(table, pairs, org_id):
        pass

    async def fake_job_update(job_id, status, result=None, error=None):
        pass

    async def fake_org_settings(org_id):
        return OrgSettings(embeddings_dims=3072)

    async def fake_ensure(org_id, dims):
        calls.append((org_id, dims))
        return []

    monkeypatch.setattr(reembed, "_iter_chunks", fake_iter_chunks)
    monkeypatch.setattr(reembed, "embed_batch", fake_embed_batch)
    monkeypatch.setattr(reembed, "_update_embeddings", fake_update)
    monkeypatch.setattr(reembed, "_set_job_status", fake_job_update)
    monkeypatch.setattr(reembed, "get_org_settings", fake_org_settings)
    monkeypatch.setattr(reembed, "ensure_org_indexes", fake_ensure)

    asyncio.run(reembed.reembed_org(5, "job-1"))

    assert calls == [(5, 3072)]


def _patch_settings(monkeypatch, current, calls):
    async def mock_get_org_settings(org_id):
        return current

    async def mock_persist_overrides(org_id, overrides):
        for k, v in overrides.items():
            if hasattr(current, k):
                setattr(current, k, v)

    async def mock_persist_org_config(org_id, config):
        pass

    async def mock_get_org_config(org_id):
        return ForgeConfig()

    async def mock_sync_repos_config(org_id):
        pass

    async def fake_ensure(org_id, dims):
        calls.append((org_id, dims))
        return []

    monkeypatch.setattr(settings_routes, "get_org_settings", mock_get_org_settings)
    monkeypatch.setattr(settings_routes, "persist_org_settings_overrides", mock_persist_overrides)
    monkeypatch.setattr(settings_routes, "persist_org_config", mock_persist_org_config)
    monkeypatch.setattr(settings_routes, "get_org_config", mock_get_org_config)
    monkeypatch.setattr(settings_routes, "sync_repos_config", mock_sync_repos_config)
    monkeypatch.setattr(settings_routes, "reset_embedder_clients", lambda: None)
    monkeypatch.setattr(settings_routes, "reset_memory_client", lambda: None)
    monkeypatch.setattr(settings_routes, "ensure_org_indexes", fake_ensure)


def _org():
    return ActiveOrg(org_id=1, role="admin", namespace="ns", name="Org")


def test_settings_save_without_a_dimension_change_ensures_the_indexes(monkeypatch):
    calls = []
    current = OrgSettings(embeddings_dims=1536)
    _patch_settings(monkeypatch, current, calls)

    req = settings_routes.SettingsUpdateRequest(
        forge_config={}, settings_overrides={"embeddings_model": "text-embedding-3-large"}
    )

    async def run():
        await settings_routes.update_runtime_settings(req=req, org=_org())
        await asyncio.sleep(0)  # let the background task run

    asyncio.run(run())
    assert calls == [(1, 1536)]


def test_settings_save_with_a_dimension_change_leaves_it_to_the_reembed(monkeypatch):
    calls = []
    current = OrgSettings(embeddings_dims=1536)
    _patch_settings(monkeypatch, current, calls)

    req = settings_routes.SettingsUpdateRequest(
        forge_config={}, settings_overrides={"embeddings_dims": 1024}
    )

    async def run():
        out = await settings_routes.update_runtime_settings(req=req, org=_org())
        await asyncio.sleep(0)
        assert out["requires_vector_reset"] is True

    asyncio.run(run())
    assert calls == []
