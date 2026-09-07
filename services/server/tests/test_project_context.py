import asyncio

from src.mcp import context


def test_project_context_roundtrip():
    context.set_current_project_id(42)
    assert context.get_current_project_id() == 42
    context.set_current_project_id(None)
    assert context.get_current_project_id() is None


def test_resolve_project_id_falls_back_to_default(monkeypatch):
    context.set_current_project_id(None)
    context.set_current_org_id(7)

    async def fake_default(org_id):
        assert org_id == 7
        return 99

    import src.projects as projects_mod
    monkeypatch.setattr(projects_mod, "get_default_project_id", fake_default)

    assert asyncio.run(context.resolve_project_id()) == 99
