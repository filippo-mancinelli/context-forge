import asyncio

import pytest

from src import projects as projects_module
from src import tenancy as tenancy_module
from src.catalog import selections as selections_module
from src.mcp import context as mcp_context
from src.mcp import permissions as perms


def _underlying(tool):
    return getattr(tool, "fn", tool)


class _SourceIdentity:
    """Identità con il permesso sources-write e un progetto selezionato.

    Senza ``user_id`` è una API key: impersonale, mai admin dell'organizzazione.
    """

    def __init__(self, user_id=None):
        self.user_id = user_id

    def __enter__(self):
        perms.set_current_permissions(frozenset({"sources-write"}))
        mcp_context.set_current_org_id(1)
        mcp_context.set_current_project_id(4)
        mcp_context.set_current_user_id(self.user_id)
        if self.user_id is None:
            principal = mcp_context.Principal(kind="api_key", id=9, label="key")
        else:
            principal = mcp_context.Principal(kind="user", id=self.user_id, label=f"user-{self.user_id}")
        mcp_context.set_current_principal(principal)
        return self

    def __exit__(self, *exc):
        perms.set_current_permissions(None)
        mcp_context.set_current_org_id(None)
        mcp_context.set_current_project_id(None)
        mcp_context.set_current_user_id(None)
        mcp_context.set_current_principal(None)
        return False


def _roles(monkeypatch, project_role, org_role="member"):
    async def fake_project_access(org_id, user_id, project_id):
        return project_role

    async def fake_membership(org_id, user_id):
        return org_role

    monkeypatch.setattr(projects_module, "resolve_project_access", fake_project_access)
    monkeypatch.setattr(tenancy_module, "get_membership_role", fake_membership)


def test_repo_add_requires_the_sources_write_permission():
    from src.mcp import repos
    perms.set_current_permissions(frozenset({"context-read"}))
    try:
        with pytest.raises(Exception, match="sources-write"):
            asyncio.run(_underlying(repos.repo_add)("asterai-v2", "gitlab"))
    finally:
        perms.set_current_permissions(None)


def _no_existing_repo(monkeypatch, module):
    async def no_existing(org_id, url, branch):
        return None

    monkeypatch.setattr(module.repo_catalog, "find_repo", no_existing)


def _capture_queue(monkeypatch, module, calls):
    async def fake_queue(org_id, repo_id, project_id):
        calls.append((org_id, repo_id, project_id))

    monkeypatch.setattr(module.repo_catalog, "queue_index", fake_queue)


def test_repo_add_registers_selects_and_queues_indexing(monkeypatch):
    from src.mcp import repos

    created, queued, selected = [], [], []

    async def fake_create(org_id, data):
        created.append((org_id, data))
        return {"id": 12, "name": data["name"], "type": data["type"], "url": data["url"],
                "path": None, "branch": data["branch"], "status": "pending"}

    _no_existing_repo(monkeypatch, repos)
    monkeypatch.setattr(repos.repo_catalog, "create_repo", fake_create)
    _capture_queue(monkeypatch, repos, queued)
    _capture_selection(monkeypatch, repos, selected)

    with _SourceIdentity():
        result = asyncio.run(
            _underlying(repos.repo_add)("asterai-v2", "gitlab", url="https://git.example.org/aster/asterai-v2")
        )

    assert result["status"] == "ok" and result["created"] is True and result["indexing"] is True
    assert "token" not in created[0][1]
    assert selected == [(1, 4, "repos", 12)]
    assert queued == [(1, 12, 4)]


def test_repo_add_selects_an_indexed_repository_without_registering_a_copy(monkeypatch):
    from src.mcp import repos

    queued, selected = [], []

    async def existing(org_id, url, branch):
        return {"id": 3, "name": "aster-desk", "type": "gitlab", "url": url, "path": None,
                "branch": branch, "status": "indexed"}

    async def must_not_create(*args):
        raise AssertionError("create_repo must not be called")

    monkeypatch.setattr(repos.repo_catalog, "find_repo", existing)
    monkeypatch.setattr(repos.repo_catalog, "create_repo", must_not_create)
    _capture_queue(monkeypatch, repos, queued)
    _capture_selection(monkeypatch, repos, selected)

    with _SourceIdentity():
        result = asyncio.run(
            _underlying(repos.repo_add)("desk", "gitlab", url="https://git.example.org/aster/aster-desk.git")
        )

    assert result["status"] == "ok" and result["created"] is False and result["indexing"] is False
    assert selected == [(1, 4, "repos", 3)]
    assert queued == []


def test_repo_add_can_skip_indexing(monkeypatch):
    from src.mcp import repos

    queued, selected = [], []

    async def fake_create(org_id, data):
        return {"id": 5, "name": data["name"], "type": data["type"], "url": None,
                "path": data["path"], "branch": "main", "status": "pending"}

    _no_existing_repo(monkeypatch, repos)
    monkeypatch.setattr(repos.repo_catalog, "create_repo", fake_create)
    _capture_queue(monkeypatch, repos, queued)
    _capture_selection(monkeypatch, repos, selected)

    with _SourceIdentity():
        result = asyncio.run(_underlying(repos.repo_add)("local-tools", "local", path="/repos/tools", index=False))

    assert result["status"] == "ok" and result["indexing"] is False
    assert queued == []


def test_repo_add_reports_a_name_already_in_the_catalog(monkeypatch):
    from src.mcp import repos

    async def conflicting(org_id, data):
        raise repos.repo_catalog.RepoConflictError({"name": "asterai-v2"})

    _no_existing_repo(monkeypatch, repos)
    monkeypatch.setattr(repos.repo_catalog, "create_repo", conflicting)

    with _SourceIdentity():
        result = asyncio.run(_underlying(repos.repo_add)("asterai-v2", "local", path="/repos/other"))

    assert result["status"] == "error" and "asterai-v2" in result["error"]


def test_repo_add_refuses_a_role_below_member(monkeypatch):
    from src.mcp import repos

    _roles(monkeypatch, "viewer")

    async def must_not_look_up(*args):
        raise AssertionError("the catalog must not be touched")

    monkeypatch.setattr(repos.repo_catalog, "find_repo", must_not_look_up)

    with _SourceIdentity(user_id=7):
        with pytest.raises(perms.PermissionDenied, match="member role"):
            asyncio.run(_underlying(repos.repo_add)("desk", "gitlab", url="https://git.example.org/a/desk"))


def test_repo_add_does_not_hand_a_restricted_repository_to_an_api_key(monkeypatch):
    from src.mcp import repos

    rights = []

    async def existing(org_id, url, branch):
        return {"id": 3, "name": "payroll-app", "type": "gitlab", "url": url, "status": "indexed"}

    async def must_not_create(*args):
        raise AssertionError("create_repo must not be called")

    monkeypatch.setattr(repos.repo_catalog, "find_repo", existing)
    monkeypatch.setattr(repos.repo_catalog, "create_repo", must_not_create)
    _refuse_restricted(monkeypatch, repos, rights, "payroll-app")

    with _SourceIdentity():
        with pytest.raises(perms.PermissionDenied, match="restricted"):
            asyncio.run(_underlying(repos.repo_add)("x", "gitlab", url="https://git.example.com/a/payroll"))

    assert rights == [False]


def test_api_add_requires_a_source(monkeypatch):
    from src.mcp import contracts

    called = []

    async def fake_create(org_id, project_id, data):
        called.append(data)
        return {"id": 1}

    monkeypatch.setattr(contracts.contracts_service, "create_contract", fake_create)

    with _SourceIdentity():
        result = asyncio.run(_underlying(contracts.api_add)("aster", "openapi"))

    assert result["status"] == "error"
    assert called == []


def test_api_add_delegates_to_the_service(monkeypatch):
    from src.mcp import contracts

    called = []

    async def fake_create(org_id, project_id, data):
        called.append((org_id, project_id, data))
        return {"id": 7, "name": "aster", "type": "openapi"}

    monkeypatch.setattr(contracts.contracts_service, "create_contract", fake_create)

    with _SourceIdentity():
        result = asyncio.run(
            _underlying(contracts.api_add)(
                "aster", "openapi", source_url="https://api.example.com/openapi.json"
            )
        )

    assert result["status"] == "ok"
    assert result["contract"]["id"] == 7
    org_id, project_id, data = called[0]
    assert (org_id, project_id) == (1, 4)
    assert data["source_url"] == "https://api.example.com/openapi.json"


def test_api_add_reports_a_service_error(monkeypatch):
    from src.mcp import contracts

    async def fake_create(org_id, project_id, data):
        raise ValueError("Unsupported contract type 'soap'")

    monkeypatch.setattr(contracts.contracts_service, "create_contract", fake_create)

    with _SourceIdentity():
        result = asyncio.run(
            _underlying(contracts.api_add)("aster", "soap", raw_spec="{}")
        )

    assert result["status"] == "error"
    assert "Unsupported" in result["error"]


def test_kb_add_url_stores_and_processes_the_document(monkeypatch):
    from src.mcp import knowledge

    saved = []
    processed = []

    async def fake_fetch(url):
        return "manuale.pdf", b"%PDF-1.4"

    async def fake_save(org_id, project_id, filename, data):
        saved.append((org_id, project_id, filename, data))
        return {"id": 21, "title": "manuale", "filename": filename, "status": "pending"}

    monkeypatch.setattr(knowledge.kb_fetch, "fetch_document", fake_fetch)
    monkeypatch.setattr(knowledge.kb_store, "save_upload", fake_save)
    monkeypatch.setattr(knowledge, "_schedule_processing", lambda doc_id: processed.append(doc_id))

    with _SourceIdentity():
        result = asyncio.run(
            _underlying(knowledge.kb_add_url)("https://example.com/docs/manuale.pdf")
        )

    assert result["status"] == "ok"
    assert result["document"]["id"] == 21
    assert saved[0][:3] == (1, 4, "manuale.pdf")
    assert processed == [21]


def test_kb_add_url_uses_the_given_title(monkeypatch):
    from src.mcp import knowledge

    saved = []

    async def fake_fetch(url):
        return "manuale.pdf", b"%PDF-1.4"

    async def fake_save(org_id, project_id, filename, data):
        saved.append(filename)
        return {"id": 22, "filename": filename}

    monkeypatch.setattr(knowledge.kb_fetch, "fetch_document", fake_fetch)
    monkeypatch.setattr(knowledge.kb_store, "save_upload", fake_save)
    monkeypatch.setattr(knowledge, "_schedule_processing", lambda doc_id: None)

    with _SourceIdentity():
        asyncio.run(
            _underlying(knowledge.kb_add_url)(
                "https://example.com/docs/manuale.pdf", title="Manuale operativo"
            )
        )

    # il titolo scelto sostituisce il nome file, l'estensione originale resta
    assert saved == ["Manuale operativo.pdf"]


def test_kb_add_url_reports_a_refused_url(monkeypatch):
    from src.mcp import knowledge
    from src.kb import fetch as kb_fetch_module

    async def fake_fetch(url):
        raise kb_fetch_module.FetchError(
            "Host 'internal.example.org' resolves to a non-public address; refusing to fetch"
        )

    monkeypatch.setattr(knowledge.kb_fetch, "fetch_document", fake_fetch)

    with _SourceIdentity():
        result = asyncio.run(
            _underlying(knowledge.kb_add_url)("http://internal.example.org/a.pdf")
        )

    assert result["status"] == "error"
    assert "non-public address" in result["error"]


def test_ui_link_is_none_without_a_configured_ui(monkeypatch):
    from src.mcp import source_links
    from src import config as config_module

    settings = config_module.get_settings()
    monkeypatch.setattr(settings, "ui_base_url", "", raising=False)
    assert source_links.ui_link("/datasources") is None


def test_ui_link_joins_base_and_path(monkeypatch):
    from src.mcp import source_links
    from src import config as config_module

    settings = config_module.get_settings()
    monkeypatch.setattr(settings, "ui_base_url", "https://care.example.com/", raising=False)
    assert source_links.ui_link("/datasources") == "https://care.example.com/datasources"


SECRET_KEYS = ("password", "private_key", "ssh_password", "ssh_private_key")


def _capture_selection(monkeypatch, module, calls):
    async def fake_select(org_id, project_id, kind, resource_id, user_id, can_select_restricted):
        calls.append((org_id, project_id, kind, resource_id))
        return {"kind": kind, "resource_id": resource_id, "name": "x", "already_selected": False}

    monkeypatch.setattr(module.selections, "select_resource", fake_select)


def test_db_add_never_passes_a_secret_marks_pending_and_selects(monkeypatch):
    from src.mcp import datasources

    created, marked, selected = [], [], []

    async def no_existing(org_id, machine_id, host, port, database_name):
        return None

    async def fake_create(org_id, data):
        created.append((org_id, data))
        return {"id": 9, "name": data["name"], "engine": data["engine"], "status": "unknown"}

    async def fake_mark(org_id, connection_id):
        marked.append((org_id, connection_id))

    monkeypatch.setattr(datasources.service, "find_connection", no_existing)
    monkeypatch.setattr(datasources.service, "create_connection", fake_create)
    monkeypatch.setattr(datasources.service, "mark_pending_secret", fake_mark)
    _capture_selection(monkeypatch, datasources, selected)

    with _SourceIdentity():
        result = asyncio.run(
            _underlying(datasources.db_add)(
                "asterai-v2-collaudo", "postgresql", host="192.168.2.39", port=5440,
                database_name="asterai_v2", username="asterai",
            )
        )

    assert result["status"] == "ok" and result["created"] is True
    org_id, data = created[0]
    assert org_id == 1
    assert not any(key in data for key in SECRET_KEYS)
    assert marked == [(1, 9)]
    assert selected == [(1, 4, "databases", 9)]
    assert "next_step" in result


def test_db_add_selects_an_existing_connection_instead_of_duplicating(monkeypatch):
    from src.mcp import datasources

    selected, looked_up = [], []

    async def fake_machine(org_id, name):
        return {"id": 5, "name": name}

    async def existing(org_id, machine_id, host, port, database_name):
        looked_up.append(machine_id)
        return {"id": 3, "name": "chat-db", "status": "ok"}

    async def must_not_create(*args):
        raise AssertionError("create_connection must not be called")

    monkeypatch.setattr(datasources.machines, "get_machine_by_name", fake_machine)
    monkeypatch.setattr(datasources.service, "find_connection", existing)
    monkeypatch.setattr(datasources.service, "create_connection", must_not_create)
    _capture_selection(monkeypatch, datasources, selected)

    with _SourceIdentity():
        result = asyncio.run(
            _underlying(datasources.db_add)(
                "chat-db", "mysql", host="127.0.0.1", port=3306, database_name="asterchat",
                ssh_machine="astercare@192.168.0.206",
            )
        )

    assert result["status"] == "ok" and result["created"] is False
    assert looked_up == [5]
    assert selected == [(1, 4, "databases", 3)]
    assert "next_step" not in result


def test_db_add_has_no_secret_parameter():
    """Il segreto non deve nemmeno essere passabile: non basta ignorarlo."""
    import inspect

    from src.mcp import datasources

    params = inspect.signature(_underlying(datasources.db_add)).parameters
    assert not any(key in params for key in SECRET_KEYS)


def test_db_add_reports_an_unsupported_engine(monkeypatch):
    from src.mcp import datasources

    async def no_existing(*args):
        return None

    async def fake_create(org_id, data):
        raise ValueError("Unsupported engine 'oracle'. Supported: postgresql, mysql")

    monkeypatch.setattr(datasources.service, "find_connection", no_existing)
    monkeypatch.setattr(datasources.service, "create_connection", fake_create)

    with _SourceIdentity():
        result = asyncio.run(_underlying(datasources.db_add)("x", "oracle"))

    assert result["status"] == "error"
    assert "Unsupported engine" in result["error"]


def test_ssh_source_add_creates_a_pending_machine_and_selects_the_folder(monkeypatch):
    from src.mcp import ssh_files

    machines_created, pending, sources_created, selected = [], [], [], []

    async def no_machine(org_id, host, port, username):
        return None

    async def fake_create_machine(org_id, data):
        machines_created.append((org_id, data))
        return {"id": 5, "name": data["name"], "status": "unknown"}

    async def fake_pending(org_id, machine_id):
        pending.append((org_id, machine_id))

    async def no_source(org_id, machine_id, root_path):
        return None

    async def fake_create_source(org_id, data):
        sources_created.append((org_id, data))
        return {"id": 3, "name": data["name"], "machine_id": data["machine_id"]}

    monkeypatch.setattr(ssh_files.machines, "find_machine", no_machine)
    monkeypatch.setattr(ssh_files.machines, "create_machine", fake_create_machine)
    monkeypatch.setattr(ssh_files.machines, "mark_pending_secret", fake_pending)
    monkeypatch.setattr(ssh_files.ssh_service, "find_source", no_source)
    monkeypatch.setattr(ssh_files.ssh_service, "create_source", fake_create_source)
    _capture_selection(monkeypatch, ssh_files, selected)

    with _SourceIdentity():
        result = asyncio.run(
            _underlying(ssh_files.ssh_source_add)(
                "asterai-v2-env", "/var/lib/compose/asterai-v2", host="192.168.2.39", username="larkin"
            )
        )

    assert result["status"] == "ok" and result["created"] is True
    org_id, machine_data = machines_created[0]
    assert org_id == 1
    assert machine_data["name"] == "larkin@192.168.2.39"
    assert not any(key in machine_data for key in SECRET_KEYS)
    assert pending == [(1, 5)]
    assert sources_created[0][1]["machine_id"] == 5
    assert sources_created[0][1]["root_path"] == "/var/lib/compose/asterai-v2"
    assert selected == [(1, 4, "folders", 3)]
    assert "next_step" in result


def test_ssh_source_add_selects_an_existing_folder(monkeypatch):
    from src.mcp import ssh_files

    selected = []

    async def fake_machine(org_id, name):
        return {"id": 5, "name": name, "status": "ok"}

    async def existing(org_id, machine_id, root_path):
        return {"id": 8, "name": "asterchat-home", "machine_id": machine_id}

    async def must_not_create(*args):
        raise AssertionError("create_source must not be called")

    monkeypatch.setattr(ssh_files.machines, "get_machine_by_name", fake_machine)
    monkeypatch.setattr(ssh_files.ssh_service, "find_source", existing)
    monkeypatch.setattr(ssh_files.ssh_service, "create_source", must_not_create)
    _capture_selection(monkeypatch, ssh_files, selected)

    with _SourceIdentity():
        result = asyncio.run(
            _underlying(ssh_files.ssh_source_add)(
                "chat-home", "/var/lib/asterchat/data", machine="astercare@192.168.0.206"
            )
        )

    assert result["status"] == "ok" and result["created"] is False
    assert selected == [(1, 4, "folders", 8)]
    assert "next_step" not in result


def test_ssh_source_add_needs_a_machine_or_an_address():
    from src.mcp import ssh_files

    with _SourceIdentity():
        result = asyncio.run(_underlying(ssh_files.ssh_source_add)("x", "/srv"))

    assert result["status"] == "error"


def test_ssh_source_add_has_no_secret_parameter():
    import inspect

    from src.mcp import ssh_files

    params = inspect.signature(_underlying(ssh_files.ssh_source_add)).parameters
    assert not any(key in params for key in SECRET_KEYS)


def _refuse_restricted(monkeypatch, module, rights, name):
    """select_resource si comporta come sul database: rifiuta una risorsa
    riservata a chi non ha il diritto di sceglierla."""

    async def fake_select(org_id, project_id, kind, resource_id, user_id, can_select_restricted):
        rights.append(can_select_restricted)
        if not can_select_restricted:
            raise selections_module.RestrictedResourceError(name)
        return {"kind": kind, "resource_id": resource_id, "name": name, "already_selected": False}

    monkeypatch.setattr(module.selections, "select_resource", fake_select)


def test_db_add_does_not_hand_a_restricted_connection_to_an_api_key(monkeypatch):
    """sources-write non è un diritto da admin: una API key non può prendersi
    una connessione riservata registrandola di nuovo."""
    from src.mcp import datasources

    rights = []

    async def existing(org_id, machine_id, host, port, database_name):
        return {"id": 3, "name": "payroll", "status": "ok"}

    async def must_not_create(*args):
        raise AssertionError("create_connection must not be called")

    monkeypatch.setattr(datasources.service, "find_connection", existing)
    monkeypatch.setattr(datasources.service, "create_connection", must_not_create)
    _refuse_restricted(monkeypatch, datasources, rights, "payroll")

    with _SourceIdentity():
        with pytest.raises(perms.PermissionDenied, match="restricted"):
            asyncio.run(
                _underlying(datasources.db_add)(
                    "payroll", "mysql", host="10.0.0.8", port=3306, database_name="payroll"
                )
            )

    assert rights == [False]


def test_db_add_refuses_a_role_below_member(monkeypatch):
    from src.mcp import datasources

    _roles(monkeypatch, "viewer")

    async def must_not_find(*args):
        raise AssertionError("find_connection must not be called")

    async def must_not_select(*args):
        raise AssertionError("select_resource must not be called")

    monkeypatch.setattr(datasources.service, "find_connection", must_not_find)
    monkeypatch.setattr(datasources.selections, "select_resource", must_not_select)

    with _SourceIdentity(user_id=42):
        with pytest.raises(perms.PermissionDenied, match="member role"):
            asyncio.run(
                _underlying(datasources.db_add)("erp", "mysql", host="10.0.0.8", database_name="erp")
            )


def test_ssh_source_add_does_not_hand_a_restricted_folder_to_an_api_key(monkeypatch):
    from src.mcp import ssh_files

    rights = []

    async def fake_machine(org_id, name):
        return {"id": 5, "name": name, "status": "ok"}

    async def existing(org_id, machine_id, root_path):
        return {"id": 8, "name": "payroll-home", "machine_id": machine_id}

    async def must_not_create(*args):
        raise AssertionError("create_source must not be called")

    monkeypatch.setattr(ssh_files.machines, "get_machine_by_name", fake_machine)
    monkeypatch.setattr(ssh_files.ssh_service, "find_source", existing)
    monkeypatch.setattr(ssh_files.ssh_service, "create_source", must_not_create)
    _refuse_restricted(monkeypatch, ssh_files, rights, "payroll-home")

    with _SourceIdentity():
        with pytest.raises(perms.PermissionDenied, match="restricted"):
            asyncio.run(
                _underlying(ssh_files.ssh_source_add)(
                    "payroll-home", "/srv/payroll", machine="astercare@192.168.0.206"
                )
            )

    assert rights == [False]


def test_ssh_source_add_refuses_a_role_below_member(monkeypatch):
    from src.mcp import ssh_files

    _roles(monkeypatch, "viewer")

    async def must_not_look_up(*args):
        raise AssertionError("the catalog must not be touched")

    async def must_not_select(*args):
        raise AssertionError("select_resource must not be called")

    monkeypatch.setattr(ssh_files.machines, "get_machine_by_name", must_not_look_up)
    monkeypatch.setattr(ssh_files.ssh_service, "find_source", must_not_look_up)
    monkeypatch.setattr(ssh_files.selections, "select_resource", must_not_select)

    with _SourceIdentity(user_id=42):
        with pytest.raises(perms.PermissionDenied, match="member role"):
            asyncio.run(
                _underlying(ssh_files.ssh_source_add)(
                    "chat-home", "/var/lib/asterchat", machine="astercare@192.168.0.206"
                )
            )
