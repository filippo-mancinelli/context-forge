import asyncio

import pytest

from src.mcp import permissions as perms


def test_groups_mapping():
    got = perms.permissions_from_groups(
        ["/mcp-tools/context-read", "/mcp-tools/db-query", "/other/x", "/mcp-tools/unknown"]
    )
    assert got == frozenset({"context-read", "db-query"})


def test_admin_group_is_wildcard():
    assert perms.permissions_from_groups(["/mcp-tools/admin"]) == frozenset({"*"})


def test_empty_groups():
    assert perms.permissions_from_groups(None) == frozenset()
    assert perms.permissions_from_groups([]) == frozenset()


def test_decorator_blocks_and_allows():
    calls = []

    @perms.requires_permission("repo-write")
    async def tool(x: int) -> int:
        calls.append(x)
        return x * 2

    async def _test():
        perms.set_current_permissions(None)
        assert await tool(1) == 2  # auth disabled -> allowed

        perms.set_current_permissions(frozenset({"*"}))
        assert await tool(2) == 4  # wildcard -> allowed

        perms.set_current_permissions(frozenset({"context-read"}))
        with pytest.raises(Exception, match="repo-write"):
            await tool(3)
        assert calls == [1, 2]

        perms.set_current_permissions(None)

    asyncio.run(_test())
