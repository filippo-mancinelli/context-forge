import asyncio

from src.mcp import context, project_tools, session_state


def _reset():
    context.set_current_org_id(1)
    context.set_current_project_id(None)
    context.set_current_user_id(None)
    context.set_current_allowed_projects(None)


PROJECTS = [
    {"id": 3, "org_id": 1, "name": "Secondo", "slug": "secondo",
     "memory_namespace": "acme--secondo", "role": "member"},
    {"id": 4, "org_id": 1, "name": "askmeai-v2", "slug": "askmeai-v2",
     "memory_namespace": "acme--askmeai-v2", "role": "member"},
]


def _patch_user_projects(monkeypatch, rows):
    async def fake_list(org_id, user_id):
        return rows

    import src.projects as projects_mod
    monkeypatch.setattr(projects_mod, "list_accessible_projects", fake_list)


def test_list_projects_for_user(monkeypatch):
    _reset()
    context.set_current_user_id(12)
    _patch_user_projects(monkeypatch, PROJECTS)

    out = asyncio.run(project_tools.list_projects())
    assert [p["slug"] for p in out["projects"]] == ["secondo", "askmeai-v2"]
    assert out["selected_project_id"] is None


class _FakeCtx:
    def __init__(self, session_id):
        self.session_id = session_id


def test_use_project_selects_accessible(monkeypatch):
    _reset()
    context.set_current_user_id(12)
    _patch_user_projects(monkeypatch, PROJECTS)

    out = asyncio.run(project_tools.use_project("askmeai-v2", _FakeCtx("s1")))
    assert out["status"] == "ok"
    assert out["project"]["id"] == 4
    # La selezione è persistita per la sessione MCP.
    assert session_state.get_selected_project("s1") == 4


def test_use_project_by_id(monkeypatch):
    _reset()
    context.set_current_user_id(12)
    _patch_user_projects(monkeypatch, PROJECTS)

    out = asyncio.run(project_tools.use_project("3"))
    assert out["status"] == "ok"
    assert out["project"]["slug"] == "secondo"


def test_use_project_rejects_inaccessible(monkeypatch):
    _reset()
    context.set_current_user_id(12)
    _patch_user_projects(monkeypatch, [PROJECTS[0]])  # solo 'secondo'

    out = asyncio.run(project_tools.use_project("askmeai-v2"))
    assert out["status"] == "error"
    assert context.get_current_project_id() is None


def test_api_key_org_wide_lists_all(monkeypatch):
    _reset()
    # nessun user_id -> ramo API key; allowed None -> org-wide
    async def fake_list_projects(org_id):
        return PROJECTS

    import src.projects as projects_mod
    monkeypatch.setattr(projects_mod, "list_projects", fake_list_projects)

    out = asyncio.run(project_tools.list_projects())
    assert {p["id"] for p in out["projects"]} == {3, 4}


def test_api_key_scoped_lists_only_allowed(monkeypatch):
    _reset()
    context.set_current_allowed_projects(frozenset({4}))

    async def fake_get_project(pid):
        return next((p for p in PROJECTS if p["id"] == pid), None)

    import src.projects as projects_mod
    monkeypatch.setattr(projects_mod, "get_project", fake_get_project)

    out = asyncio.run(project_tools.list_projects())
    assert [p["id"] for p in out["projects"]] == [4]
