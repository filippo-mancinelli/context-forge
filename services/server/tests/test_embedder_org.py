import asyncio

from src.indexer import embedder
from src.org_settings import OrgSettings


class _FakeClient:
    def __init__(self, tag):
        self.tag = tag


def test_client_cache_is_per_org_config(monkeypatch):
    embedder.reset_embedder_clients()
    created = []

    def fake_factory(api_key, base_url):
        created.append((api_key, base_url))
        return _FakeClient(len(created))

    async def fake_org_settings(org_id):
        if org_id == 1:
            return OrgSettings(openai_api_key="k1")
        return OrgSettings(openai_api_key="k2", embeddings_base_url="https://alt.example")

    monkeypatch.setattr(embedder, "_make_client", fake_factory)
    monkeypatch.setattr(embedder, "get_org_settings", fake_org_settings)

    c1 = asyncio.run(embedder._get_api_client(1))
    c1_bis = asyncio.run(embedder._get_api_client(1))
    c2 = asyncio.run(embedder._get_api_client(2))
    assert c1 is c1_bis
    assert c1 is not c2
    assert created == [("k1", None), ("k2", "https://alt.example")]


def test_reset_clears_cache(monkeypatch):
    embedder.reset_embedder_clients()

    def fake_factory(api_key, base_url):
        return _FakeClient(api_key)

    async def fake_org_settings(org_id):
        return OrgSettings(openai_api_key="k1")

    monkeypatch.setattr(embedder, "_make_client", fake_factory)
    monkeypatch.setattr(embedder, "get_org_settings", fake_org_settings)
    a = asyncio.run(embedder._get_api_client(1))
    embedder.reset_embedder_clients(1)
    b = asyncio.run(embedder._get_api_client(1))
    assert a is not b
