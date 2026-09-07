import asyncio

from src import config
from src.mcp import auth as mcp_auth


def test_claim_matches_org_slug(monkeypatch):
    monkeypatch.setattr(config.get_settings(), "oidc_org_claim", "tenant_id", raising=False)

    async def fake_by_slug(slug):
        assert slug == "lascaux"
        return {"id": 7, "name": "Lascaux", "slug": "lascaux", "memory_namespace": "ns-lascaux"}

    monkeypatch.setattr(mcp_auth, "get_organization_by_slug", fake_by_slug)
    org_id, ns = asyncio.run(mcp_auth._resolve_org_from_claims({"tenant_id": "lascaux"}))
    assert (org_id, ns) == (7, "ns-lascaux")


def test_missing_claim_falls_back_to_default(monkeypatch):
    async def fake_default():
        return 1

    async def fake_ns(org_id):
        return "ns-default"

    monkeypatch.setattr(mcp_auth, "ensure_default_org", fake_default)
    monkeypatch.setattr(mcp_auth, "get_namespace_for_org", fake_ns)
    org_id, ns = asyncio.run(mcp_auth._resolve_org_from_claims({}))
    assert (org_id, ns) == (1, "ns-default")
