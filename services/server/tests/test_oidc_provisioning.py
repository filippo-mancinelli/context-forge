# services/server/tests/test_oidc_provisioning.py
import asyncio

from src.api import security


class FakeConn:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    async def fetchrow(self, query, *args):
        return self.rows.pop(0) if self.rows else None

    async def fetchval(self, query, *args):
        self.executed.append((query, args))
        return 42

    async def execute(self, query, *args):
        self.executed.append((query, args))


class FakeAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return FakeAcquire(self.conn)


def test_new_oidc_user_created_without_org_side_effects(monkeypatch):
    """Login provisioning only creates the user record. Organization membership
    is owned by the application, never derived from token claims — so no
    organization is resolved from the claim and no auto-enrollment happens."""
    conn = FakeConn(rows=[None, None])  # no oidc_sub match, no username match

    async def fake_pool():
        return FakePool(conn)

    monkeypatch.setattr(security, "get_pool", fake_pool)

    claims = {"sub": "kc-1", "preferred_username": "mario.rossi",
              "email": "mario@lascaux.it", "tenant_id": "askme",
              "groups": ["/mcp-tools/admin"]}
    user_id = asyncio.run(security.provision_oidc_user(claims))
    assert user_id == 42
    # No membership was written: the only DB writes are the user INSERT itself.
    assert all("organization_members" not in q for q, _ in conn.executed)
