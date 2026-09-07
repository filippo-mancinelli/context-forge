"""Tool del grafo: scoping al progetto, permessi e forma della risposta."""
import asyncio
import inspect

import pytest

from src.mcp import code_graph, context
from src.mcp.permissions import set_current_permissions


class _FakeConn:
    def __init__(self, results):
        self.results = results
        self.queries = []
        self.args = []

    async def fetch(self, sql, *args):
        self.queries.append(" ".join(sql.split()))
        self.args.append(args)
        return self.results.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return self._conn


@pytest.fixture(autouse=True)
def _ctx():
    context.set_current_org_id(1)
    context.set_current_project_id(18)
    set_current_permissions(frozenset({"context-read"}))
    yield
    context.set_current_org_id(None)
    context.set_current_project_id(None)
    set_current_permissions(frozenset())


def _install_pool(monkeypatch, results):
    conn = _FakeConn(results)
    from src import db

    async def _get_pool():
        return _FakePool(conn)

    monkeypatch.setattr(db, "get_pool", _get_pool)
    return conn


def test_repo_map_ranks_and_carries_the_repo_name(monkeypatch):
    edges = [{"src_file": "a.java", "dst_file": "core.java", "weight": 4}]
    defs = [
        {"file_path": "core.java", "name": "AssignmentScheduler", "kind": "class_declaration",
         "repo_name": "askme-desk"},
        {"file_path": "a.java", "name": "CallerService", "kind": "class_declaration",
         "repo_name": "askme-desk"},
    ]
    _install_pool(monkeypatch, [edges, defs])

    out = asyncio.run(code_graph.repo_map())
    assert out["status"] == "ok"
    assert out["files"][0]["file_path"] == "core.java"
    assert out["files"][0]["repo_name"] == "askme-desk"
    assert "AssignmentScheduler" in out["files"][0]["defines"]


def test_repo_map_query_changes_the_order(monkeypatch):
    edges = [
        {"src_file": "a.java", "dst_file": "core.java", "weight": 9},
        {"src_file": "b.java", "dst_file": "core.java", "weight": 9},
    ]
    defs = [
        {"file_path": "core.java", "name": "CoreService", "kind": "class_declaration", "repo_name": "r"},
        {"file_path": "sched.java", "name": "availableSlots", "kind": "method_declaration", "repo_name": "r"},
    ]
    _install_pool(monkeypatch, [list(edges), list(defs)])
    plain = asyncio.run(code_graph.repo_map())
    _install_pool(monkeypatch, [list(edges), list(defs)])
    focused = asyncio.run(code_graph.repo_map(query="availableSlots resta a zero"))

    assert plain["files"][0]["file_path"] == "core.java"
    assert focused["files"][0]["file_path"] == "sched.java"


def test_repo_map_scopes_every_query_to_the_project(monkeypatch):
    conn = _install_pool(monkeypatch, [[], []])
    asyncio.run(code_graph.repo_map())
    assert all(a[0] == 18 for a in conn.args), conn.args


def test_repo_map_passes_the_repo_filter(monkeypatch):
    conn = _install_pool(monkeypatch, [[], []])
    asyncio.run(code_graph.repo_map(repos=["askme-desk"]))
    assert all(a[1] == ["askme-desk"] for a in conn.args)
    assert all("repo_name = ANY" in q for q in conn.queries)


def test_repo_map_without_a_graph_says_so(monkeypatch):
    _install_pool(monkeypatch, [[], []])
    out = asyncio.run(code_graph.repo_map())
    assert out["files"] == []
    assert "re-index" in out["note"]


def test_repo_map_needs_a_selected_project(monkeypatch):
    from src import config

    context.set_current_project_id(None)
    monkeypatch.setattr(context, "_selected_project_for_session", lambda: None)
    # Con l'autenticazione MCP attiva non esiste fallback al progetto default.
    monkeypatch.setattr(
        config, "get_settings", lambda: type("S", (), {"mcp_auth_mode": "enabled"})()
    )
    out = asyncio.run(code_graph.repo_map())
    assert out["status"] == "error"
    assert "project" in out["error"].lower()


def test_repo_neighbors_returns_the_three_sections(monkeypatch):
    defined = [{"repo_name": "r", "file_path": "sched.java", "node_type": "method_declaration", "line": 42}]
    referenced = [{"repo_name": "r", "file_path": "caller.java", "occurrences": 3, "line": 10}]
    siblings = [{"file_path": "sched.java", "name": "processStandardCandidates", "node_type": "method_declaration"}]
    _install_pool(monkeypatch, [defined, referenced, siblings])

    out = asyncio.run(code_graph.repo_neighbors(symbol="availableSlots"))
    assert out["defined_in"][0]["file_path"] == "sched.java"
    assert out["referenced_by"][0]["occurrences"] == 3
    assert out["siblings"][0]["name"] == "processStandardCandidates"


def test_repo_neighbors_skips_siblings_when_undefined(monkeypatch):
    conn = _install_pool(monkeypatch, [[], [{"repo_name": "r", "file_path": "x.java", "occurrences": 1, "line": 1}]])
    out = asyncio.run(code_graph.repo_neighbors(symbol="ignoto"))
    assert out["defined_in"] == []
    assert out["siblings"] == []
    assert len(conn.queries) == 2


def test_repo_neighbors_rejects_an_empty_symbol():
    out = asyncio.run(code_graph.repo_neighbors(symbol="   "))
    assert out["status"] == "error"


def test_tools_are_read_only_and_gated():
    for tool in (code_graph.repo_map, code_graph.repo_neighbors):
        source = inspect.getsource(tool)
        assert "DELETE" not in source.upper().replace("DELETED", "")
        assert "INSERT" not in source.upper()
        assert "UPDATE" not in source.upper()
    module_source = inspect.getsource(code_graph)
    assert module_source.count('@requires_permission("context-read")') == 2
