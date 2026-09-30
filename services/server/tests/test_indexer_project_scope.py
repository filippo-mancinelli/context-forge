import inspect

from src.indexer import indexer


def test_chunks_are_keyed_by_repository_id():
    assert "ON CONFLICT (repo_id, file_path, chunk_index)" in indexer._INSERT_CHUNK_SQL
    assert "project_id" not in indexer._INSERT_CHUNK_SQL
    assert list(inspect.signature(indexer._chunk_rows).parameters)[:2] == ["org_id", "repo_id"]


def test_symbols_are_keyed_by_repository_id():
    source = inspect.getsource(indexer._store_symbols)
    assert "ON CONFLICT (repo_id, file_path, name, kind)" in source
    assert "project_id" not in source


def test_index_runs_are_tracked_per_repository_id():
    assert "repo.id" in inspect.getsource(indexer.run_index_repo)
