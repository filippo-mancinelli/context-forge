"""0003_mcp_tool_calls: contratto del modulo e DDL applicata."""
import asyncio
import importlib

MODULE = importlib.import_module("src.migrations.versions.0003_mcp_tool_calls")


class FakeConn:
    def __init__(self):
        self.executed = []

    async def execute(self, sql, *args):
        self.executed.append(sql)
        return "OK"


def test_module_contract():
    assert MODULE.VERSION == 3
    assert MODULE.NAME == "mcp_tool_calls"
    assert MODULE.TRANSACTIONAL is True


def test_upgrade_creates_table_indexes_and_column():
    conn = FakeConn()
    asyncio.run(MODULE.upgrade(conn))
    sql = "\n".join(conn.executed)
    assert "CREATE TABLE IF NOT EXISTS mcp_tool_calls" in sql
    assert "mcp_tool_calls_org_created_idx" in sql
    assert "mcp_tool_calls_org_tool_idx" in sql
    assert "ALTER TABLE mcp_api_keys ADD COLUMN IF NOT EXISTS rate_limit_per_minute INT" in sql
