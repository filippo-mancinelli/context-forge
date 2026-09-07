"""Preview delle proposte di scrittura: EXPLAIN per SQL, hash/dimensioni per i file."""
import asyncio
import hashlib
import json

import pytest

from src import write_previews


# ===== EXPLAIN =====

def test_explain_statement_postgres():
    assert write_previews.explain_statement(
        "postgresql", "UPDATE t SET a=1 WHERE id=1"
    ) == "EXPLAIN (FORMAT JSON) UPDATE t SET a=1 WHERE id=1"


def test_explain_statement_mysql():
    assert write_previews.explain_statement(
        "mysql", "DELETE FROM t WHERE id=1"
    ) == "EXPLAIN FORMAT=JSON DELETE FROM t WHERE id=1"


def test_explain_statement_unsupported_dialect():
    with pytest.raises(write_previews.UnsupportedExplain):
        write_previews.explain_statement("sqlite", "DELETE FROM t WHERE id=1")


def test_extract_plan_reads_top_node_plan_rows():
    plan = [{"Plan": {"Node Type": "Update", "Plan Rows": 42}}]
    rows = [{"QUERY PLAN": plan}]
    plan_rows, plan_text = write_previews.extract_plan(rows)
    assert plan_rows == 42
    assert "Plan Rows" in plan_text


def test_extract_plan_accepts_a_json_string_cell():
    plan = json.dumps([{"Plan": {"Plan Rows": 7}}])
    plan_rows, plan_text = write_previews.extract_plan([{"QUERY PLAN": plan}])
    assert plan_rows == 7


def test_extract_plan_truncates_plan_text():
    plan = [{"Plan": {"Plan Rows": 1, "Filter": "x" * 5000}}]
    _rows, plan_text = write_previews.extract_plan([{"QUERY PLAN": plan}])
    assert len(plan_text) == write_previews.PLAN_TEXT_CHARS


def test_extract_plan_without_plan_rows_is_none():
    plan_rows, plan_text = write_previews.extract_plan([{"EXPLAIN": '{"query_block": {}}'}])
    assert plan_rows is None
    assert "query_block" in plan_text


def test_extract_plan_on_empty_result():
    assert write_previews.extract_plan([]) == (None, "")


# ===== sql_preview =====

class _FakeDialect:
    name = "postgresql"


class _FakeEngine:
    dialect = _FakeDialect()


def _patch_datasources(monkeypatch, executed, rows):
    from src.datasources import service

    async def fake_get_connection(org_id, project_id, ref, include_secret=False):
        return {"id": 4, "name": "erp"}

    async def fake_resolve_engine(record):
        return _FakeEngine()

    def fake_execute_readonly(engine, sql, max_rows):
        executed.append(sql)
        return (["QUERY PLAN"], rows, False)

    monkeypatch.setattr(service, "get_connection", fake_get_connection)
    monkeypatch.setattr(service, "_resolve_engine", fake_resolve_engine)
    monkeypatch.setattr(service, "_execute_readonly", fake_execute_readonly)


def test_sql_preview_runs_explain_through_the_readonly_executor(monkeypatch):
    executed = []
    _patch_datasources(monkeypatch, executed, [{"QUERY PLAN": [{"Plan": {"Plan Rows": 9}}]}])

    preview = asyncio.run(
        write_previews.sql_preview(1, 2, "erp", "UPDATE t SET a=1 WHERE id=1")
    )

    assert executed == ["EXPLAIN (FORMAT JSON) UPDATE t SET a=1 WHERE id=1"]
    assert preview["statement"] == "UPDATE t SET a=1 WHERE id=1"
    assert preview["plan_rows"] == 9
    assert "Plan Rows" in preview["plan_text"]
    assert "explain_error" not in preview


def test_sql_preview_survives_an_explain_failure(monkeypatch):
    from src.datasources import service

    async def fake_get_connection(org_id, project_id, ref, include_secret=False):
        return {"id": 4, "name": "erp"}

    async def fake_resolve_engine(record):
        return _FakeEngine()

    def boom(engine, sql, max_rows):
        raise RuntimeError("permission denied for table t")

    monkeypatch.setattr(service, "get_connection", fake_get_connection)
    monkeypatch.setattr(service, "_resolve_engine", fake_resolve_engine)
    monkeypatch.setattr(service, "_execute_readonly", boom)

    preview = asyncio.run(
        write_previews.sql_preview(1, 2, "erp", "UPDATE t SET a=1 WHERE id=1")
    )
    assert preview["plan_rows"] is None
    assert "permission denied" in preview["explain_error"]
    assert preview["statement"] == "UPDATE t SET a=1 WHERE id=1"


# ===== file_preview =====

def _conn():
    return {"root_path": "/etc/app", "host": "h", "port": 22, "username": "u",
            "auth_method": "password", "password": "p", "include_globs": None,
            "exclude_globs": None}


def test_file_preview_for_a_new_file(monkeypatch):
    from src.ssh_sources import client

    def missing(conn, path, offset=None, tail=None):
        raise FileNotFoundError(path)

    monkeypatch.setattr(client, "read_file", missing)

    preview = write_previews.file_preview(_conn(), "conf.d/app.yml", "key: value\n")
    assert preview["is_new"] is True
    assert preview["path"] == "conf.d/app.yml"
    assert preview["content_bytes"] == len(b"key: value\n")
    assert preview["content_sha256"] == hashlib.sha256(b"key: value\n").hexdigest()
    assert preview["existing_sha256"] is None
    assert preview["existing_size"] is None


def test_file_preview_for_an_existing_file(monkeypatch):
    from src.ssh_sources import client

    def existing(conn, path, offset=None, tail=None):
        return {"path": path, "size": 5, "offset": 0, "bytes_read": 5,
                "truncated": False, "content": "old\n\n"}

    monkeypatch.setattr(client, "read_file", existing)

    preview = write_previews.file_preview(_conn(), "app.yml", "new\n")
    assert preview["is_new"] is False
    assert preview["existing_size"] == 5
    assert preview["existing_sha256"] == hashlib.sha256(b"old\n\n").hexdigest()


def test_file_preview_skips_the_hash_of_a_truncated_read(monkeypatch):
    from src.ssh_sources import client

    def huge(conn, path, offset=None, tail=None):
        return {"path": path, "size": 9_000_000, "offset": 0, "bytes_read": 1_000_000,
                "truncated": True, "content": "x" * 1_000_000}

    monkeypatch.setattr(client, "read_file", huge)

    preview = write_previews.file_preview(_conn(), "big.log", "new\n")
    assert preview["is_new"] is False
    assert preview["existing_size"] == 9_000_000
    assert preview["existing_sha256"] is None


def test_file_preview_refuses_oversize_content(monkeypatch):
    from src.ssh_sources import client

    def boom(conn, path, offset=None, tail=None):
        raise AssertionError("must not touch SSH before the size check")

    monkeypatch.setattr(client, "read_file", boom)
    over = "x" * (write_previews.MAX_PROPOSAL_CONTENT_BYTES + 1)
    with pytest.raises(ValueError, match="exceeds"):
        write_previews.file_preview(_conn(), "big.txt", over)


def test_file_preview_records_a_read_error_without_treating_it_as_new(monkeypatch):
    from src.ssh_sources import client

    def unreachable(conn, path, offset=None, tail=None):
        raise OSError("connection refused")

    monkeypatch.setattr(client, "read_file", unreachable)

    preview = write_previews.file_preview(_conn(), "app.yml", "new\n")
    assert preview["is_new"] is False
    assert preview["existing_sha256"] is None
    assert preview["existing_size"] is None
    assert preview["read_error"] == "connection refused"
