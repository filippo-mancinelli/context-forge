"""0001_baseline replays the frozen DDL; init_db delegates to the runner."""
import asyncio
import inspect

from src import db
from src.migrations import runner
from tests.fake_db import FakeConn, FakePool


def baseline():
    modules = runner.discover_versions()
    assert modules, "no migration modules discovered"
    return modules[0]


def test_baseline_declares_the_module_contract():
    module = baseline()
    assert module.VERSION == 1
    assert module.NAME == "baseline"
    assert module.TRANSACTIONAL is True
    assert inspect.iscoroutinefunction(module.upgrade)


def test_baseline_docstring_freezes_the_ddl():
    doc = baseline().__doc__ or ""
    assert "new version" in doc.lower()


def test_baseline_executes_the_ddl_with_dims_substituted():
    conn = FakeConn()
    asyncio.run(baseline().upgrade(conn))
    assert len(conn.executed) == 1
    sql = conn.executed[0][0]
    assert "CREATE TABLE IF NOT EXISTS repos" in sql
    assert "CREATE TABLE IF NOT EXISTS organizations" in sql
    # str.format has run: the doubled braces are collapsed.
    assert "'{}'::jsonb" in sql
    assert "{dims}" not in sql


def test_init_db_calls_the_runner_and_executes_no_ddl(monkeypatch):
    conn = FakeConn()
    pool = FakePool(conn)
    seen = []

    async def fake_get_pool():
        return pool

    async def fake_run_migrations(arg):
        seen.append(arg)
        return [1]

    monkeypatch.setattr(db, "get_pool", fake_get_pool)
    monkeypatch.setattr(db, "run_migrations", fake_run_migrations)

    asyncio.run(db.init_db())

    assert seen == [pool]
    assert conn.executed == []
    assert "DDL.format" not in inspect.getsource(db.init_db)


def test_ddl_is_marked_frozen_in_db_py():
    source = inspect.getsource(db)
    header = source.split('DDL = """')[0]
    assert "0001_baseline" in header
