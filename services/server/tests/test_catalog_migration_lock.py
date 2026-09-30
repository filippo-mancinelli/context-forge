"""The catalog conversion serialises concurrent boots and reports what needs a look."""
import asyncio
import logging

from src.catalog import migration, repo_migration
from src.datasources import scope_migration
from src.migrations.runner import MIGRATIONS_LOCK_KEY
from tests.fake_db import FakeConn, FakePool


def _run(monkeypatch, conn, legacy=False):
    async def fake_pool():
        return FakePool(conn)

    async def column_exists(conn, table, column):
        return legacy

    monkeypatch.setattr(migration, "get_pool", fake_pool)
    monkeypatch.setattr(migration, "_column_exists", column_exists)
    return asyncio.run(migration.apply_catalog_migration())


def test_the_conversion_takes_its_advisory_lock_first_inside_the_transaction(monkeypatch):
    # Converted schema: every catalog lookup finds nothing legacy, every index present.
    conn = FakeConn(fetchval_results=[False] + [True] * 3)

    _run(monkeypatch, conn)

    first_sql, first_args = conn.executed[0]
    assert "pg_advisory_xact_lock" in first_sql
    assert first_args == (migration.CATALOG_MIGRATION_LOCK_KEY,)
    assert conn.transactions == 1
    # A converted database takes no table lock: no DDL at all.
    ddl = [q for q in conn.sql if q.lstrip().upper().startswith(("ALTER", "DROP", "CREATE"))]
    assert ddl == []


def test_the_boot_lock_keys_are_pairwise_distinct():
    keys = [
        migration.CATALOG_MIGRATION_LOCK_KEY,
        repo_migration.REPO_CATALOG_LOCK_KEY,
        scope_migration.MIGRATION_LOCK_KEY,
        MIGRATIONS_LOCK_KEY,
    ]
    assert len(set(keys)) == len(keys)


def test_replaced_secrets_or_renames_are_logged_as_warnings(monkeypatch, caplog):
    async def fake_machines(conn, rows, report):
        report["machines"] += 1
        report["secret_replaced"].append("ssh_sources#7")

    async def no_rows(conn, has_sources, has_databases):
        return []

    monkeypatch.setattr(migration, "_create_machines", fake_machines)
    monkeypatch.setattr(migration, "_legacy_ssh_rows", no_rows)
    monkeypatch.setattr(migration, "_move_to_selections", lambda *a: asyncio.sleep(0))
    conn = FakeConn(fetchval_results=[False] + [True] * 3)

    with caplog.at_level(logging.INFO, logger=migration.logger.name):
        _run(monkeypatch, conn, legacy=True)

    assert [r.levelno for r in caplog.records] == [logging.WARNING]


def test_a_plain_machine_count_stays_at_info(monkeypatch, caplog):
    async def fake_machines(conn, rows, report):
        report["machines"] += 2

    async def no_rows(conn, has_sources, has_databases):
        return []

    monkeypatch.setattr(migration, "_create_machines", fake_machines)
    monkeypatch.setattr(migration, "_legacy_ssh_rows", no_rows)
    monkeypatch.setattr(migration, "_move_to_selections", lambda *a: asyncio.sleep(0))
    conn = FakeConn(fetchval_results=[False] + [True] * 3)

    with caplog.at_level(logging.INFO, logger=migration.logger.name):
        _run(monkeypatch, conn, legacy=True)

    assert [r.levelno for r in caplog.records] == [logging.INFO]
