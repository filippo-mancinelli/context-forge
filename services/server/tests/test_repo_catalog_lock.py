"""The repository conversion runs under its own advisory lock, clone moves included."""
import asyncio

import pytest

from src.catalog import repo_migration
from src.migrations.runner import MIGRATIONS_LOCK_KEY


class _Conn:
    def __init__(self, events):
        self.events = events

    async def execute(self, sql, *args):
        if "pg_advisory_lock" in sql:
            self.events.append(("lock", args[0]))
        elif "pg_advisory_unlock" in sql:
            self.events.append(("unlock", args[0]))

    def transaction(self):
        events = self.events

        class _Tx:
            async def __aenter__(self):
                events.append(("begin",))

            async def __aexit__(self, *exc):
                events.append(("end",))
                return False

        return _Tx()

    async def close(self):
        self.events.append(("close",))


def _patch(monkeypatch, events, *, fail=False):
    async def connect(dsn, **kwargs):
        return _Conn(events)

    async def legacy(conn, table, column):
        return True

    async def no_unowned(conn):
        return False

    async def nothing(conn, *args):
        return [] if not args else {}

    async def default_projects(conn):
        return {}

    async def import_legacy(conn, config_repos, defaults):
        return 0

    async def convert(conn, report, tokens, defaults):
        if fail:
            raise RuntimeError("conversion failed")
        return [("move", "a", "b")]

    async def keys(conn):
        events.append(("keys",))

    async def strip(conn):
        return 0

    monkeypatch.setattr(repo_migration.asyncpg, "connect", connect)
    monkeypatch.setattr(repo_migration, "_column_exists", legacy)
    monkeypatch.setattr(repo_migration, "_has_unowned_rows", no_unowned)
    monkeypatch.setattr(repo_migration, "_config_repos", nothing)
    monkeypatch.setattr(repo_migration, "_default_projects", default_projects)
    monkeypatch.setattr(repo_migration, "_import_legacy", import_legacy)
    monkeypatch.setattr(repo_migration, "_convert_legacy", convert)
    monkeypatch.setattr(repo_migration, "_ensure_repo_keys", keys)
    monkeypatch.setattr(repo_migration, "_strip_config_repos", strip)
    monkeypatch.setattr(repo_migration, "_apply_clone_ops", lambda ops: events.append(("clones", len(ops))))


def test_the_lock_covers_the_transaction_and_the_clone_moves(monkeypatch):
    events = []
    _patch(monkeypatch, events)

    asyncio.run(repo_migration.apply_repo_catalog_migration())

    key = repo_migration.REPO_CATALOG_LOCK_KEY
    assert events == [
        ("lock", key), ("begin",), ("keys",), ("end",), ("clones", 1), ("unlock", key), ("close",),
    ]


def test_the_lock_is_released_when_the_conversion_fails(monkeypatch):
    events = []
    _patch(monkeypatch, events, fail=True)

    with pytest.raises(RuntimeError, match="conversion failed"):
        asyncio.run(repo_migration.apply_repo_catalog_migration())

    key = repo_migration.REPO_CATALOG_LOCK_KEY
    assert events[0] == ("lock", key)
    assert events[-2:] == [("unlock", key), ("close",)]
    assert not any(e[0] == "clones" for e in events)


def test_the_lock_key_differs_from_the_migration_runner_key():
    assert repo_migration.REPO_CATALOG_LOCK_KEY != MIGRATIONS_LOCK_KEY
