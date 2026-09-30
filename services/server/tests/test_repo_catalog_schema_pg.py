from src import db
from src.catalog.migration import apply_catalog_migration
from src.catalog.repo_migration import EMPTY_REPORT, apply_repo_catalog_migration
from tests.pgutil import fetch, requires_pg, run_db, seed_org

pytestmark = requires_pg


def test_fresh_schema_has_the_repo_catalog_shape(pg_database):
    async def scenario():
        rows = await fetch(
            "SELECT table_name, column_name, is_nullable FROM information_schema.columns "
            "WHERE table_name IN ('repos', 'repo_chunks', 'repo_symbols', 'chunk_annotations', "
            "'index_requests', 'project_repos')"
        )
        constraints = {r["conname"] for r in await fetch("SELECT conname FROM pg_constraint")}
        return {f"{r['table_name']}.{r['column_name']}": r["is_nullable"] for r in rows}, constraints

    columns, constraints = run_db(scenario)
    assert columns["repo_chunks.repo_id"] == "NO"
    assert columns["repo_symbols.repo_id"] == "NO"
    assert columns["chunk_annotations.repo_id"] == "NO"
    assert columns["index_requests.repo_id"] == "YES"
    assert columns["index_requests.project_id"] == "YES"
    assert "repos.token_enc" in columns and "repos.restricted" in columns
    assert "project_repos.added_by" in columns
    for legacy in ("repos.project_id", "repo_chunks.repo_name", "repo_chunks.project_id",
                   "repo_symbols.repo_name", "repo_symbols.project_id", "chunk_annotations.repo_name",
                   "chunk_annotations.project_id", "index_requests.repo_name"):
        assert legacy not in columns, legacy
    assert {"repos_pkey", "repo_chunks_repo_fkey", "repo_chunks_repo_unique", "repo_symbols_repo_pkey",
            "repo_symbols_repo_fkey", "chunk_annotations_repo_fkey", "index_requests_repo_fkey",
            "project_repos_pkey"} <= constraints


def test_a_second_boot_on_the_catalog_schema_changes_nothing(pg_database):
    async def scenario():
        org = await seed_org("acme")
        await db.init_db()
        await db.apply_tenant_repo_migration(org)
        await db.apply_project_migration()
        await apply_catalog_migration()
        return await apply_repo_catalog_migration()

    assert run_db(scenario) == EMPTY_REPORT
