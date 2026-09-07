"""Namespace isolation for the memory MCP tools (src/mcp/memory.py).

memory_add/memory_search/memory_list must resolve their mem0 user_id solely
from the request-scoped namespace (get_current_namespace), never from a
caller-supplied parameter — otherwise any MCP caller could read or write
another project's/org's memories by passing an arbitrary user_id. memory_delete
must verify the target memory belongs to the current namespace before issuing
the delete, since mem0's delete-by-id does not filter by user_id.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from src.mcp import memory
from src.mcp.context import set_current_namespace, set_current_org_id
from src.mcp.permissions import ToolError, set_current_permissions


def _fake_get_memory(fake):
    """Build a patch for memory._get_memory(org_id) returning a fixed client."""
    async def _get(org_id):
        return fake

    return _get


def _underlying(tool):
    return getattr(tool, "fn", tool)


def test_memory_tools_have_no_user_id_parameter():
    for fn in (memory.memory_add, memory.memory_search, memory.memory_list):
        params = inspect.signature(_underlying(fn)).parameters
        assert "user_id" not in params, f"{fn} must not accept a caller-supplied user_id"


def test_memory_module_resolves_uid_from_namespace_only():
    source = inspect.getsource(memory)
    assert "user_id or" not in source
    # memory_add, memory_search, memory_list, and memory_delete each resolve
    # uid this way.
    assert (
        source.count("await resolve_memory_namespace() or get_forge_config().memory.user_id") == 4
    )


def test_memory_add_uses_context_namespace(monkeypatch):
    calls = []

    class _FakeMem:
        def add(self, content, user_id=None, metadata=None, infer=False):
            calls.append(user_id)
            return {"results": [{"id": "mem-1"}]}

    monkeypatch.setattr(memory, "_get_memory", _fake_get_memory(_FakeMem()))
    set_current_namespace("org_acme_projA")
    set_current_org_id(1)
    set_current_permissions(frozenset({"*"}))
    try:
        result = asyncio.run(_underlying(memory.memory_add)("hello"))
    finally:
        set_current_namespace(None)
        set_current_org_id(None)
        set_current_permissions(None)

    assert result["status"] == "ok"
    assert calls == ["org_acme_projA"]


def test_memory_search_uses_context_namespace(monkeypatch):
    calls = []

    class _FakeMem:
        def search(self, query, user_id=None, limit=10):
            calls.append(user_id)
            return {"results": []}

    monkeypatch.setattr(memory, "_get_memory", _fake_get_memory(_FakeMem()))
    set_current_namespace("org_acme_projA")
    set_current_org_id(1)
    set_current_permissions(frozenset({"*"}))
    try:
        result = asyncio.run(_underlying(memory.memory_search)("query"))
    finally:
        set_current_namespace(None)
        set_current_org_id(None)
        set_current_permissions(None)

    assert result["status"] == "ok"
    assert calls == ["org_acme_projA"]


def test_memory_delete_rejects_memory_outside_namespace(monkeypatch):
    class _FakeMem:
        def get(self, memory_id):
            return {"id": memory_id, "user_id": "org_other_project", "memory": "secret"}

        def delete(self, memory_id):
            raise AssertionError("delete must not be called across namespaces")

    monkeypatch.setattr(memory, "_get_memory", _fake_get_memory(_FakeMem()))
    set_current_namespace("org_acme_projA")
    set_current_org_id(1)
    set_current_permissions(frozenset({"*"}))
    try:
        with pytest.raises(ToolError):
            asyncio.run(_underlying(memory.memory_delete)("mem-1"))
    finally:
        set_current_namespace(None)
        set_current_org_id(None)
        set_current_permissions(None)


def test_memory_delete_rejects_unknown_memory(monkeypatch):
    class _FakeMem:
        def get(self, memory_id):
            return None

        def delete(self, memory_id):
            raise AssertionError("delete must not be called for an unresolvable memory")

    monkeypatch.setattr(memory, "_get_memory", _fake_get_memory(_FakeMem()))
    set_current_namespace("org_acme_projA")
    set_current_org_id(1)
    set_current_permissions(frozenset({"*"}))
    try:
        with pytest.raises(ToolError):
            asyncio.run(_underlying(memory.memory_delete)("mem-1"))
    finally:
        set_current_namespace(None)
        set_current_org_id(None)
        set_current_permissions(None)


def test_memory_delete_allows_memory_in_namespace(monkeypatch):
    deleted = []

    class _FakeMem:
        def get(self, memory_id):
            return {"id": memory_id, "user_id": "org_acme_projA", "memory": "ok"}

        def delete(self, memory_id):
            deleted.append(memory_id)

    monkeypatch.setattr(memory, "_get_memory", _fake_get_memory(_FakeMem()))
    set_current_namespace("org_acme_projA")
    set_current_org_id(1)
    set_current_permissions(frozenset({"*"}))
    try:
        result = asyncio.run(_underlying(memory.memory_delete)("mem-1"))
    finally:
        set_current_namespace(None)
        set_current_org_id(None)
        set_current_permissions(None)

    assert result == {"status": "ok", "deleted": "mem-1"}
    assert deleted == ["mem-1"]


def test_rest_delete_memory_route_scoped_to_active_project():
    import src.api.routes.memory as memory_routes

    source = inspect.getsource(memory_routes.delete_memory)
    assert "get_active_project" in source
    assert "project.namespace" in source
