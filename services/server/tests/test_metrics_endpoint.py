"""GET /metrics: declared metric names and METRICS_TOKEN enforcement."""
import pytest
from fastapi.testclient import TestClient

from src import config, metrics
from src.api.app import api


@pytest.fixture
def client(monkeypatch):
    settings = config.get_settings()
    monkeypatch.setattr(settings, "metrics_token", "", raising=False)
    return TestClient(api)


DECLARED = [
    "contextforge_jobs",
    "contextforge_index_requests_pending",
    "contextforge_index_requests_oldest_age_seconds",
    "contextforge_kb_documents_pending",
    "contextforge_web_pages_pending",
    "contextforge_scheduler_tick_timestamp_seconds",
    "contextforge_schema_version",
    "contextforge_mcp_tool_calls_total",
]


def test_metrics_exposes_every_declared_metric(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.text
    for name in DECLARED:
        assert name in body, name


def test_jobs_gauge_is_preinitialised_for_every_status(client):
    body = client.get("/metrics").text
    for status in ("pending", "running", "done", "error", "dead"):
        assert f'contextforge_jobs{{status="{status}"}}' in body


def test_metrics_content_type_is_the_prometheus_text_format(client):
    resp = client.get("/metrics")
    assert "text/plain" in resp.headers["content-type"]


def test_metrics_never_labels_by_organization():
    """Checked on the registry itself: a label with no children has no sample."""
    declared = {
        name
        for collector in metrics.registry._collector_to_names
        for name in getattr(collector, "_labelnames", ())
    }
    assert declared == {"status", "tool", "outcome"}


def test_open_when_no_token_is_configured(client):
    assert client.get("/metrics").status_code == 200


def test_token_is_required_when_configured(monkeypatch):
    settings = config.get_settings()
    monkeypatch.setattr(settings, "metrics_token", "s3cret", raising=False)
    client = TestClient(api)
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = client.get("/metrics", headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200
    assert "contextforge_jobs" in ok.text


def test_a_non_ascii_authorization_header_is_rejected_not_a_crash(monkeypatch):
    """hmac.compare_digest refuses non-ASCII str, so the comparison is on bytes."""
    settings = config.get_settings()
    monkeypatch.setattr(settings, "metrics_token", "s3cret", raising=False)
    client = TestClient(api, raise_server_exceptions=False)
    header = "Bearer s3crét".encode("utf-8")  # str headers must be ASCII for httpx
    resp = client.get("/metrics", headers={"Authorization": header})
    assert resp.status_code == 401


def test_metrics_is_not_behind_the_session_guard(monkeypatch):
    """The guard only covers /api/*; /metrics must answer without a session token."""
    settings = config.get_settings()
    monkeypatch.setattr(settings, "metrics_token", "", raising=False)
    assert TestClient(api).get("/metrics").status_code == 200
