import asyncio

from src import org_settings


def test_webhook_secret_resolves_org(monkeypatch):
    async def fake_rows():
        return [
            {"org_id": 1, "secret": "s-one"},
            {"org_id": 2, "secret": "s-two"},
        ]

    monkeypatch.setattr(org_settings, "_fetch_telegram_secrets", fake_rows)
    assert asyncio.run(org_settings.resolve_org_by_webhook_secret("s-two")) == 2
    assert asyncio.run(org_settings.resolve_org_by_webhook_secret("nope")) is None
    assert asyncio.run(org_settings.resolve_org_by_webhook_secret("")) is None


def test_webhook_secret_ambiguous_match_fails_closed(monkeypatch):
    """Two orgs sharing the same secret must resolve to no org, not the first row."""

    async def fake_rows():
        return [
            {"org_id": 1, "secret": "shared-secret"},
            {"org_id": 2, "secret": "shared-secret"},
        ]

    monkeypatch.setattr(org_settings, "_fetch_telegram_secrets", fake_rows)
    assert asyncio.run(org_settings.resolve_org_by_webhook_secret("shared-secret")) is None
