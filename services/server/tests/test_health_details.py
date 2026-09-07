"""GET /api/health/details: shape, degraded rules, and that it is not public."""
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import deps
from src.api.routes import health as health_routes

ADMIN = deps.ActiveOrg(org_id=1, role="admin", namespace="acme", name="Acme")

STATS = {
    "jobs_pending": 2.0,
    "jobs_running": 1.0,
    "jobs_done": 10.0,
    "jobs_error": 0.0,
    "jobs_dead": 3.0,
    "index_requests_pending": 4.0,
    "index_requests_oldest_age_seconds": 12.0,
    "kb_documents_pending": 5.0,
    "web_pages_pending": 6.0,
}


class FakeConn:
    async def fetchval(self, sql, *args):
        return 1


class FakeAcquire:
    async def __aenter__(self):
        return FakeConn()

    async def __aexit__(self, *exc):
        return False


class FakePool:
    def acquire(self):
        return FakeAcquire()


def _wire(monkeypatch, *, stats=None, tick=None, running=True, db_ok=True):
    async def fake_pool():
        if not db_ok:
            raise RuntimeError("connection refused")
        return FakePool()

    async def fake_stats():
        return dict(stats if stats is not None else STATS)

    async def fake_version(pool):
        return 2

    monkeypatch.setattr(health_routes, "get_pool", fake_pool)
    monkeypatch.setattr(health_routes, "collect_queue_stats", fake_stats)
    monkeypatch.setattr(health_routes, "current_version", fake_version)
    monkeypatch.setattr(
        health_routes, "last_scheduler_tick",
        lambda: time.time() if tick is None else tick,
    )
    monkeypatch.setattr(health_routes, "is_scheduler_running", lambda: running)

    app = FastAPI()
    app.include_router(health_routes.router, prefix="/api")
    app.dependency_overrides[deps.get_active_org] = lambda: ADMIN
    return TestClient(app)


def test_healthy_shape(monkeypatch):
    resp = _wire(monkeypatch).get("/api/health/details")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"]["ok"] is True
    assert isinstance(body["database"]["latency_ms"], int)
    assert body["schema_version"] == 2
    assert body["scheduler"]["running"] is True
    assert body["scheduler"]["last_tick_at"].endswith("+00:00")
    assert body["queues"] == {
        "jobs_pending": 2,
        "jobs_running": 1,
        "jobs_dead": 3,
        "index_requests_pending": 4,
        "index_requests_oldest_age_seconds": 12,
        "kb_documents_pending": 5,
        "web_pages_pending": 6,
    }


def test_database_failure_is_degraded(monkeypatch):
    body = _wire(monkeypatch, db_ok=False).get("/api/health/details").json()
    assert body["status"] == "degraded"
    assert body["database"] == {"ok": False, "latency_ms": 0}


def test_stale_scheduler_tick_is_degraded(monkeypatch):
    body = _wire(monkeypatch, tick=time.time() - 61).get("/api/health/details").json()
    assert body["status"] == "degraded"


def test_recent_scheduler_tick_is_healthy(monkeypatch):
    body = _wire(monkeypatch, tick=time.time() - 59).get("/api/health/details").json()
    assert body["status"] == "ok"


def test_never_ticked_is_degraded(monkeypatch):
    body = _wire(monkeypatch, tick=0).get("/api/health/details").json()
    assert body["status"] == "degraded"


def test_old_index_queue_is_degraded(monkeypatch):
    stats = dict(STATS, index_requests_oldest_age_seconds=3601.0)
    body = _wire(monkeypatch, stats=stats).get("/api/health/details").json()
    assert body["status"] == "degraded"
    assert body["queues"]["index_requests_oldest_age_seconds"] == 3601


def test_index_queue_at_the_threshold_is_healthy(monkeypatch):
    stats = dict(STATS, index_requests_oldest_age_seconds=3600.0)
    body = _wire(monkeypatch, stats=stats).get("/api/health/details").json()
    assert body["status"] == "ok"


def test_members_are_rejected(monkeypatch):
    client = _wire(monkeypatch)
    client.app.dependency_overrides[deps.get_active_org] = lambda: deps.ActiveOrg(
        org_id=1, role="member", namespace="acme", name="Acme"
    )
    assert client.get("/api/health/details").status_code == 403


def test_owners_are_admitted(monkeypatch):
    client = _wire(monkeypatch)
    client.app.dependency_overrides[deps.get_active_org] = lambda: deps.ActiveOrg(
        org_id=1, role="owner", namespace="acme", name="Acme"
    )
    assert client.get("/api/health/details").status_code == 200


# ── the auth guard must not treat /api/health as a prefix ─────────────────────

def test_details_is_behind_the_session_guard(monkeypatch):
    from src.api import security as api_security
    from src.api.app import api

    async def fake_is_configured():
        return True

    monkeypatch.setattr(api_security, "is_configured", fake_is_configured)
    resp = TestClient(api).get("/api/health/details")
    assert resp.status_code == 401


def test_public_health_stays_open():
    from src.api.app import api

    resp = TestClient(api).get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "context-forge-api"}
