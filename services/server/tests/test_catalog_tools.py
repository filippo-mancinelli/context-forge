import asyncio

import pytest

from src import projects as projects_module
from src import tenancy as tenancy_module
from src.catalog import selections as selections_module
from src.datasources import project_scopes as project_scopes_module
from src.datasources.scopes import ScopeShapeError
from src.mcp import catalog_tools
from src.mcp import context as mcp_context
from src.mcp import permissions as perms


def _underlying(tool):
    return getattr(tool, "fn", tool)


def _principal(user_id):
    """A user principal, or an API key when there is no user id."""
    if user_id is None:
        return mcp_context.Principal(kind="api_key", id=9, label="key")
    return mcp_context.Principal(kind="user", id=user_id, label=f"user-{user_id}")


class _Identity:
    """Org 1, progetto 4, con i permessi di lettura e scrittura del contesto."""

    def __init__(self, user_id=42):
        self.user_id = user_id

    def __enter__(self):
        perms.set_current_permissions(frozenset({"context-read", "context-write"}))
        mcp_context.set_current_org_id(1)
        mcp_context.set_current_project_id(4)
        mcp_context.set_current_user_id(self.user_id)
        mcp_context.set_current_principal(_principal(self.user_id))
        return self

    def __exit__(self, *exc):
        perms.set_current_permissions(None)
        mcp_context.set_current_org_id(None)
        mcp_context.set_current_project_id(None)
        mcp_context.set_current_user_id(None)
        mcp_context.set_current_principal(None)
        return False


def _roles(monkeypatch, project_role, org_role):
    async def fake_project_access(org_id, user_id, project_id):
        return project_role

    async def fake_membership(org_id, user_id):
        return org_role

    monkeypatch.setattr(projects_module, "resolve_project_access", fake_project_access)
    monkeypatch.setattr(tenancy_module, "get_membership_role", fake_membership)


def _resource(monkeypatch, resource):
    async def fake_find(org_id, kind, name):
        return resource

    monkeypatch.setattr(catalog_tools.selections, "find_resource_by_name", fake_find)


def test_catalog_list_marks_what_the_project_already_selected(monkeypatch):
    async def fake_folders(org_id):
        return [
            {"id": 1, "name": "logs", "machine_name": "m", "root_path": "/srv", "description": None, "restricted": False},
            {"id": 2, "name": "reports", "machine_name": "m", "root_path": "/r", "description": None, "restricted": True},
        ]

    async def fake_databases(org_id):
        return [{"id": 7, "name": "erp", "engine": "mysql", "host": "h", "database_name": "d",
                 "ssh_machine_name": None, "description": None, "restricted": False}]

    async def fake_repos(org_id):
        return [{"id": 9, "name": "aster-desk", "type": "gitlab", "url": "https://git.example.org/aster/aster-desk",
                 "branch": "main", "status": "indexed", "description": None, "restricted": False}]

    async def fake_selected(project_id, kind):
        return {1} if kind == "folders" else {9} if kind == "repos" else set()

    async def fake_scopes(org_id, project_id):
        return []

    monkeypatch.setattr(catalog_tools.ssh_service, "list_catalog", fake_folders)
    monkeypatch.setattr(catalog_tools.db_service, "list_catalog_connections", fake_databases)
    monkeypatch.setattr(catalog_tools.repo_catalog, "list_catalog", fake_repos)
    monkeypatch.setattr(catalog_tools.selections, "selected_ids", fake_selected)
    monkeypatch.setattr(catalog_tools.db_service, "list_connections", fake_scopes)

    with _Identity():
        result = asyncio.run(_underlying(catalog_tools.catalog_list)())

    assert [(f["name"], f["selected"], f["restricted"]) for f in result["folders"]] == [
        ("logs", True, False), ("reports", False, True),
    ]
    assert result["databases"][0]["selected"] is False
    assert [(r["name"], r["selected"]) for r in result["repos"]] == [("aster-desk", True)]


def test_catalog_list_rejects_an_unknown_kind():
    with _Identity():
        result = asyncio.run(_underlying(catalog_tools.catalog_list)("connectors"))
    assert result["status"] == "error"


def test_project_member_selects_without_the_restricted_right(monkeypatch):
    calls = []
    _roles(monkeypatch, "member", "member")
    _resource(monkeypatch, {"id": 1, "name": "logs", "restricted": False})

    async def fake_select(org_id, project_id, kind, resource_id, user_id, can_select_restricted):
        calls.append((org_id, project_id, kind, resource_id, user_id, can_select_restricted))
        return {"kind": kind, "resource_id": resource_id, "name": "logs", "already_selected": False}

    monkeypatch.setattr(catalog_tools.selections, "select_resource", fake_select)

    with _Identity():
        result = asyncio.run(_underlying(catalog_tools.resource_select)("folders", "logs"))

    assert result["status"] == "ok"
    assert calls == [(1, 4, "folders", 1, 42, False)]


def test_viewer_cannot_select(monkeypatch):
    _roles(monkeypatch, "viewer", "member")

    async def must_not_select(*args):
        raise AssertionError("select_resource must not be called")

    monkeypatch.setattr(catalog_tools.selections, "select_resource", must_not_select)

    # A refusal for missing rights is a PermissionDenied, audited as denied.
    with _Identity():
        with pytest.raises(perms.PermissionDenied, match="member role"):
            asyncio.run(_underlying(catalog_tools.resource_select)("folders", "logs"))


def test_restricted_resource_is_reported(monkeypatch):
    _roles(monkeypatch, "member", "member")
    _resource(monkeypatch, {"id": 2, "name": "reports", "restricted": True})

    async def fake_select(*args):
        raise selections_module.RestrictedResourceError("reports")

    monkeypatch.setattr(catalog_tools.selections, "select_resource", fake_select)

    with _Identity():
        with pytest.raises(perms.PermissionDenied, match="restricted"):
            asyncio.run(_underlying(catalog_tools.resource_select)("folders", "reports"))


def test_api_key_never_gets_the_restricted_right(monkeypatch):
    captured = []
    _resource(monkeypatch, {"id": 1, "name": "logs", "restricted": False})

    async def fake_select(org_id, project_id, kind, resource_id, user_id, can_select_restricted):
        captured.append((user_id, can_select_restricted))
        return {"kind": kind, "resource_id": resource_id, "name": "logs", "already_selected": True}

    monkeypatch.setattr(catalog_tools.selections, "select_resource", fake_select)

    with _Identity(user_id=None):
        asyncio.run(_underlying(catalog_tools.resource_select)("folders", "logs"))

    assert captured == [(None, False)]


def test_unknown_name_and_missing_selection(monkeypatch):
    _roles(monkeypatch, "member", "member")
    _resource(monkeypatch, None)

    with _Identity():
        unknown = asyncio.run(_underlying(catalog_tools.resource_select)("folders", "nowhere"))

    _resource(monkeypatch, {"id": 1, "name": "logs", "restricted": False})

    async def fake_deselect(org_id, project_id, kind, resource_id):
        return False

    monkeypatch.setattr(catalog_tools.selections, "deselect_resource", fake_deselect)

    with _Identity():
        missing = asyncio.run(_underlying(catalog_tools.resource_deselect)("folders", "logs"))

    assert unknown["status"] == "error" and "not in the folders catalog" in unknown["error"]
    assert missing["status"] == "error" and "not selected" in missing["error"]


def test_db_tools_say_when_a_connection_is_not_selected(monkeypatch):
    from src.datasources import service
    from src.mcp import datasources

    async def not_selected(org_id, project_id, ref, include_secret=False):
        raise service.ConnectionNotSelectedError(
            f"Database connection '{ref}' is not available in this project. "
            "Select it from the catalog first (catalog_list, resource_select)."
        )

    async def nothing_selected(org_id, project_id):
        return []

    monkeypatch.setattr(service, "get_scope", not_selected)
    monkeypatch.setattr(service, "list_connections", nothing_selected)

    with _Identity():
        result = asyncio.run(_underlying(datasources.db_schema)(connection="crm"))

    assert result["status"] == "error"
    assert "not available in this project" in result["error"]


def test_viewer_cannot_deselect(monkeypatch):
    _roles(monkeypatch, "viewer", "member")

    with _Identity():
        with pytest.raises(perms.PermissionDenied, match="member role"):
            asyncio.run(_underlying(catalog_tools.resource_deselect)("folders", "logs"))


def test_selection_rights_follow_the_principal_kind(monkeypatch):
    from src.mcp.project_access import selection_rights

    _roles(monkeypatch, "member", "admin")

    def rights(principal, user_id=None):
        mcp_context.set_current_principal(principal)
        mcp_context.set_current_user_id(user_id)
        try:
            return asyncio.run(selection_rights(1, 4))
        finally:
            mcp_context.set_current_principal(None)
            mcp_context.set_current_user_id(None)

    assert rights(None) == (True, False)  # auth disabled: anonymous
    assert rights(_principal(None)) == (True, False)  # API key
    assert rights(_principal(42), 42) == (True, True)
    # A user principal without a resolved user id gets no rights.
    assert rights(_principal(42), None) == (False, False)


VENDITE = {"scope_id": 31, "alias": "erp-vendite", "scope_label": "app.vendite",
           "scope_inferred": False}


def test_catalog_list_shows_the_scopes_the_project_linked(monkeypatch):
    async def fake_databases(org_id):
        return [{"id": 7, "name": "erp", "engine": "postgresql", "host": "h", "database_name": "app",
                 "ssh_machine_name": None, "description": None, "restricted": False},
                {"id": 8, "name": "crm", "engine": "mysql", "host": "h", "database_name": "crm",
                 "ssh_machine_name": None, "description": None, "restricted": False}]

    async def fake_selected(project_id, kind):
        return {7}

    async def fake_scopes(org_id, project_id):
        return [
            {"id": 7, "alias": "erp", "scope_label": "app.public", "scope_inferred": True},
            {"id": 7, "alias": "erp/app.vendite", "scope_label": "app.vendite", "scope_inferred": False},
        ]

    monkeypatch.setattr(catalog_tools.db_service, "list_catalog_connections", fake_databases)
    monkeypatch.setattr(catalog_tools.selections, "selected_ids", fake_selected)
    monkeypatch.setattr(catalog_tools.db_service, "list_connections", fake_scopes)

    with _Identity():
        result = asyncio.run(_underlying(catalog_tools.catalog_list)("databases"))

    erp, crm = result["databases"]
    assert erp["selected"] is True and erp["scopes"] == [
        {"alias": "erp", "scope": "app.public", "inferred": True},
        {"alias": "erp/app.vendite", "scope": "app.vendite", "inferred": False},
    ]
    assert crm["selected"] is False and crm["scopes"] == []


def _scope_calls(monkeypatch, calls, *, found=None, error=None):
    """create_scope finto: registra la chiamata, oppure solleva ``error``.
    ``found`` è il perimetro che find_project_scope trova dopo la chiamata."""

    async def fake_create(org_id, project_id, connection_id, database, schema, alias,
                          user_id, can_select_restricted, inferred=False):
        calls.append((org_id, project_id, connection_id, database, schema, alias,
                      user_id, can_select_restricted, inferred))
        if error is not None:
            raise error
        return {"id": 31}

    async def fake_find(org_id, project_id, connection_id, database, schema):
        return found if calls else None

    async def fake_catalog_connection(org_id, connection_id, include_secret=False):
        return {"id": connection_id, "name": "erp", "engine": "postgresql", "database_name": "app"}

    monkeypatch.setattr(catalog_tools.project_scopes, "create_scope", fake_create)
    monkeypatch.setattr(catalog_tools.db_service, "find_project_scope", fake_find)
    monkeypatch.setattr(catalog_tools.db_service, "get_catalog_connection", fake_catalog_connection)


def test_resource_select_links_the_chosen_scope(monkeypatch):
    calls = []
    _roles(monkeypatch, "member", "member")
    _resource(monkeypatch, {"id": 7, "name": "erp", "restricted": False})
    _scope_calls(monkeypatch, calls, found=VENDITE)

    with _Identity():
        result = asyncio.run(_underlying(catalog_tools.resource_select)(
            "databases", "erp", database="app", schema="vendite", alias="erp-vendite"))

    # Il perimetro scelto da chi chiama nasce confermato.
    assert calls == [(1, 4, 7, "app", "vendite", "erp-vendite", 42, False, False)]
    assert result == {"status": "ok", "kind": "databases", "name": "erp",
                      "already_selected": False, "scope_id": 31, "alias": "erp-vendite",
                      "scope": "app.vendite", "inferred": False}


def test_resource_select_without_a_scope_links_the_default_one(monkeypatch):
    calls = []
    _roles(monkeypatch, "member", "member")
    _resource(monkeypatch, {"id": 7, "name": "erp", "restricted": False})
    _scope_calls(monkeypatch, calls, found=VENDITE)

    with _Identity():
        result = asyncio.run(_underlying(catalog_tools.resource_select)("databases", "erp"))

    # Senza database ne schema il perimetro e' dedotto dalla connessione, non
    # scelto: resta marcato come dedotto e tiene l'avviso di verificarlo.
    assert calls == [(1, 4, 7, "app", "public", None, 42, False, True)]
    assert result["status"] == "ok"


def test_resource_select_with_only_the_schema_confirms_the_scope(monkeypatch):
    """Chi nomina anche solo lo schema ha scelto il perimetro: quello nasce
    confermato, come quando nomina il database."""
    calls = []
    _roles(monkeypatch, "member", "member")
    _resource(monkeypatch, {"id": 7, "name": "erp", "restricted": False})
    _scope_calls(monkeypatch, calls, found=VENDITE)

    with _Identity():
        asyncio.run(_underlying(catalog_tools.resource_select)(
            "databases", "erp", schema="vendite"))

    assert calls == [(1, 4, 7, None, "vendite", None, 42, False, False)]


def test_resource_select_reports_an_existing_scope_as_already_selected(monkeypatch):
    calls = []
    _roles(monkeypatch, "member", "member")
    _resource(monkeypatch, {"id": 7, "name": "erp", "restricted": False})
    _scope_calls(monkeypatch, calls, found=VENDITE,
                 error=project_scopes_module.ScopeConflictError("scope already linked"))

    with _Identity():
        result = asyncio.run(_underlying(catalog_tools.resource_select)(
            "databases", "erp", database="app", schema="vendite"))

    assert result["status"] == "ok" and result["already_selected"] is True
    assert result["alias"] == "erp-vendite"


@pytest.mark.parametrize("error, text", [
    (project_scopes_module.ScopeConflictError("alias 'erp' is already used"), "already used"),
    (ScopeShapeError("Engine 'mysql' has no schema separate from the database 'app'"), "no schema"),
])
def test_resource_select_reports_scope_errors(monkeypatch, error, text):
    calls = []
    _roles(monkeypatch, "member", "member")
    _resource(monkeypatch, {"id": 7, "name": "erp", "restricted": False})
    _scope_calls(monkeypatch, calls, found=None, error=error)

    with _Identity():
        result = asyncio.run(_underlying(catalog_tools.resource_select)(
            "databases", "erp", database="app", schema="other", alias="erp"))

    assert result["status"] == "error" and text in result["error"]


def test_resource_select_refuses_a_restricted_scope(monkeypatch):
    calls = []
    _roles(monkeypatch, "member", "member")
    _resource(monkeypatch, {"id": 7, "name": "erp", "restricted": False})
    _scope_calls(monkeypatch, calls, found=None, error=selections_module.RestrictedResourceError("erp"))

    with _Identity():
        with pytest.raises(perms.PermissionDenied, match="restricted"):
            asyncio.run(_underlying(catalog_tools.resource_select)(
                "databases", "erp", database="app", schema="other", alias="erp"))


def test_scope_fields_apply_only_to_databases(monkeypatch):
    _roles(monkeypatch, "member", "member")
    _resource(monkeypatch, {"id": 1, "name": "logs", "restricted": False})

    with _Identity():
        result = asyncio.run(_underlying(catalog_tools.resource_select)(
            "folders", "logs", schema="vendite"))

    assert result["status"] == "error" and "databases" in result["error"]
