"""La migrazione 0004 crea la tabella delle write request e il suo indice."""
import asyncio
import importlib.util
from pathlib import Path

from tests.fake_db import FakeConn

_PATH = Path(__file__).resolve().parents[1] / "src" / "migrations" / "versions" / "0004_write_requests.py"
_spec = importlib.util.spec_from_file_location("m0004", _PATH)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def test_module_declares_the_contract():
    assert mod.VERSION == 4
    assert mod.NAME == "write_requests"
    assert mod.TRANSACTIONAL is True


def test_upgrade_creates_table_and_index():
    conn = FakeConn()
    asyncio.run(mod.upgrade(conn))
    sql = "\n".join(conn.sql)
    assert "CREATE TABLE IF NOT EXISTS write_requests" in sql
    assert "status         TEXT NOT NULL DEFAULT 'pending'" in sql
    assert "write_requests_org_status_idx" in sql
    assert "INTERVAL '7 days'" in sql
