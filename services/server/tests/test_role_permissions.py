# services/server/tests/test_role_permissions.py
import asyncio

from src import tenancy


class FakeConn:
    def __init__(self, fetch_rows=None):
        self.fetch_rows = fetch_rows if fetch_rows is not None else []
        self.executed = []

    async def fetch(self, query, *args):
        return self.fetch_rows

    async def execute(self, query, *args):
        self.executed.append((query, args))

    def transaction(self):
        class _Tx:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *exc):
                return False

        return _Tx()


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


def _patch_pool(monkeypatch, conn):
    async def fake_pool():
        return FakePool(conn)

    monkeypatch.setattr(tenancy, "get_pool", fake_pool)


def test_resolve_falls_back_to_defaults(monkeypatch):
    _patch_pool(monkeypatch, FakeConn(fetch_rows=[]))
    perms = asyncio.run(tenancy.resolve_role_permissions(1, "member"))
    assert perms == frozenset({"context-read", "context-write", "db-query", "ssh-read"})


def test_resolve_uses_customized_rows(monkeypatch):
    rows = [
        {"role": "member", "permission": "context-read"},
        {"role": "member", "permission": "jobs"},
    ]
    _patch_pool(monkeypatch, FakeConn(fetch_rows=rows))
    perms = asyncio.run(tenancy.resolve_role_permissions(1, "member"))
    assert perms == frozenset({"context-read", "jobs"})


def test_resolve_customized_org_role_with_no_rows_is_empty(monkeypatch):
    """Once an org has ANY customized rows, a role absent from that customization is
    explicitly empty (revoked) — it must NOT fall back to DEFAULT_ROLE_PERMISSIONS."""
    rows = [{"role": "viewer", "permission": "context-read"}]
    _patch_pool(monkeypatch, FakeConn(fetch_rows=rows))
    perms = asyncio.run(tenancy.resolve_role_permissions(1, "member"))
    assert perms == frozenset()


def test_resolve_without_org_or_role_is_empty(monkeypatch):
    _patch_pool(monkeypatch, FakeConn())
    assert asyncio.run(tenancy.resolve_role_permissions(None, "member")) == frozenset()
    assert asyncio.run(tenancy.resolve_role_permissions(1, None)) == frozenset()
    # ruolo sconosciuto e nessuna personalizzazione -> nessun permesso
    assert asyncio.run(tenancy.resolve_role_permissions(1, "ghost")) == frozenset()


def test_get_org_role_permissions_groups_by_role(monkeypatch):
    rows = [
        {"role": "viewer", "permission": "context-read"},
        {"role": "member", "permission": "context-read"},
        {"role": "member", "permission": "db-query"},
    ]
    _patch_pool(monkeypatch, FakeConn(fetch_rows=rows))
    out = asyncio.run(tenancy.get_org_role_permissions(1))
    assert out == {"viewer": ["context-read"], "member": ["context-read", "db-query"]}


def test_set_org_role_permissions_replaces_matrix(monkeypatch):
    conn = FakeConn()
    _patch_pool(monkeypatch, conn)
    asyncio.run(
        tenancy.set_org_role_permissions(7, {"viewer": ["context-read"], "admin": ["*"]})
    )
    # prima DELETE dell'org, poi una INSERT per ogni (ruolo, permesso)
    assert "DELETE FROM org_role_permissions" in conn.executed[0][0]
    assert conn.executed[0][1] == (7,)
    inserts = [e for e in conn.executed[1:] if "INSERT INTO org_role_permissions" in e[0]]
    assert sorted(e[1] for e in inserts) == [(7, "admin", "*"), (7, "viewer", "context-read")]


def test_clear_org_role_permissions(monkeypatch):
    conn = FakeConn()
    _patch_pool(monkeypatch, conn)
    asyncio.run(tenancy.clear_org_role_permissions(7))
    assert "DELETE FROM org_role_permissions" in conn.executed[0][0]
    assert conn.executed[0][1] == (7,)


def test_projects_write_is_a_known_permission():
    from src.mcp.permissions import PERMISSIONS
    assert "projects-write" in PERMISSIONS


def test_projects_write_default_is_admin_and_owner_only():
    from src.mcp.permissions import DEFAULT_ROLE_PERMISSIONS
    assert "projects-write" in DEFAULT_ROLE_PERMISSIONS["admin"]
    assert DEFAULT_ROLE_PERMISSIONS["owner"] == frozenset({"*"})
    assert "projects-write" not in DEFAULT_ROLE_PERMISSIONS["member"]
    assert "projects-write" not in DEFAULT_ROLE_PERMISSIONS["viewer"]


def test_projects_write_is_offered_by_the_permissions_matrix_api():
    """La UI Organization -> MCP permissions elenca i permessi da PERMISSIONS:
    il permesso nuovo deve comparire senza modifiche al frontend."""
    from src.mcp.permissions import PERMISSIONS
    from src.api.routes import organizations
    assert organizations.PERMISSIONS is PERMISSIONS


def test_sources_write_is_a_known_permission():
    from src.mcp.permissions import PERMISSIONS
    assert "sources-write" in PERMISSIONS


def test_sources_write_default_is_admin_and_owner_only():
    from src.mcp.permissions import DEFAULT_ROLE_PERMISSIONS
    assert "sources-write" in DEFAULT_ROLE_PERMISSIONS["admin"]
    assert "sources-write" not in DEFAULT_ROLE_PERMISSIONS["member"]
    assert "sources-write" not in DEFAULT_ROLE_PERMISSIONS["viewer"]
