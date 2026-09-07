# services/server/tests/test_permissions_model.py
from src.mcp.permissions import (
    DEFAULT_ROLE_PERMISSIONS,
    ORG_ROLES,
    PERMISSIONS,
    parse_permissions_csv,
    permissions_from_scope,
)


def test_default_role_permissions_cover_all_roles():
    assert set(DEFAULT_ROLE_PERMISSIONS) == set(ORG_ROLES)
    assert DEFAULT_ROLE_PERMISSIONS["viewer"] == frozenset({"context-read"})
    assert DEFAULT_ROLE_PERMISSIONS["member"] == frozenset(
        {"context-read", "context-write", "db-query", "ssh-read"}
    )
    assert DEFAULT_ROLE_PERMISSIONS["admin"] == frozenset(
        {
            "context-read",
            "context-write",
            "db-query",
            "jobs",
            "ssh-read",
            "projects-write",
            "sources-write",
        }
    )
    assert DEFAULT_ROLE_PERMISSIONS["owner"] == frozenset({"*"})


def test_permissions_from_scope_mapping():
    assert permissions_from_scope("read") == frozenset({"context-read"})
    assert permissions_from_scope("write") == frozenset({"context-read", "context-write"})
    assert permissions_from_scope("read,write") == frozenset({"context-read", "context-write"})
    assert permissions_from_scope("admin") == frozenset({"*"})
    assert permissions_from_scope("read,admin") == frozenset({"*"})
    assert permissions_from_scope(None) == frozenset()
    assert permissions_from_scope("") == frozenset()


def test_parse_permissions_csv():
    assert parse_permissions_csv(None) is None
    assert parse_permissions_csv("*") == frozenset({"*"})
    assert parse_permissions_csv("context-read,db-query") == frozenset(
        {"context-read", "db-query"}
    )
    # permessi sconosciuti vengono scartati, non fanno errore
    assert parse_permissions_csv("context-read,bogus") == frozenset({"context-read"})
    # '*' assorbe tutto
    assert parse_permissions_csv("db-query,*") == frozenset({"*"})
    assert parse_permissions_csv("") == frozenset()


def test_write_permissions_exist():
    assert "db-write" in PERMISSIONS
    assert "ssh-write" in PERMISSIONS
    assert "repo-write" in PERMISSIONS


def test_only_owner_gets_write_capabilities_by_default():
    for role in ("viewer", "member", "admin"):
        perms = DEFAULT_ROLE_PERMISSIONS[role]
        assert "*" not in perms
        assert not perms & {"db-write", "ssh-write", "repo-write"}
    assert DEFAULT_ROLE_PERMISSIONS["owner"] == frozenset({"*"})


def test_admin_defaults_keep_non_write_capabilities():
    assert DEFAULT_ROLE_PERMISSIONS["admin"] == frozenset(
        {
            "context-read",
            "context-write",
            "db-query",
            "jobs",
            "ssh-read",
            "projects-write",
            "sources-write",
        }
    )
