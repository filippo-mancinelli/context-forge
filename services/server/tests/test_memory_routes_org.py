"""REST memory call sites resolve the per-org Mem0 client.

src.mcp.memory._get_memory became async and org-scoped (org_id: int). The REST
helpers in chat.py, memory.py, and webhooks.py must await it with the correct
org_id rather than calling it with zero arguments — a stale call site would
either raise a TypeError (missing arg) or hand an un-awaited coroutine to
mem.add/search/get_all/delete.
"""
from __future__ import annotations

import asyncio

from src.api.deps import ActiveProject
from src.mcp import memory as memory_mod


def _fake_get_memory(recorded_org_ids, fake_client):
    """Patch for src.mcp.memory._get_memory(org_id) that records org_id and
    returns a fixed fake Mem0 client."""
    async def _get(org_id):
        recorded_org_ids.append(org_id)
        return fake_client

    return _get


class _FakeMem:
    def __init__(self):
        self.calls = []

    def add(self, content, user_id=None, **kwargs):
        self.calls.append(("add", user_id))
        return {"id": "mem-1"}

    def search(self, query, user_id=None, **kwargs):
        self.calls.append(("search", user_id))
        return {"results": []}

    def get_all(self, user_id=None, **kwargs):
        self.calls.append(("get_all", user_id))
        return {"results": []}

    def get(self, memory_id):
        return {"id": memory_id, "user_id": "ns"}

    def delete(self, memory_id):
        self.calls.append(("delete", memory_id))


def _project(org_id=7, namespace="ns"):
    return ActiveProject(
        org_id=org_id, project_id=1, role="member", namespace=namespace,
        name="p", org_name="org",
    )


def test_chat_search_memory_resolves_org_scoped_client(monkeypatch):
    import src.api.routes.chat as chat

    recorded = []
    fake = _FakeMem()
    monkeypatch.setattr(memory_mod, "_get_memory", _fake_get_memory(recorded, fake))

    asyncio.run(chat._search_memory(_project(org_id=7), {"query": "x"}))

    assert recorded == [7]
    assert ("search", "ns") in fake.calls


def test_chat_add_and_delete_memory_resolve_org_scoped_client(monkeypatch):
    import src.api.routes.chat as chat

    recorded = []
    fake = _FakeMem()
    monkeypatch.setattr(memory_mod, "_get_memory", _fake_get_memory(recorded, fake))

    asyncio.run(chat._add_memory(_project(org_id=7), {"content": "note"}))
    asyncio.run(chat._delete_memory(_project(org_id=7), {"memory_id": "mem-1"}))

    assert recorded == [7, 7]
    assert ("add", "ns") in fake.calls
    assert ("delete", "mem-1") in fake.calls


def test_memory_routes_resolve_org_scoped_client(monkeypatch):
    import src.api.routes.memory as memory_routes

    recorded = []
    fake = _FakeMem()
    monkeypatch.setattr(memory_mod, "_get_memory", _fake_get_memory(recorded, fake))

    project = _project(org_id=9)
    asyncio.run(memory_routes.add_memory(
        memory_routes.MemoryAddRequest(content="note"), project=project
    ))
    asyncio.run(memory_routes.list_memories(project=project))
    asyncio.run(memory_routes.search_memories(
        memory_routes.MemorySearchRequest(query="x"), project=project
    ))

    assert recorded == [9, 9, 9]
    assert ("add", "ns") in fake.calls
    assert ("get_all", "ns") in fake.calls
    assert ("search", "ns") in fake.calls


def test_webhook_auto_memory_resolves_client_per_match_org(monkeypatch):
    """The Mem0 client is resolved inside the match loop (not once before it),
    since matches can span multiple orgs and each has its own client."""
    import inspect

    import src.api.routes.webhooks as webhooks

    source = inspect.getsource(webhooks.webhook_index)
    for_loop_idx = source.index("for _org_id, project_id, repo_name in matches:")
    pre_loop = source[:for_loop_idx]
    loop_body = source[for_loop_idx:]

    assert "_get_memory()" not in pre_loop
    assert "await _get_memory(_org_id)" in loop_body
