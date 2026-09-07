import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src import projects
from src.api import deps
from src.api.routes import projects as project_routes


class _FakeConn:
    def __init__(self, fetchval=None, fetch=None):
        self._fetchval = fetchval
        self._fetch = fetch or []
        self.calls = []

    async def fetchval(self, query, *args):
        self.calls.append(("fetchval", query, args))
        return self._fetchval

    async def fetch(self, query, *args):
        self.calls.append(("fetch", query, args))
        return self._fetch

    async def execute(self, query, *args):
        self.calls.append(("execute", query, args))
        return "OK"


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def _wire(monkeypatch, *, org_role, project_role=None, projects_rows=None):
    async def fake_membership(org_id, user_id):
        return org_role

    monkeypatch.setattr(projects, "get_membership_role", fake_membership)
    conn = _FakeConn(fetchval=project_role, fetch=projects_rows or [])
    monkeypatch.setattr(projects, "get_pool", lambda: _fake_pool(conn))
    return conn


async def _fake_pool(conn):
    return _FakePool(conn)


# ── resolve_project_access ────────────────────────────────────────────────────

def test_org_admin_accesses_any_project_without_membership(monkeypatch):
    conn = _wire(monkeypatch, org_role="admin")
    role = asyncio.run(projects.resolve_project_access(1, 5, 42))
    assert role == "admin"
    # Nessuna query a project_members: l'accesso è implicito.
    assert conn.calls == []


def test_owner_accesses_any_project(monkeypatch):
    _wire(monkeypatch, org_role="owner")
    assert asyncio.run(projects.resolve_project_access(1, 5, 42)) == "owner"


def test_member_needs_explicit_enablement(monkeypatch):
    _wire(monkeypatch, org_role="member", project_role="viewer")
    assert asyncio.run(projects.resolve_project_access(1, 5, 42)) == "viewer"


def test_member_without_enablement_is_denied(monkeypatch):
    _wire(monkeypatch, org_role="member", project_role=None)
    assert asyncio.run(projects.resolve_project_access(1, 5, 42)) is None


def test_non_org_member_is_denied(monkeypatch):
    _wire(monkeypatch, org_role=None)
    assert asyncio.run(projects.resolve_project_access(1, 5, 42)) is None


def test_list_accessible_for_member_returns_joined_rows(monkeypatch):
    rows = [{"id": 4, "org_id": 1, "name": "askmeai-v2", "slug": "askmeai-v2",
             "memory_namespace": "acme--askmeai-v2", "created_at": None, "role": "member"}]
    _wire(monkeypatch, org_role="member", projects_rows=rows)
    out = asyncio.run(projects.list_accessible_projects(1, 5))
    assert [p["slug"] for p in out] == ["askmeai-v2"]
    assert out[0]["role"] == "member"


# ── route membri progetto ─────────────────────────────────────────────────────

ORG = deps.ActiveOrg(org_id=1, role="admin", namespace="acme", name="Acme")
ORG_OWNER = deps.ActiveOrg(org_id=1, role="owner", namespace="acme", name="Acme")


@pytest.fixture
def client(monkeypatch):
    added = {}

    async def fake_get_project(pid):
        return {"id": pid, "org_id": 1, "name": "P", "slug": "p", "memory_namespace": "acme--p"}

    async def fake_membership(org_id, user_id):
        return "member" if user_id == 12 else None

    async def fake_add(project_id, user_id, role):
        added.update(project_id=project_id, user_id=user_id, role=role)

    async def fake_email(email):
        return 12 if email == "bob@acme.io" else None

    monkeypatch.setattr(project_routes.projects, "get_project", fake_get_project)
    monkeypatch.setattr(project_routes.projects, "add_project_member", fake_add)
    monkeypatch.setattr(project_routes, "get_membership_role", fake_membership)
    monkeypatch.setattr(project_routes, "get_user_id_by_email", fake_email)

    app = FastAPI()
    app.include_router(project_routes.router)
    # require_role("admin") depends on get_active_org; overriding the latter with
    # an admin org is enough to satisfy the role gate.
    app.dependency_overrides[deps.get_active_org] = lambda: ORG
    app.dependency_overrides[deps.get_current_user_id] = lambda: 1
    return TestClient(app), added


def test_add_member_by_email_resolves_and_enables(client, monkeypatch):
    tc, added = client
    # Override with owner role since this test exercises role choice
    app = tc.app
    app.dependency_overrides[deps.get_active_org] = lambda: ORG_OWNER
    resp = tc.post("/projects/4/members", json={"email": "bob@acme.io", "role": "viewer"})
    assert resp.status_code == 200, resp.text
    assert added == {"project_id": 4, "user_id": 12, "role": "viewer"}


def test_add_member_rejects_non_org_user(client):
    tc, _ = client
    resp = tc.post("/projects/4/members", json={"user_id": 99, "role": "member"})
    assert resp.status_code == 400
    assert "not a member" in resp.json()["detail"].lower()


def test_add_member_rejects_bad_role(client):
    tc, _ = client
    resp = tc.post("/projects/4/members", json={"user_id": 12, "role": "owner"})
    assert resp.status_code == 400
