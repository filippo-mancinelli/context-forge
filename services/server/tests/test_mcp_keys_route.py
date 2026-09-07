# services/server/tests/test_mcp_keys_route.py
import pytest
from fastapi import HTTPException

from src.api.routes.mcp_keys import (
    CreateKeyRequest,
    _enforce_creator_cap,
    _resolve_permissions_csv,
)


def test_explicit_permissions_win():
    req = CreateKeyRequest(name="k", permissions=["db-query", "jobs"])
    assert _resolve_permissions_csv(req) == "db-query,jobs"


def test_star_is_exclusive():
    with pytest.raises(HTTPException) as exc:
        _resolve_permissions_csv(CreateKeyRequest(name="k", permissions=["*", "jobs"]))
    assert exc.value.status_code == 422


def test_star_alone_is_valid():
    assert _resolve_permissions_csv(CreateKeyRequest(name="k", permissions=["*"])) == "*"


def test_unknown_permission_rejected():
    with pytest.raises(HTTPException) as exc:
        _resolve_permissions_csv(CreateKeyRequest(name="k", permissions=["bogus"]))
    assert exc.value.status_code == 422


def test_empty_permissions_rejected():
    with pytest.raises(HTTPException) as exc:
        _resolve_permissions_csv(CreateKeyRequest(name="k", permissions=[]))
    assert exc.value.status_code == 422


def test_legacy_scope_alias():
    req = CreateKeyRequest(name="k", scope="admin")
    assert _resolve_permissions_csv(req) == "*"


def test_default_when_nothing_given():
    req = CreateKeyRequest(name="k")
    assert _resolve_permissions_csv(req) == "context-read,context-write"


# ===== I1: key permissions capped by the creator's own effective role permissions =====

def test_creator_cap_member_requesting_star_rejected():
    allowed = frozenset({"context-read", "context-write", "db-query"})
    with pytest.raises(HTTPException) as exc:
        _enforce_creator_cap("*", allowed)
    assert exc.value.status_code == 403


def test_creator_cap_member_requesting_out_of_scope_permission_rejected():
    allowed = frozenset({"context-read", "context-write", "db-query"})
    with pytest.raises(HTTPException) as exc:
        _enforce_creator_cap("repo-write,jobs", allowed)
    assert exc.value.status_code == 403


def test_creator_cap_member_requesting_subset_allowed():
    allowed = frozenset({"context-read", "context-write", "db-query"})
    _enforce_creator_cap("context-read,db-query", allowed)  # no raise


def test_creator_cap_admin_requesting_star_allowed():
    allowed = frozenset({"*"})
    _enforce_creator_cap("*", allowed)  # no raise


def test_creator_cap_admin_requesting_anything_allowed():
    allowed = frozenset({"*"})
    _enforce_creator_cap("repo-write,jobs", allowed)  # no raise


from datetime import datetime, timezone

from src.api.routes.mcp_keys import _serialize_key


def test_serialize_key_passes_through_project_fields():
    ts = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
    row = {
        "id": 1, "name": "k", "scope": "read,write",
        "permissions": "context-read", "created_at": ts,
        "last_used_at": None, "expires_at": None,
        "created_by": 3, "org_id": 2,
        "project_id": 9, "project_slug": "billing",
    }
    out = _serialize_key(row)
    assert out["project_id"] == 9
    assert out["project_slug"] == "billing"
    assert out["created_at"] == "2026-07-22T12:00:00+00:00"
    assert out["last_used_at"] is None
