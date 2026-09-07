import asyncio

from src import projects as projects_module
from src.mcp import context as mcp_context
from src.mcp import permissions as perms
from src.mcp import project_access


def _underlying(tool):
    return getattr(tool, "fn", tool)


class _AdminIdentity:
    """Utente Keycloak admin dell'org 1, con il permesso projects-write."""

    def __enter__(self):
        perms.set_current_permissions(frozenset({"projects-write"}))
        mcp_context.set_current_org_id(1)
        mcp_context.set_current_user_id(42)
        return self

    def __exit__(self, *exc):
        perms.set_current_permissions(None)
        mcp_context.set_current_org_id(None)
        mcp_context.set_current_user_id(None)
        mcp_context.set_current_project_id(None)
        return False


def _projects(monkeypatch, rows):
    async def fake_accessible():
        return rows

    monkeypatch.setattr(project_access, "accessible_projects", fake_accessible)


def test_resolve_accessible_matches_id_or_slug(monkeypatch):
    rows = [
        {"id": 4, "slug": "askmeai-v2", "name": "AskmeAI v2", "role": "admin"},
        {"id": 9, "slug": "care", "name": "Care", "role": "member"},
    ]
    _projects(monkeypatch, rows)
    assert asyncio.run(project_access.resolve_accessible("9"))["slug"] == "care"
    assert asyncio.run(project_access.resolve_accessible("askmeai-v2"))["id"] == 4
    assert asyncio.run(project_access.resolve_accessible("nope")) is None


def test_public_project_hides_internal_fields():
    record = {
        "id": 4,
        "slug": "care",
        "name": "Care",
        "role": "admin",
        "memory_namespace": "askme--care",
        "org_id": 1,
    }
    assert project_access.public_project(record) == {
        "id": 4,
        "slug": "care",
        "name": "Care",
        "role": "admin",
    }


class _FakeCtx:
    """Context MCP con un session id, come nel transport streamable-http."""

    def __init__(self, session_id):
        self.session_id = session_id


def test_create_project_selects_it_for_the_session(monkeypatch):
    """La selezione che conta è quella nello store di sessione: i contextvar non
    raggiungono il task in cui girano i tool."""
    from src.mcp import project_admin, session_state

    created = {"id": 12, "org_id": 1, "name": "Nuovo", "slug": "nuovo",
               "memory_namespace": "askme--nuovo"}

    async def fake_create(org_id, name):
        assert (org_id, name) == (1, "Nuovo")
        return created

    async def fake_org(org_id):
        return {"id": 1, "slug": "askme", "name": "Askme"}

    monkeypatch.setattr(projects_module, "create_project", fake_create)
    monkeypatch.setattr(project_admin.tenancy, "get_organization", fake_org)

    with _AdminIdentity():
        result = asyncio.run(
            _underlying(project_admin.create_project)("Nuovo", ctx=_FakeCtx("sess-create"))
        )

    try:
        assert result["status"] == "ok"
        assert result["project"] == {"id": 12, "slug": "nuovo", "name": "Nuovo", "role": None}
        assert session_state.get_selected_project("sess-create") == 12
    finally:
        session_state.clear_session("sess-create")


def test_create_project_reports_reserved_slug_as_an_error(monkeypatch):
    from src.mcp import project_admin

    async def fake_create(org_id, name):
        raise ValueError("Project slug 'oauth' is reserved")

    monkeypatch.setattr(projects_module, "create_project", fake_create)

    with _AdminIdentity():
        result = asyncio.run(_underlying(project_admin.create_project)("OAuth"))

    assert result["status"] == "error"
    assert "reserved" in result["error"]


def test_create_project_returns_the_dedicated_mcp_url(monkeypatch):
    from src.mcp import project_admin
    from src import config as config_module

    created = {"id": 12, "org_id": 1, "name": "Nuovo", "slug": "nuovo",
               "memory_namespace": "askme--nuovo"}

    async def fake_create(org_id, name):
        return created

    async def fake_org(org_id):
        return {"id": 1, "slug": "askme", "name": "Askme"}

    settings = config_module.get_settings()
    monkeypatch.setattr(settings, "public_mcp_url", "https://care.example.com", raising=False)
    monkeypatch.setattr(projects_module, "create_project", fake_create)
    monkeypatch.setattr(project_admin.tenancy, "get_organization", fake_org)

    with _AdminIdentity():
        result = asyncio.run(_underlying(project_admin.create_project)("Nuovo"))

    assert result["mcp_url"] == "https://care.example.com/mcp/askme/nuovo"


def test_rename_project_keeps_the_slug(monkeypatch):
    from src.mcp import project_admin

    async def fake_accessible():
        return [{"id": 4, "org_id": 1, "slug": "care", "name": "Care", "role": "admin",
                 "memory_namespace": "askme--care"}]

    renamed = {"id": 4, "org_id": 1, "slug": "care", "name": "ContextForge",
               "memory_namespace": "askme--care"}

    async def fake_update(project_id, name):
        assert (project_id, name) == (4, "ContextForge")
        return renamed

    monkeypatch.setattr(project_access, "accessible_projects", fake_accessible)
    monkeypatch.setattr(projects_module, "update_project", fake_update)

    with _AdminIdentity():
        result = asyncio.run(_underlying(project_admin.rename_project)("care", "ContextForge"))

    assert result["status"] == "ok"
    assert result["project"]["name"] == "ContextForge"
    assert result["project"]["slug"] == "care"


def test_rename_project_refuses_a_project_it_cannot_see(monkeypatch):
    from src.mcp import project_admin

    async def fake_accessible():
        return []

    calls = []

    async def fake_update(project_id, name):
        calls.append(project_id)
        return None

    monkeypatch.setattr(project_access, "accessible_projects", fake_accessible)
    monkeypatch.setattr(projects_module, "update_project", fake_update)

    with _AdminIdentity():
        result = asyncio.run(_underlying(project_admin.rename_project)("other-org-project", "X"))

    assert result["status"] == "error"
    assert calls == []


def _scratch_project(monkeypatch):
    async def fake_accessible():
        return [{"id": 4, "org_id": 1, "slug": "scratch", "name": "Scratch", "role": "admin",
                 "memory_namespace": "askme--scratch"}]

    monkeypatch.setattr(project_access, "accessible_projects", fake_accessible)


def _no_memories(monkeypatch):
    """mem0 vuoto per il namespace del progetto."""
    from src.mcp import project_admin

    async def fake_count(namespace):
        return 0

    monkeypatch.setattr(project_admin, "_count_memories", fake_count)


def test_delete_project_removes_an_empty_project(monkeypatch):
    from src.mcp import project_admin

    async def fake_counts(project_id):
        return {}

    deleted = []

    async def fake_delete(project_id):
        deleted.append(project_id)
        return True

    _scratch_project(monkeypatch)
    monkeypatch.setattr(projects_module, "count_project_resources", fake_counts)
    monkeypatch.setattr(projects_module, "delete_project", fake_delete)
    _no_memories(monkeypatch)

    with _AdminIdentity():
        result = asyncio.run(_underlying(project_admin.delete_project)("scratch"))

    assert result["status"] == "ok"
    assert deleted == [4]


def test_delete_project_refuses_a_project_with_resources(monkeypatch):
    from src.mcp import project_admin

    async def fake_counts(project_id):
        return {"repos": 2, "ssh_sources": 1}

    deleted = []

    async def fake_delete(project_id):
        deleted.append(project_id)
        return True

    _scratch_project(monkeypatch)
    monkeypatch.setattr(projects_module, "count_project_resources", fake_counts)
    monkeypatch.setattr(projects_module, "delete_project", fake_delete)
    _no_memories(monkeypatch)

    with _AdminIdentity():
        result = asyncio.run(_underlying(project_admin.delete_project)("scratch"))

    assert result["status"] == "error"
    assert result["resources"] == {"repos": 2, "ssh_sources": 1}
    assert deleted == []


def test_delete_project_refuses_when_the_namespace_still_has_memories(monkeypatch):
    from src.mcp import project_admin

    async def fake_counts(project_id):
        return {}

    async def fake_memories(namespace):
        assert namespace == "askme--scratch"
        return 3

    deleted = []

    async def fake_delete(project_id):
        deleted.append(project_id)
        return True

    _scratch_project(monkeypatch)
    monkeypatch.setattr(projects_module, "count_project_resources", fake_counts)
    monkeypatch.setattr(projects_module, "delete_project", fake_delete)
    monkeypatch.setattr(project_admin, "_count_memories", fake_memories)

    with _AdminIdentity():
        result = asyncio.run(_underlying(project_admin.delete_project)("scratch"))

    assert result["status"] == "error"
    assert result["resources"] == {"memories": 3}
    assert deleted == []


def test_delete_project_refuses_when_memories_cannot_be_checked(monkeypatch):
    """Se mem0 non risponde non si può dichiarare vuoto il progetto."""
    from src.mcp import project_admin

    async def fake_counts(project_id):
        return {}

    async def fake_memories(namespace):
        return None

    deleted = []

    async def fake_delete(project_id):
        deleted.append(project_id)
        return True

    _scratch_project(monkeypatch)
    monkeypatch.setattr(projects_module, "count_project_resources", fake_counts)
    monkeypatch.setattr(projects_module, "delete_project", fake_delete)
    monkeypatch.setattr(project_admin, "_count_memories", fake_memories)

    with _AdminIdentity():
        result = asyncio.run(_underlying(project_admin.delete_project)("scratch"))

    assert result["status"] == "error"
    assert deleted == []


def test_delete_project_never_removes_the_default_project(monkeypatch):
    from src.mcp import project_admin

    async def fake_accessible():
        return [{"id": 1, "org_id": 1, "slug": "default", "name": "Default", "role": "admin",
                 "memory_namespace": "askme--default"}]

    deleted = []

    async def fake_delete(project_id):
        deleted.append(project_id)
        return True

    monkeypatch.setattr(project_access, "accessible_projects", fake_accessible)
    monkeypatch.setattr(projects_module, "delete_project", fake_delete)

    with _AdminIdentity():
        result = asyncio.run(_underlying(project_admin.delete_project)("default"))

    assert result["status"] == "error"
    assert deleted == []


def _member_fixtures(monkeypatch, caller_role="owner", target_org_role="member"):
    from src.mcp import project_admin
    from src.api import security as api_security

    async def fake_accessible():
        return [{"id": 4, "org_id": 1, "slug": "care", "name": "Care", "role": "admin",
                 "memory_namespace": "askme--care"}]

    async def fake_email(email):
        return 99 if email == "mario@lascaux.it" else None

    async def fake_membership(org_id, user_id):
        if user_id == 42:
            return caller_role
        return target_org_role

    added = []

    async def fake_add(project_id, user_id, role="member"):
        added.append((project_id, user_id, role))

    monkeypatch.setattr(project_access, "accessible_projects", fake_accessible)
    monkeypatch.setattr(api_security, "get_user_id_by_email", fake_email)
    monkeypatch.setattr(project_admin.tenancy, "get_membership_role", fake_membership)
    monkeypatch.setattr(projects_module, "add_project_member", fake_add)
    return added


def test_add_project_member_by_email(monkeypatch):
    from src.mcp import project_admin
    added = _member_fixtures(monkeypatch)

    with _AdminIdentity():
        result = asyncio.run(
            _underlying(project_admin.add_project_member)("mario@lascaux.it", "member", "care")
        )

    assert result["status"] == "ok"
    assert added == [(4, 99, "member")]


def test_only_an_owner_chooses_the_project_role(monkeypatch):
    from src.mcp import project_admin
    added = _member_fixtures(monkeypatch, caller_role="admin")

    with _AdminIdentity():
        result = asyncio.run(
            _underlying(project_admin.add_project_member)("mario@lascaux.it", "admin", "care")
        )

    assert result["member"]["role"] == "member"
    assert added == [(4, 99, "member")]


def test_owner_can_grant_the_admin_project_role(monkeypatch):
    from src.mcp import project_admin
    added = _member_fixtures(monkeypatch, caller_role="owner")

    with _AdminIdentity():
        result = asyncio.run(
            _underlying(project_admin.add_project_member)("mario@lascaux.it", "admin", "care")
        )

    assert result["member"]["role"] == "admin"
    assert added == [(4, 99, "admin")]


def test_add_project_member_refuses_a_user_outside_the_organization(monkeypatch):
    from src.mcp import project_admin
    added = _member_fixtures(monkeypatch, target_org_role=None)

    with _AdminIdentity():
        result = asyncio.run(
            _underlying(project_admin.add_project_member)("mario@lascaux.it", "member", "care")
        )

    assert result["status"] == "error"
    assert "not a member of this organization" in result["error"]
    assert added == []


def test_add_project_member_refuses_an_invalid_role(monkeypatch):
    from src.mcp import project_admin
    added = _member_fixtures(monkeypatch)

    with _AdminIdentity():
        result = asyncio.run(
            _underlying(project_admin.add_project_member)("mario@lascaux.it", "owner", "care")
        )

    assert result["status"] == "error"
    assert added == []


def test_add_project_member_defaults_to_the_selected_project(monkeypatch):
    from src.mcp import project_admin
    added = _member_fixtures(monkeypatch)

    async def fake_get(project_id):
        return {"id": 4, "org_id": 1, "slug": "care", "name": "Care",
                "memory_namespace": "askme--care"}

    monkeypatch.setattr(projects_module, "get_project", fake_get)

    with _AdminIdentity():
        mcp_context.set_current_project_id(4)
        result = asyncio.run(
            _underlying(project_admin.add_project_member)("mario@lascaux.it")
        )

    assert result["status"] == "ok"
    assert added == [(4, 99, "member")]


def test_add_project_member_without_a_project_is_an_error(monkeypatch):
    from src.mcp import project_admin
    added = _member_fixtures(monkeypatch)

    with _AdminIdentity():
        result = asyncio.run(
            _underlying(project_admin.add_project_member)("mario@lascaux.it")
        )

    assert result["status"] == "error"
    assert added == []
