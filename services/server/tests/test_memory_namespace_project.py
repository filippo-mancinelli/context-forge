"""La memoria segue il progetto scelto con use_project.

Sull'endpoint org-level il namespace nel context e' quello dell'organizzazione:
viene fissato dal middleware all'inizializzazione della sessione, prima che
esista una scelta di progetto. Se i tool di memoria si fermassero li', scrivere
o cercare dentro un progetto finirebbe nel calderone dell'organizzazione.
"""
import asyncio

import pytest

from src.mcp import context


class _FakeCtx:
    def __init__(self, session_id):
        self.session_id = session_id


@pytest.fixture(autouse=True)
def _clean_context():
    context.set_current_namespace(None)
    context.set_current_project_id(None)
    yield
    context.set_current_namespace(None)
    context.set_current_project_id(None)


def _fake_project(monkeypatch, project_id, namespace):
    from src import projects

    async def _get_project(pid):
        assert pid == project_id
        return {"id": pid, "memory_namespace": namespace}

    monkeypatch.setattr(projects, "get_project", _get_project)


def test_namespace_follows_the_project_selected_in_session(monkeypatch):
    # Org-level: il context porta il namespace dell'org, la scelta vive in sessione.
    context.set_current_namespace("org_askme")
    monkeypatch.setattr(context, "get_selected_project_id", lambda: 18)
    _fake_project(monkeypatch, 18, "askme--360-welfare")

    assert asyncio.run(context.resolve_memory_namespace()) == "askme--360-welfare"


def test_namespace_falls_back_to_the_context_without_a_selection(monkeypatch):
    context.set_current_namespace("org_askme")
    monkeypatch.setattr(context, "get_selected_project_id", lambda: None)

    assert asyncio.run(context.resolve_memory_namespace()) == "org_askme"


def test_namespace_falls_back_when_the_project_has_none(monkeypatch):
    context.set_current_namespace("org_askme")
    monkeypatch.setattr(context, "get_selected_project_id", lambda: 18)
    from src import projects

    async def _get_project(pid):
        return {"id": pid, "memory_namespace": None}

    monkeypatch.setattr(projects, "get_project", _get_project)

    assert asyncio.run(context.resolve_memory_namespace()) == "org_askme"


def test_namespace_on_a_project_endpoint_is_the_project_one(monkeypatch):
    # Endpoint /mcp/{org}/{project}: il middleware ha gia' messo il namespace
    # giusto nel context e non c'e' nessuna selezione di sessione.
    context.set_current_namespace("askme--askme-desk")
    context.set_current_project_id(7)
    monkeypatch.setattr(context, "get_selected_project_id", lambda: 7)
    _fake_project(monkeypatch, 7, "askme--askme-desk")

    assert asyncio.run(context.resolve_memory_namespace()) == "askme--askme-desk"


def test_memory_tools_use_the_selected_project_namespace(monkeypatch):
    """I tool di memoria scrivono e cercano nel namespace del progetto scelto."""
    from src.mcp import memory as memory_tools

    context.set_current_namespace("org_askme")
    monkeypatch.setattr(context, "get_selected_project_id", lambda: 18)
    _fake_project(monkeypatch, 18, "askme--360-welfare")

    seen: dict[str, str] = {}

    class _Mem:
        def add(self, content, user_id=None, metadata=None, infer=True):
            seen["add"] = user_id
            return {"results": []}

        def search(self, query, user_id=None, limit=20):
            seen["search"] = user_id
            return {"results": []}

        def get_all(self, user_id=None):
            seen["get_all"] = user_id
            return {"results": []}

    async def _get_memory(org_id):
        return _Mem()

    monkeypatch.setattr(memory_tools, "_get_memory", _get_memory)
    monkeypatch.setattr(context, "resolve_org_id", _async_value(1))
    from src.mcp.permissions import set_current_permissions

    set_current_permissions(frozenset({"context-read", "context-write"}))

    asyncio.run(memory_tools.memory_add(content="nota"))
    asyncio.run(memory_tools.memory_search(query="loop"))
    asyncio.run(memory_tools.memory_list())

    assert seen == {
        "add": "askme--360-welfare",
        "search": "askme--360-welfare",
        "get_all": "askme--360-welfare",
    }


def _async_value(value):
    async def _fn():
        return value

    return _fn
