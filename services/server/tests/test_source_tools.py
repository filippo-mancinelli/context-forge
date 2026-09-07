import asyncio

import pytest

from src.mcp import context as mcp_context
from src.mcp import permissions as perms


def _underlying(tool):
    return getattr(tool, "fn", tool)


class _SourceIdentity:
    """Identità con il permesso sources-write e un progetto selezionato."""

    def __enter__(self):
        perms.set_current_permissions(frozenset({"sources-write"}))
        mcp_context.set_current_org_id(1)
        mcp_context.set_current_project_id(4)
        return self

    def __exit__(self, *exc):
        perms.set_current_permissions(None)
        mcp_context.set_current_org_id(None)
        mcp_context.set_current_project_id(None)
        return False


def test_repo_add_requires_the_sources_write_permission():
    from src.mcp import repos
    perms.set_current_permissions(frozenset({"context-read"}))
    try:
        with pytest.raises(Exception, match="sources-write"):
            asyncio.run(_underlying(repos.repo_add)("askmeai-v2", "gitlab"))
    finally:
        perms.set_current_permissions(None)


def test_repo_add_registers_and_queues_indexing(monkeypatch):
    from src.mcp import repos

    added = []
    queued = []

    async def fake_add(**kwargs):
        added.append(kwargs)
        return {"name": kwargs["name"], "type": kwargs["type"], "branch": kwargs["branch"],
                "language": "auto"}

    async def fake_queue(org_id, project_id, repo_name):
        queued.append((org_id, project_id, repo_name))

    monkeypatch.setattr(repos.repo_registry, "add_repo", fake_add)
    monkeypatch.setattr(repos.repo_registry, "queue_index", fake_queue)

    with _SourceIdentity():
        result = asyncio.run(
            _underlying(repos.repo_add)(
                "askmeai-v2", "gitlab", url="https://git.lascaux.it/askme/askmeai-v2"
            )
        )

    assert result["status"] == "ok"
    assert result["indexing"] is True
    assert added[0]["org_id"] == 1 and added[0]["project_id"] == 4
    assert queued == [(1, 4, "askmeai-v2")]


def test_repo_add_can_skip_indexing(monkeypatch):
    from src.mcp import repos

    queued = []

    async def fake_add(**kwargs):
        return {"name": kwargs["name"], "type": kwargs["type"], "branch": kwargs["branch"],
                "language": "auto"}

    async def fake_queue(org_id, project_id, repo_name):
        queued.append(repo_name)

    monkeypatch.setattr(repos.repo_registry, "add_repo", fake_add)
    monkeypatch.setattr(repos.repo_registry, "queue_index", fake_queue)

    with _SourceIdentity():
        result = asyncio.run(
            _underlying(repos.repo_add)("askmeai-v2", "gitlab", index=False)
        )

    assert result["indexing"] is False
    assert queued == []


def test_repo_add_reports_a_duplicate_name(monkeypatch):
    from src.mcp import repos
    from src import repo_registry

    async def fake_add(**kwargs):
        raise repo_registry.RepoAlreadyExistsError("Repository 'askmeai-v2' already exists")

    monkeypatch.setattr(repos.repo_registry, "add_repo", fake_add)

    with _SourceIdentity():
        result = asyncio.run(_underlying(repos.repo_add)("askmeai-v2", "gitlab"))

    assert result["status"] == "error"
    assert "already exists" in result["error"]


def test_api_add_requires_a_source(monkeypatch):
    from src.mcp import contracts

    called = []

    async def fake_create(org_id, project_id, data):
        called.append(data)
        return {"id": 1}

    monkeypatch.setattr(contracts.contracts_service, "create_contract", fake_create)

    with _SourceIdentity():
        result = asyncio.run(_underlying(contracts.api_add)("askme", "openapi"))

    assert result["status"] == "error"
    assert called == []


def test_api_add_delegates_to_the_service(monkeypatch):
    from src.mcp import contracts

    called = []

    async def fake_create(org_id, project_id, data):
        called.append((org_id, project_id, data))
        return {"id": 7, "name": "askme", "type": "openapi"}

    monkeypatch.setattr(contracts.contracts_service, "create_contract", fake_create)

    with _SourceIdentity():
        result = asyncio.run(
            _underlying(contracts.api_add)(
                "askme", "openapi", source_url="https://api.example.com/openapi.json"
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
            _underlying(contracts.api_add)("askme", "soap", raw_spec="{}")
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
            "Host 'internal.lascaux.it' resolves to a non-public address; refusing to fetch"
        )

    monkeypatch.setattr(knowledge.kb_fetch, "fetch_document", fake_fetch)

    with _SourceIdentity():
        result = asyncio.run(
            _underlying(knowledge.kb_add_url)("http://internal.lascaux.it/a.pdf")
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


def test_db_add_never_passes_a_secret_and_marks_pending(monkeypatch):
    from src.mcp import datasources

    created = []
    marked = []

    async def fake_create(org_id, project_id, data):
        created.append((org_id, project_id, data))
        return {"id": 9, "name": data["name"], "engine": data["engine"], "status": "unknown"}

    async def fake_mark(org_id, project_id, connection_id):
        marked.append((org_id, project_id, connection_id))

    monkeypatch.setattr(datasources.service, "create_connection", fake_create)
    monkeypatch.setattr(datasources.service, "mark_pending_secret", fake_mark)

    with _SourceIdentity():
        result = asyncio.run(
            _underlying(datasources.db_add)(
                "askmeai-v2-collaudo", "postgres", host="192.168.2.39", port=5440,
                database_name="askmeai_v2", username="askmeai",
            )
        )

    assert result["status"] == "ok"
    org_id, project_id, data = created[0]
    assert (org_id, project_id) == (1, 4)
    assert not any(key in data for key in SECRET_KEYS)
    assert marked == [(1, 4, 9)]
    assert "next_step" in result


def test_db_add_has_no_secret_parameter():
    """Il segreto non deve nemmeno essere passabile: non basta ignorarlo."""
    import inspect

    from src.mcp import datasources

    params = inspect.signature(_underlying(datasources.db_add)).parameters
    assert not any(key in params for key in SECRET_KEYS)


def test_db_add_reports_an_unsupported_engine(monkeypatch):
    from src.mcp import datasources

    async def fake_create(org_id, project_id, data):
        raise ValueError("Unsupported engine 'oracle'. Supported: postgres, mysql")

    monkeypatch.setattr(datasources.service, "create_connection", fake_create)

    with _SourceIdentity():
        result = asyncio.run(_underlying(datasources.db_add)("x", "oracle"))

    assert result["status"] == "error"
    assert "Unsupported engine" in result["error"]


def test_ssh_source_add_never_passes_a_secret_and_marks_pending(monkeypatch):
    from src.mcp import ssh_files

    created = []
    marked = []

    async def fake_create(org_id, project_id, data):
        created.append((org_id, project_id, data))
        return {"id": 3, "name": data["name"], "host": data["host"], "status": "unknown"}

    async def fake_mark(org_id, project_id, source_id):
        marked.append((org_id, project_id, source_id))

    monkeypatch.setattr(ssh_files.ssh_service, "create_source", fake_create)
    monkeypatch.setattr(ssh_files.ssh_service, "mark_pending_secret", fake_mark)

    with _SourceIdentity():
        result = asyncio.run(
            _underlying(ssh_files.ssh_source_add)(
                "askmeai-v2-env", "192.168.2.39", "/var/lib/compose/askmeai-v2", "lascaux"
            )
        )

    assert result["status"] == "ok"
    org_id, project_id, data = created[0]
    assert (org_id, project_id) == (1, 4)
    assert not any(key in data for key in SECRET_KEYS)
    assert data["root_path"] == "/var/lib/compose/askmeai-v2"
    assert marked == [(1, 4, 3)]


def test_ssh_source_add_has_no_secret_parameter():
    import inspect

    from src.mcp import ssh_files

    params = inspect.signature(_underlying(ssh_files.ssh_source_add)).parameters
    assert not any(key in params for key in SECRET_KEYS)
