import inspect

from src.indexer import indexer


def test_chunk_insert_carries_project():
    assert "project_id" in indexer._INSERT_CHUNK_SQL
    sig = inspect.signature(indexer._chunk_rows)
    assert list(sig.parameters)[:2] == ["org_id", "project_id"]


def test_sync_assigns_default_project_without_clobbering():
    src_text = inspect.getsource(indexer.sync_repos_config)
    assert "get_default_project_id" in src_text
    assert "COALESCE(repos.project_id, EXCLUDED.project_id)" in src_text


def test_index_repo_resolves_repo_project():
    src_text = inspect.getsource(indexer.index_repo)
    assert "project_id" in src_text
    assert "get_default_project_id" in src_text


def test_webhook_queues_project_scoped_requests():
    import src.api.routes.webhooks as webhooks

    src_text = inspect.getsource(webhooks.webhook_index)
    assert "project_id" in src_text
