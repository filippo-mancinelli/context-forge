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
    conn = FakeConn(fetchval_results=[True], fetch_rows=[{"version": 1}, {"version": 4}])
    assert asyncio.run(runner.current_version(FakePool(conn))) == 4


def test_applied_versions_is_empty_when_the_table_is_absent():
    conn = FakeConn(fetchval_results=[False])
    assert asyncio.run(runner.applied_versions(FakePool(conn))) == set()


def test_applied_versions_reads_every_recorded_row():
    conn = FakeConn(fetchval_results=[True], fetch_rows=[{"version": 1}, {"version": 3}])
    assert asyncio.run(runner.applied_versions(FakePool(conn))) == {1, 3}


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


def test_discovery_rejects_a_filename_number_disagreeing_with_version(tmp_path):
    (tmp_path / "0002_a.py").write_text(
        MODULE.format(version=1, name="a", transactional=True), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="0002_a.py"):
        runner.discover_versions(tmp_path)


def test_discovery_rejects_a_filename_suffix_disagreeing_with_name(tmp_path):
    (tmp_path / "0001_a.py").write_text(
        MODULE.format(version=1, name="b", transactional=True), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="0001_a.py"):
        runner.discover_versions(tmp_path)


def test_discovery_rejects_a_non_int_version(tmp_path):
    (tmp_path / "0001_a.py").write_text(
        'VERSION = "1"\nNAME = "a"\n\n\nasync def upgrade(conn) -> None:\n    pass\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="VERSION must be an int"):
        runner.discover_versions(tmp_path)


def test_discovery_rejects_a_bool_version(tmp_path):
    (tmp_path / "0001_a.py").write_text(
        'VERSION = True\nNAME = "a"\n\n\nasync def upgrade(conn) -> None:\n    pass\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="VERSION must be an int"):
        runner.discover_versions(tmp_path)


def test_discovery_rejects_a_non_str_name(tmp_path):
    (tmp_path / "0001_a.py").write_text(
        "VERSION = 1\nNAME = 5\n\n\nasync def upgrade(conn) -> None:\n    pass\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="NAME must be a str"):
        runner.discover_versions(tmp_path)


def test_discovery_rejects_a_non_coroutine_upgrade(tmp_path):
    (tmp_path / "0001_a.py").write_text(
        'VERSION = 1\nNAME = "a"\n\n\ndef upgrade(conn) -> None:\n    pass\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="upgrade must be an async function"):
        runner.discover_versions(tmp_path)


def test_discovery_rejects_an_empty_versions_directory(tmp_path):
    with pytest.raises(ValueError, match="no migration modules found"):
        runner.discover_versions(tmp_path)


def test_discovery_rejects_a_missing_versions_directory(tmp_path):
    missing = tmp_path / "does-not-exist"
    with pytest.raises(ValueError, match="no migration modules found"):
        runner.discover_versions(missing)
