import asyncio

import pytest

from src.mcp import permissions as perms


def _underlying(tool):
    return getattr(tool, "fn", tool)


def test_read_only_caller_cannot_write_memory():
    from src.mcp import memory
    perms.set_current_permissions(frozenset({"context-read"}))
    try:
        with pytest.raises(Exception, match="context-write"):
            asyncio.run(_underlying(memory.memory_add)("x"))
    finally:
        perms.set_current_permissions(None)


def test_read_only_caller_cannot_run_db_query():
    from src.mcp import datasources
    perms.set_current_permissions(frozenset({"context-read"}))
    try:
        with pytest.raises(Exception, match="db-query"):
            asyncio.run(_underlying(datasources.db_query)("SELECT 1"))
    finally:
        perms.set_current_permissions(None)
