"""forge-cli migrate applies pending versions; --status only reports."""
import argparse

from src import cli, db
from src.migrations import runner


class FakeModule:
    def __init__(self, version, name):
        self.VERSION = version
        self.NAME = name


def _patch(monkeypatch, *, applied=None, version=0, modules=()):
    calls = {"run": 0, "closed": 0}
    pool = object()

    async def fake_get_pool():
        return pool

    async def fake_close_db():
        calls["closed"] += 1

    async def fake_run_migrations(arg):
        calls["run"] += 1
        assert arg is pool
        return list(applied or [])

    async def fake_current_version(arg):
        assert arg is pool
        return version

    monkeypatch.setattr(db, "get_pool", fake_get_pool)
    monkeypatch.setattr(db, "close_db", fake_close_db)
    monkeypatch.setattr(runner, "run_migrations", fake_run_migrations)
    monkeypatch.setattr(runner, "current_version", fake_current_version)
    monkeypatch.setattr(runner, "discover_versions", lambda *a, **k: list(modules))
    return calls


def test_migrate_prints_the_versions_it_applied(monkeypatch, capsys):
    calls = _patch(monkeypatch, applied=[1, 2])
    cli.cmd_migrate(argparse.Namespace(status=False))
    out = capsys.readouterr().out
    assert "Applied: 1, 2" in out
    assert calls["run"] == 1
    assert calls["closed"] == 1


def test_migrate_says_so_when_there_is_nothing_to_do(monkeypatch, capsys):
    _patch(monkeypatch, applied=[])
    cli.cmd_migrate(argparse.Namespace(status=False))
    assert "Already up to date" in capsys.readouterr().out


def test_status_prints_current_version_and_pending_modules(monkeypatch, capsys):
    calls = _patch(
        monkeypatch,
        version=1,
        modules=[FakeModule(1, "baseline"), FakeModule(2, "jobs_retry")],
    )
    cli.cmd_migrate(argparse.Namespace(status=True))
    out = capsys.readouterr().out
    assert "Current schema version: 1" in out
    assert "0002_jobs_retry" in out
    assert "0001_baseline" not in out
    assert calls["run"] == 0


def test_status_reports_an_up_to_date_database(monkeypatch, capsys):
    _patch(monkeypatch, version=1, modules=[FakeModule(1, "baseline")])
    cli.cmd_migrate(argparse.Namespace(status=True))
    assert "no pending migrations" in capsys.readouterr().out


def test_parser_exposes_migrate_and_its_status_flag(monkeypatch):
    parsed = {}

    def fake_migrate(args):
        parsed["status"] = args.status

    monkeypatch.setattr(cli, "cmd_migrate", fake_migrate)
    monkeypatch.setattr("sys.argv", ["forge-cli", "migrate", "--status"])
    cli.main()
    assert parsed["status"] is True
