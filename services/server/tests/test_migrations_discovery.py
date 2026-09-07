"""Discovery orders modules by number; current_version reads the table."""
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


def write_versions(directory, specs):
    for version, name, transactional in specs:
        path = directory / f"{version:04d}_{name}.py"
        path.write_text(
            MODULE.format(version=version, name=name, transactional=transactional),
            encoding="utf-8",
        )
    (directory / "__init__.py").write_text("", encoding="utf-8")
    (directory / "notes.txt").write_text("not a migration", encoding="utf-8")


def test_lock_key_is_the_agreed_constant():
    assert runner.MIGRATIONS_LOCK_KEY == 0x43464D47


def test_discovery_orders_by_version_and_skips_non_modules(tmp_path):
    write_versions(
        tmp_path,
        [(3, "c", True), (1, "a", True), (2, "b", False)],
    )
    modules = runner.discover_versions(tmp_path)
    assert [m.VERSION for m in modules] == [1, 2, 3]
    assert [m.NAME for m in modules] == ["a", "b", "c"]
    assert [m.TRANSACTIONAL for m in modules] == [True, False, True]


def test_discovery_defaults_to_the_packaged_versions_dir(tmp_path, monkeypatch):
    write_versions(tmp_path, [(1, "a", True)])
    monkeypatch.setattr(runner, "VERSIONS_DIR", tmp_path)
    assert [m.VERSION for m in runner.discover_versions()] == [1]


def test_current_version_is_zero_when_the_table_is_absent():
    conn = FakeConn(fetchval_results=[False])
    assert asyncio.run(runner.current_version(FakePool(conn))) == 0


def test_current_version_reads_the_max_applied():
    conn = FakeConn(fetchval_results=[True, 4])
    assert asyncio.run(runner.current_version(FakePool(conn))) == 4


def test_discovery_rejects_a_module_missing_the_contract(tmp_path):
    (tmp_path / "0001_a.py").write_text("NAME = 'a'\n", encoding="utf-8")
    with pytest.raises(ValueError, match="0001_a.py"):
        runner.discover_versions(tmp_path)


def test_discovery_rejects_duplicate_versions(tmp_path):
    write_versions(tmp_path, [(1, "a", True)])
    (tmp_path / "0001_b.py").write_text(
        MODULE.format(version=1, name="b", transactional=True), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="duplicate migration version 1"):
        runner.discover_versions(tmp_path)
