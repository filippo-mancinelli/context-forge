import pytest

from src import config
from src.catalog import repos as repo_catalog
from src.catalog import selections
from src.indexer import indexer
from tests.pgutil import fetch, fetchval, requires_pg, run_db, seed_org, seed_project

pytestmark = requires_pg

SOURCE = (
    "def handle_request(payload):\n"
    "    return validate(payload)\n\n\n"
    "def validate(payload):\n"
    "    return bool(payload) and payload.get('id') is not None\n"
)


@pytest.fixture(autouse=True)
def _settings(monkeypatch, tmp_path):
    monkeypatch.setattr(config.get_settings(), "encryption_key", "test-key")
    monkeypatch.setattr(config.get_settings(), "repos_cache_dir", str(tmp_path / "cache"))


def test_a_repository_selected_by_two_projects_is_indexed_once_under_its_id(pg_database, tmp_path, monkeypatch):
    source = tmp_path / "widget"
    (source / "src").mkdir(parents=True)
    (source / "src" / "app.py").write_text(SOURCE, encoding="utf-8")

    async def fake_embed(texts, org_id):
        return [[0.1, 0.2, 0.3] for _ in texts]

    monkeypatch.setattr(indexer, "embed_batch", fake_embed)

    async def scenario():
        org = await seed_org("acme")
        alpha = await seed_project(org, "alpha")
        beta = await seed_project(org, "beta")
        repo = await repo_catalog.create_repo(org, {"name": "widget", "type": "local", "path": str(source)})
        for project in (alpha, beta):
            await selections.select_resource(org, project, "repos", repo["id"], None, False)
        await repo_catalog.queue_index(org, None, alpha)
        await indexer.run_pending_index_requests()
        return {
            "repo": (await fetch("SELECT status, total_chunks FROM repos WHERE id = $1", repo["id"]))[0],
            "chunks": await fetchval("SELECT count(*) FROM repo_chunks WHERE repo_id = $1", repo["id"]),
            "symbols": await fetchval("SELECT count(*) FROM repo_symbols WHERE repo_id = $1", repo["id"]),
            "pending": await fetchval("SELECT count(*) FROM index_requests WHERE processed_at IS NULL"),
        }

    out = run_db(scenario)
    assert out["repo"]["status"] == "indexed"
    assert out["repo"]["total_chunks"] == out["chunks"] > 0
    assert out["symbols"] > 0
    assert out["pending"] == 0


def test_a_remote_copy_lives_under_the_repository_id(tmp_path, monkeypatch):
    from src.config import RepoRecord
    from src.indexer.git_manager import get_repo_local_path

    monkeypatch.setattr(config.get_settings(), "repos_cache_dir", str(tmp_path))
    repo = RepoRecord(id=7, org_id=3, name="AsterSign@develop", type="gitlab", url="https://h/x/AsterSign")
    assert get_repo_local_path(repo, 3) == str(tmp_path / "org_3" / "repo_7")
