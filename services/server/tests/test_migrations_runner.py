"""run_migrations: lock, ordering, transaction dispatch, failure behaviour."""
import asyncio

import pytest

from src.migrations import runner
from tests.fake_db import FakeConn, FakePool

MODULE = '''
VERSION = {version}
NAME = "{name}"
TRANSACTIONAL = {transactional}


async def upgrade(conn) -> None:
    await conn.execute("-- migration {version}")
'''

FAILING = '''
VERSION = {version}
NAME = "{name}"
TRANSACTIONAL = True


async def upgrade(conn) -> None:
    raise RuntimeError("boom {version}")
'''


def build_versions(tmp_path, specs, failing=()):
    for version, name, transactional in specs:
        (tmp_path / f"{version:04d}_{name}.py").write_text(
            MODULE.format(version=version, name=name, transactional=transactional),
            encoding="utf-8",
        )
    for version, name in failing:
        (tmp_path / f"{version:04d}_{name}.py").write_text(
            FAILING.format(version=version, name=name), encoding="utf-8"
        )
    return tmp_path


def run(pool):
    return asyncio.run(runner.run_migrations(pool))


def test_applies_every_pending_module_in_order(tmp_path, monkeypatch):
    build_versions(tmp_path, [(1, "a", True), (2, "b", True), (3, "c", True)])
    monkeypatch.setattr(runner, "VERSIONS_DIR", tmp_path)
    conn = FakeConn(fetchval_results=[0])

    assert run(FakePool(conn)) == [1, 2, 3]
    assert "-- migration 1" in conn.sql
    assert "-- migration 3" in conn.sql
    assert any("CREATE TABLE IF NOT EXISTS schema_migrations" in q for q in conn.sql)
    recorded = [args for query, args in conn.executed if "INSERT INTO schema_migrations" in query]
    assert recorded == [(1, "a"), (2, "b"), (3, "c")]


def test_skips_modules_already_recorded(tmp_path, monkeypatch):
    build_versions(tmp_path, [(1, "a", True), (2, "b", True), (3, "c", True)])
    monkeypatch.setattr(runner, "VERSIONS_DIR", tmp_path)
    conn = FakeConn(fetch_rows=[{"version": 1}, {"version": 2}])

    assert run(FakePool(conn)) == [3]
    assert "-- migration 1" not in conn.sql
    assert "-- migration 2" not in conn.sql
    assert "-- migration 3" in conn.sql


def test_a_lower_numbered_module_applied_after_a_higher_one_still_runs(tmp_path, monkeypatch):
    build_versions(tmp_path, [(1, "a", True), (2, "b", True), (3, "c", True)])
    monkeypatch.setattr(runner, "VERSIONS_DIR", tmp_path)
    conn = FakeConn(fetch_rows=[{"version": 1}, {"version": 3}])

    assert run(FakePool(conn)) == [2]
    assert "-- migration 2" in conn.sql


def test_nothing_to_do_returns_an_empty_list(tmp_path, monkeypatch):
    build_versions(tmp_path, [(1, "a", True)])
    monkeypatch.setattr(runner, "VERSIONS_DIR", tmp_path)
    conn = FakeConn(fetch_rows=[{"version": 1}])

    assert run(FakePool(conn)) == []


def test_transactional_module_runs_inside_a_transaction(tmp_path, monkeypatch):
    build_versions(tmp_path, [(1, "a", True)])
    monkeypatch.setattr(runner, "VERSIONS_DIR", tmp_path)
    conn = FakeConn(fetchval_results=[0])

    run(FakePool(conn))
    assert conn.transactions == 1
    assert conn.open_transactions == 0


def test_non_transactional_module_runs_in_autocommit(tmp_path, monkeypatch):
    build_versions(tmp_path, [(1, "a", False)])
    monkeypatch.setattr(runner, "VERSIONS_DIR", tmp_path)
    conn = FakeConn(fetchval_results=[0])

    assert run(FakePool(conn)) == [1]
    assert conn.transactions == 0


def test_advisory_lock_is_taken_first_and_released_last(tmp_path, monkeypatch):
    build_versions(tmp_path, [(1, "a", True)])
    monkeypatch.setattr(runner, "VERSIONS_DIR", tmp_path)
    conn = FakeConn(fetchval_results=[0])
    pool = FakePool(conn)

    run(pool)
    assert "pg_advisory_lock" in conn.executed[0][0]
    assert conn.executed[0][1] == (runner.MIGRATIONS_LOCK_KEY,)
    assert "pg_advisory_unlock" in conn.executed[-1][0]
    assert conn.executed[-1][1] == (runner.MIGRATIONS_LOCK_KEY,)
    assert pool.acquired == 1


def test_first_failure_stops_the_run_and_still_releases_the_lock(tmp_path, monkeypatch):
    build_versions(tmp_path, [(1, "a", True), (3, "c", True)], failing=[(2, "b")])
    monkeypatch.setattr(runner, "VERSIONS_DIR", tmp_path)
    conn = FakeConn(fetchval_results=[0])

    with pytest.raises(RuntimeError, match="boom 2"):
        run(FakePool(conn))

    assert "-- migration 1" in conn.sql
    assert "-- migration 3" not in conn.sql
    recorded = [args for query, args in conn.executed if "INSERT INTO schema_migrations" in query]
    assert recorded == [(1, "a")]
    assert "pg_advisory_unlock" in conn.executed[-1][0]
