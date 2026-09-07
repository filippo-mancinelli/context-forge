"""Dispatch dei tool di scrittura: esecuzione diretta o proposta da approvare."""
import asyncio

import pytest

from src import write_previews, write_requests as wr_service
from src.mcp import approvals as mcp_approvals
from src.mcp import datasources as mcp_datasources
from src.mcp import ssh_files as mcp_ssh
from src.mcp.context import set_current_org_id, set_current_project_id
from src.mcp.permissions import set_current_permissions


@pytest.fixture(autouse=True)
def _reset_mcp_context():
    yield
    set_current_org_id(None)
    set_current_project_id(None)
    set_current_permissions(None)


def _underlying(tool):
    return getattr(tool, "fn", tool)


class _Principal:
    kind = "api_key"
    id = 5
    label = "ci-bot"


def _patch_principal(monkeypatch):
    monkeypatch.setattr(mcp_approvals, "get_current_principal", lambda: _Principal(), raising=False)


# ===== db_execute =====

def test_db_execute_direct_path_is_unchanged(monkeypatch):
    """ACCETTAZIONE: con db-write la risposta è identica a prima, byte per byte."""
    expected = {"connection": "erp", "sql": "UPDATE t SET a=1 WHERE id=1",
                "row_count": 1, "duration_ms": 5}
    called = {}

    async def fake_run_write(org_id, project_id, ref, sql, source="mcp"):
        called.update(org=org_id, project=project_id, ref=ref, sql=sql, source=source)
        return expected

    async def no_create(**kwargs):
        raise AssertionError("a caller holding db-write must not create a request")

    monkeypatch.setattr(mcp_datasources.service, "run_write", fake_run_write)
    monkeypatch.setattr(wr_service, "create", no_create)
    set_current_org_id(1)
    set_current_project_id(2)
    set_current_permissions(frozenset({"db-write"}))

    out = asyncio.run(_underlying(mcp_datasources.db_execute)(
        connection="erp", sql="UPDATE t SET a=1 WHERE id=1"))

    assert out == expected
    assert called == {"org": 1, "project": 2, "ref": "erp",
                      "sql": "UPDATE t SET a=1 WHERE id=1", "source": "mcp"}


def test_db_execute_direct_path_with_auth_disabled(monkeypatch):
    """Permessi None (auth MCP disattivata): resta l'esecuzione diretta."""
    async def fake_run_write(org_id, project_id, ref, sql, source="mcp"):
        return {"connection": "erp", "sql": sql, "row_count": 2, "duration_ms": 1}

    monkeypatch.setattr(mcp_datasources.service, "run_write", fake_run_write)
    set_current_org_id(1)
    set_current_project_id(2)
    set_current_permissions(None)
    out = asyncio.run(_underlying(mcp_datasources.db_execute)(
        connection="erp", sql="UPDATE t SET a=1 WHERE id=1"))
    assert out["row_count"] == 2


def test_db_execute_direct_path_error_shape_is_unchanged(monkeypatch):
    async def boom(org_id, project_id, ref, sql, source="mcp"):
        raise RuntimeError("db statement timeout")

    monkeypatch.setattr(mcp_datasources.service, "run_write", boom)
    set_current_org_id(1)
    set_current_project_id(2)
    set_current_permissions(frozenset({"db-write"}))
    out = asyncio.run(_underlying(mcp_datasources.db_execute)(
        connection="erp", sql="UPDATE t SET a=1 WHERE id=1"))
    assert out == {"status": "error", "error": "db statement timeout"}


def test_db_execute_without_db_write_creates_a_request(monkeypatch):
    _patch_principal(monkeypatch)
    created = {}

    async def no_run_write(*a, **k):
        raise AssertionError("must not execute without db-write")

    async def fake_get_connection(org_id, project_id, ref, include_secret=False):
        return {"id": 4, "name": "erp"}

    async def fake_sql_preview(org_id, project_id, connection, sql):
        return {"statement": sql, "plan_rows": 3, "plan_text": "{}"}

    async def fake_create(**kwargs):
        created.update(kwargs)
        return {"id": 12}

    monkeypatch.setattr(mcp_datasources.service, "run_write", no_run_write)
    monkeypatch.setattr(mcp_datasources.service, "get_connection", fake_get_connection)
    monkeypatch.setattr(write_previews, "sql_preview", fake_sql_preview)
    monkeypatch.setattr(wr_service, "create", fake_create)
    set_current_org_id(1)
    set_current_project_id(2)
    set_current_permissions(frozenset({"context-read", "context-write"}))

    out = asyncio.run(_underlying(mcp_datasources.db_execute)(
        connection="erp", sql="UPDATE t SET a=1 WHERE id=1", reason="fix a bad row"))

    assert out == {
        "status": "pending_approval",
        "request_id": 12,
        "preview": {"statement": "UPDATE t SET a=1 WHERE id=1", "plan_rows": 3, "plan_text": "{}"},
        "message": ("An organization admin must approve this change. "
                    "Poll with write_request_status(12)."),
    }
    assert created["kind"] == "db_execute"
    assert created["target"] == "erp"
    assert created["payload"] == {"sql": "UPDATE t SET a=1 WHERE id=1"}
    assert created["reason"] == "fix a bad row"
    assert created["requested_by_kind"] == "api_key"
    assert created["requested_by_id"] == 5
    assert created["requested_by"] == "ci-bot"


def test_db_execute_proposal_validates_before_previewing(monkeypatch):
    async def no_preview(*a, **k):
        raise AssertionError("must not preview an invalid statement")

    monkeypatch.setattr(write_previews, "sql_preview", no_preview)
    set_current_org_id(1)
    set_current_project_id(2)
    set_current_permissions(frozenset({"context-write"}))

    out = asyncio.run(_underlying(mcp_datasources.db_execute)(
        connection="erp", sql="DROP TABLE t"))
    assert out["status"] == "error"
    assert "Query rejected" in out["error"]


def test_db_execute_with_neither_permission_is_denied():
    set_current_org_id(1)
    set_current_project_id(2)
    set_current_permissions(frozenset({"context-read", "db-query"}))
    with pytest.raises(Exception, match="db-write"):
        asyncio.run(_underlying(mcp_datasources.db_execute)(
            connection="erp", sql="UPDATE t SET a=1 WHERE id=1"))


def test_db_execute_proposal_scrubs_the_explain_error(monkeypatch):
    """Il preview non deve portare credenziali di connessione all'agente/UI."""
    async def fake_get_connection(org_id, project_id, ref, include_secret=False):
        return {"id": 4, "name": "erp"}

    async def fake_sql_preview(org_id, project_id, connection, sql):
        return {
            "statement": sql,
            "plan_rows": None,
            "plan_text": "",
            "explain_error": "could not connect to postgresql://user:password@host:5432/db?password=abc",
        }

    async def fake_create(**kwargs):
        return {"id": 12, "preview": kwargs["preview"]}

    monkeypatch.setattr(mcp_datasources.service, "get_connection", fake_get_connection)
    monkeypatch.setattr(write_previews, "sql_preview", fake_sql_preview)
    monkeypatch.setattr(wr_service, "create", fake_create)
    set_current_org_id(1)
    set_current_project_id(2)
    set_current_permissions(frozenset({"context-write"}))

    out = asyncio.run(_underlying(mcp_datasources.db_execute)(
        connection="erp", sql="UPDATE t SET a=1 WHERE id=1"))

    assert "password" not in out["preview"]["explain_error"]
    assert out["preview"]["explain_error"] == "could not connect to postgresql://host:5432/db"


# ===== ssh_write_file =====

def _source_record():
    return {"id": 3, "org_id": 1, "project_id": 2, "name": "web1",
            "root_path": "/etc/app", "host": "h", "port": 22, "username": "u",
            "auth_method": "password", "password_enc": "", "private_key_enc": "",
            "include_globs": None, "exclude_globs": None}


def test_ssh_write_file_direct_path_is_unchanged(monkeypatch):
    """ACCETTAZIONE: con ssh-write la risposta è identica a prima."""
    async def fake_resolve(source, include_secret=False):
        return _source_record()

    def fake_write(conn, path, content):
        return {"path": path, "bytes_written": len(content.encode("utf-8"))}

    async def no_create(**kwargs):
        raise AssertionError("a caller holding ssh-write must not create a request")

    monkeypatch.setattr(mcp_ssh, "_resolve", fake_resolve)
    monkeypatch.setattr(mcp_ssh.ssh_service, "decrypted_conn", lambda r: {"root_path": "/etc/app"})
    from src.ssh_sources import client
    monkeypatch.setattr(client, "write_file", fake_write)
    monkeypatch.setattr(wr_service, "create", no_create)
    set_current_permissions(frozenset({"ssh-write"}))

    out = asyncio.run(_underlying(mcp_ssh.ssh_write_file)(
        source="web1", path="app.yml", content="key: value\n"))
    assert out == {"source": "web1", "path": "app.yml", "bytes_written": 11}


def test_ssh_write_file_without_ssh_write_creates_a_request(monkeypatch):
    _patch_principal(monkeypatch)
    created = {}

    async def fake_resolve(source, include_secret=False):
        return _source_record()

    def no_write(conn, path, content):
        raise AssertionError("must not write without ssh-write")

    def fake_file_preview(conn_params, path, content):
        return {"path": path, "content_bytes": 11, "content_sha256": "abc",
                "existing_sha256": None, "existing_size": None, "is_new": True}

    async def fake_create(**kwargs):
        created.update(kwargs)
        return {"id": 31}

    monkeypatch.setattr(mcp_ssh, "_resolve", fake_resolve)
    monkeypatch.setattr(mcp_ssh.ssh_service, "decrypted_conn", lambda r: {"root_path": "/etc/app"})
    from src.ssh_sources import client
    monkeypatch.setattr(client, "write_file", no_write)
    monkeypatch.setattr(write_previews, "file_preview", fake_file_preview)
    monkeypatch.setattr(wr_service, "create", fake_create)
    set_current_permissions(frozenset({"context-write"}))

    out = asyncio.run(_underlying(mcp_ssh.ssh_write_file)(
        source="web1", path="app.yml", content="key: value\n", reason="rotate the port"))

    assert out["status"] == "pending_approval"
    assert out["request_id"] == 31
    assert out["message"].endswith("Poll with write_request_status(31).")
    assert created["kind"] == "ssh_write_file"
    assert created["target"] == "web1:app.yml"
    assert created["payload"] == {"source": "web1", "path": "app.yml", "content": "key: value\n"}
    assert created["org_id"] == 1 and created["project_id"] == 2


def test_ssh_write_file_with_neither_permission_is_denied():
    set_current_permissions(frozenset({"ssh-read"}))
    with pytest.raises(Exception, match="ssh-write"):
        asyncio.run(_underlying(mcp_ssh.ssh_write_file)(
            source="web1", path="app.yml", content="x"))


def test_ssh_write_file_proposal_scrubs_the_read_error(monkeypatch):
    """Il preview non deve portare credenziali di connessione all'agente/UI."""
    async def fake_resolve(source, include_secret=False):
        return _source_record()

    def fake_file_preview(conn_params, path, content):
        return {
            "path": path,
            "content_bytes": 11,
            "content_sha256": "abc",
            "existing_sha256": None,
            "existing_size": None,
            "is_new": False,
            "read_error": "sftp error at sftp://user:password@h:22/etc/app?password=abc",
        }

    async def fake_create(**kwargs):
        return {"id": 31, "preview": kwargs["preview"]}

    monkeypatch.setattr(mcp_ssh, "_resolve", fake_resolve)
    monkeypatch.setattr(write_previews, "file_preview", fake_file_preview)
    monkeypatch.setattr(wr_service, "create", fake_create)
    set_current_permissions(frozenset({"context-write"}))

    out = asyncio.run(_underlying(mcp_ssh.ssh_write_file)(
        source="web1", path="app.yml", content="key: value\n"))

    assert "password" not in out["preview"]["read_error"]
    assert out["preview"]["read_error"] == "sftp error at sftp://h:22/etc/app"


# ===== write_request_status =====

def test_write_request_status_returns_the_record(monkeypatch):
    async def fake_get(request_id, org_id=None, project_id=None):
        assert (request_id, org_id, project_id) == (12, 1, 2)
        return {"id": 12, "kind": "db_execute", "target": "erp", "status": "executed",
                "decision_note": "looks fine", "result": {"row_count": 1}, "error": None,
                "created_at": "2026-09-07T10:00:00+00:00",
                "expires_at": "2026-09-14T10:00:00+00:00"}

    monkeypatch.setattr(wr_service, "get", fake_get)
    set_current_org_id(1)
    set_current_project_id(2)
    set_current_permissions(frozenset({"context-read"}))

    out = asyncio.run(_underlying(mcp_approvals.write_request_status)(request_id=12))
    assert out["status"] == "ok"
    assert out["state"] == "executed"
    assert out["result"] == {"row_count": 1}
    assert out["decision_note"] == "looks fine"


def test_write_request_status_is_scoped_to_org_and_project(monkeypatch):
    async def fake_get(request_id, org_id=None, project_id=None):
        return None

    monkeypatch.setattr(wr_service, "get", fake_get)
    set_current_org_id(1)
    set_current_project_id(2)
    set_current_permissions(frozenset({"context-read"}))
    out = asyncio.run(_underlying(mcp_approvals.write_request_status)(request_id=12))
    assert out["status"] == "error"
    assert "not found" in out["error"]


def test_write_request_status_requires_context_read():
    set_current_org_id(1)
    set_current_project_id(2)
    set_current_permissions(frozenset({"jobs"}))
    with pytest.raises(Exception, match="context-read"):
        asyncio.run(_underlying(mcp_approvals.write_request_status)(request_id=12))


# ===== permission helpers =====

def test_has_permission_true_when_auth_is_disabled():
    from src.mcp.permissions import has_permission
    set_current_permissions(None)
    assert has_permission("db-write") is True


def test_has_permission_honours_the_wildcard():
    from src.mcp.permissions import has_permission
    set_current_permissions(frozenset({"*"}))
    assert has_permission("ssh-write") is True


def test_has_permission_false_when_missing():
    from src.mcp.permissions import has_permission
    set_current_permissions(frozenset({"context-write"}))
    assert has_permission("db-write") is False


def test_requires_permission_accepts_an_alternative():
    from src.mcp.permissions import requires_permission, set_current_permissions as setp

    @requires_permission("db-write", alternatives=("context-write",))
    async def tool():
        return "ran"

    setp(frozenset({"context-write"}))
    assert asyncio.run(tool()) == "ran"


def test_requires_permission_denial_message_names_the_primary():
    from src.mcp.permissions import requires_permission, set_current_permissions as setp

    @requires_permission("db-write", alternatives=("context-write",))
    async def tool():
        return "ran"

    setp(frozenset({"db-query"}))
    with pytest.raises(Exception, match="db-write"):
        asyncio.run(tool())
