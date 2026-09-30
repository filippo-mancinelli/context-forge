import pytest

from src import config
from src.catalog import repos as repo_catalog
from src.catalog import selections
from tests.pgutil import execute, fetchval, requires_pg, run_db, seed_org, seed_project

pytestmark = requires_pg

REMOTE = {"type": "gitlab", "url": "http://git.example.org/aster/AsterSign.git", "branch": "develop", "token": "glpat-1"}


@pytest.fixture(autouse=True)
def _settings(monkeypatch, tmp_path):
    monkeypatch.setattr(config.get_settings(), "encryption_key", "test-key")
    monkeypatch.setattr(config.get_settings(), "repos_cache_dir", str(tmp_path))


def test_a_repository_is_unique_by_url_and_branch_and_by_name(pg_database):
    async def scenario():
        org = await seed_org("acme")
        await repo_catalog.create_repo(org, {"name": "astersign", "type": "gitlab",
                                             "url": "https://git.example.org/aster/AsterSign", "branch": "master"})
        created = await repo_catalog.create_repo(org, REMOTE)
        conflicts = []
        for data in (dict(REMOTE, name="another"), {"name": "ASTERSIGN", "type": "local", "path": "/repos/x"}):
            try:
                await repo_catalog.create_repo(org, data)
            except repo_catalog.RepoConflictError as e:
                conflicts.append(e.existing["name"])
        return created, conflicts

    created, conflicts = run_db(scenario)
    assert created["name"] == "AsterSign@develop"
    assert created["url"] == "https://git.example.org/aster/AsterSign"
    assert created["has_token"] is True and "token_enc" not in created
    assert conflicts == ["AsterSign@develop", "astersign"]


def test_a_project_reaches_only_the_repositories_it_selected(pg_database):
    async def scenario():
        org = await seed_org("acme")
        alpha = await seed_project(org, "alpha")
        beta = await seed_project(org, "beta")
        repo = await repo_catalog.create_repo(org, dict(REMOTE, name="sign"))
        await selections.select_resource(org, alpha, "repos", repo["id"], None, False)
        record = await repo_catalog.resolve_project_repo(org, alpha, "SIGN")
        outcomes = []
        for project, name in ((beta, "sign"), (alpha, "missing")):
            try:
                await repo_catalog.resolve_project_repo(org, project, name)
            except (repo_catalog.RepoNotAvailableError, repo_catalog.RepoNotFoundError) as e:
                outcomes.append(type(e).__name__)
        listed = [r["name"] for r in await repo_catalog.list_project_repos(org, beta)]
        catalog = await repo_catalog.list_catalog(org)
        return record, outcomes, listed, catalog

    record, outcomes, listed, catalog = run_db(scenario)
    assert (record.name, record.token, record.branch) == ("sign", "glpat-1", "develop")
    assert outcomes == ["RepoNotAvailableError", "RepoNotFoundError"]
    assert listed == []
    assert catalog[0]["project_count"] == 1


def test_changing_the_branch_drops_the_index_and_an_empty_token_keeps_the_stored_one(pg_database):
    async def scenario():
        org = await seed_org("acme")
        repo = await repo_catalog.create_repo(org, dict(REMOTE, name="sign"))
        await execute(
            "UPDATE repos SET status = 'indexed', total_chunks = 1, indexed_commit = 'abc' WHERE id = $1", repo["id"]
        )
        await execute(
            "INSERT INTO repo_chunks (org_id, repo_id, file_path, chunk_index, content) VALUES ($1, $2, 'a.py', 0, 'x')",
            org, repo["id"],
        )
        renamed = await repo_catalog.update_repo(org, repo["id"], dict(REMOTE, name="sign-dev", token=""))
        chunks_after_rename = await fetchval("SELECT count(*) FROM repo_chunks WHERE repo_id = $1", repo["id"])
        moved = await repo_catalog.update_repo(org, repo["id"], dict(REMOTE, name="sign-dev", branch="main", token=""))
        chunks_after_branch = await fetchval("SELECT count(*) FROM repo_chunks WHERE repo_id = $1", repo["id"])
        token = (await repo_catalog.get_record(org, repo["id"])).token
        return renamed, chunks_after_rename, moved, chunks_after_branch, token

    renamed, chunks_after_rename, moved, chunks_after_branch, token = run_db(scenario)
    assert renamed["status"] == "indexed" and chunks_after_rename == 1
    assert moved["status"] == "pending" and moved["indexed_commit"] is None and chunks_after_branch == 0
    assert token == "glpat-1"


def test_deleting_a_repository_removes_its_index_and_selections(pg_database):
    async def scenario():
        org = await seed_org("acme")
        alpha = await seed_project(org, "alpha")
        repo = await repo_catalog.create_repo(org, dict(REMOTE, name="sign"))
        await selections.select_resource(org, alpha, "repos", repo["id"], None, False)
        await execute(
            "INSERT INTO repo_chunks (org_id, repo_id, file_path, chunk_index, content) VALUES ($1, $2, 'a.py', 0, 'x')",
            org, repo["id"],
        )
        deleted = await repo_catalog.delete_repo(org, repo["id"])
        again = await repo_catalog.delete_repo(org, repo["id"])
        return deleted, again, await fetchval("SELECT count(*) FROM project_repos"), await fetchval(
            "SELECT count(*) FROM repo_chunks"
        )

    assert run_db(scenario) == (True, False, 0, 0)


def test_a_restricted_repository_needs_the_restricted_right(pg_database):
    async def scenario():
        org = await seed_org("acme")
        alpha = await seed_project(org, "alpha")
        repo = await repo_catalog.create_repo(org, dict(REMOTE, name="sign", restricted=True))
        try:
            await selections.select_resource(org, alpha, "repos", repo["id"], None, False)
        except selections.RestrictedResourceError:
            refused = True
        else:
            refused = False
        admitted = await selections.select_resource(org, alpha, "repos", repo["id"], None, True)
        return refused, admitted["already_selected"]

    assert run_db(scenario) == (True, False)


def test_update_repo_turns_a_unique_violation_into_a_repo_conflict_error(pg_database, monkeypatch):
    """If a concurrent write slips past the pre-check, the unique index must still
    surface RepoConflictError rather than a raw asyncpg error."""

    async def _no_conflict(*args, **kwargs):
        return None

    async def scenario():
        org = await seed_org("acme")
        await repo_catalog.create_repo(org, {"name": "astersign", "type": "local", "path": "/repos/astersign"})
        other = await repo_catalog.create_repo(org, {"name": "other", "type": "local", "path": "/repos/other"})
        monkeypatch.setattr(repo_catalog, "_conflict", _no_conflict)
        try:
            await repo_catalog.update_repo(
                org, other["id"], {"name": "ASTERSIGN", "type": "local", "path": "/repos/other"}
            )
        except repo_catalog.RepoConflictError:
            return True
        return False

    assert run_db(scenario) is True
