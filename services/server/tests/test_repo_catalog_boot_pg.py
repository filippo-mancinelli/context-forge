"""The repository catalog conversion at boot, starting from the frozen baseline shape.

A fresh context-forge database keys repositories by name (and by project after
setup), with nullable repo_id columns from migration 0006; ensure_tenant_storage()
must reach the catalog shape with or without an organization, and never run
the conversion twice at the same time.
"""
import asyncio
from pathlib import Path

import asyncpg

from src import config, tenancy
from src.catalog.repo_migration import EMPTY_REPORT, REPO_CATALOG_LOCK_KEY, apply_repo_catalog_migration
from src.config import ForgeConfig
from tests import pgutil
from tests.pgutil import execute, fetch, fetchval, run_db, seed_org, seed_project, seed_user

pytestmark = pgutil.requires_pg

_TABLES = ("repos", "repo_chunks", "repo_symbols", "chunk_annotations", "index_requests", "project_repos")


async def _shape() -> dict:
    rows = await fetch(
        "SELECT table_name, column_name, is_nullable FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = ANY($1::text[])",
        list(_TABLES),
    )
    constraints = await fetch(
        "SELECT conname FROM pg_constraint WHERE conrelid = ANY("
        "SELECT oid FROM pg_class WHERE relname = ANY($1::text[]))",
        list(_TABLES),
    )
    pkey = await fetch(
        "SELECT a.attname FROM pg_constraint c "
        "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey) "
        "WHERE c.conname = 'repos_pkey' AND c.conrelid = 'repos'::regclass"
    )
    return {
        "columns": {f"{r['table_name']}.{r['column_name']}": r["is_nullable"] for r in rows},
        "constraints": {r["conname"] for r in constraints},
        "repos_pkey": [r["attname"] for r in pkey],
    }


def _assert_repo_catalog_shape(shape: dict) -> None:
    columns = shape["columns"]
    for column in ("repo_chunks.repo_id", "repo_symbols.repo_id", "chunk_annotations.repo_id",
                   "repos.id", "repos.org_id", "repo_chunks.org_id"):
        assert columns[column] == "NO", column
    assert columns["index_requests.repo_id"] == "YES"
    assert columns["index_requests.project_id"] == "YES"
    assert {"repos.token_enc", "repos.description", "repos.restricted", "project_repos.added_by"} <= set(columns)
    for legacy in ("repos.project_id", "repo_chunks.repo_name", "repo_chunks.project_id",
                   "repo_symbols.repo_name", "repo_symbols.project_id", "chunk_annotations.repo_name",
                   "chunk_annotations.project_id", "index_requests.repo_name"):
        assert legacy not in columns, legacy
    assert {"repos_pkey", "repo_chunks_repo_fkey", "repo_chunks_repo_unique", "repo_symbols_repo_pkey",
            "repo_symbols_repo_fkey", "chunk_annotations_repo_fkey", "index_requests_repo_fkey",
            "project_repos_pkey"} <= shape["constraints"]
    assert "repos_org_name_pkey" not in shape["constraints"]
    assert shape["repos_pkey"] == ["id"]


def test_baseline_starts_from_the_name_keyed_shape(baseline_database):
    shape = run_db(_shape)
    columns = shape["columns"]
    assert "repos.id" not in columns
    assert shape["repos_pkey"] == ["name"]
    assert columns["repo_chunks.repo_name"] == "NO"
    assert columns["repo_chunks.repo_id"] == "YES"
    assert columns["index_requests.repo_id"] == "YES"
    assert "repos.token_enc" in columns and "repos.restricted" in columns
    assert "repos.project_id" in columns


def test_boot_before_setup_reaches_the_repo_catalog_shape(baseline_database):
    async def scenario():
        first = await tenancy.ensure_tenant_storage()
        second = await tenancy.ensure_tenant_storage()
        return first, second, await apply_repo_catalog_migration(), await _shape()

    first, second, report, shape = run_db(scenario)
    assert first is None and second is None
    assert report == EMPTY_REPORT
    _assert_repo_catalog_shape(shape)


def test_boot_with_an_org_converts_name_keyed_repositories(baseline_database):
    cache = Path(config.get_settings().repos_cache_dir)

    async def scenario():
        org = await seed_org("acme")
        project = await seed_project(org, "alpha")
        await execute(
            "INSERT INTO repos (org_id, project_id, name, type, url, branch, status, total_chunks) "
            "VALUES ($1, $2, 'desk', 'gitlab', 'https://git.example.com/team/desk', 'main', 'indexed', 1)",
            org, project,
        )
        await execute(
            "INSERT INTO repo_chunks (org_id, project_id, repo_name, file_path, chunk_index, content) "
            "VALUES ($1, $2, 'desk', 'src/app.py', 0, 'def main(): pass')",
            org, project,
        )
        await execute(
            "INSERT INTO repo_symbols (org_id, project_id, repo_name, file_path, name, kind) "
            "VALUES ($1, $2, 'desk', 'src/app.py', 'main', 'def')",
            org, project,
        )
        await execute(
            "INSERT INTO chunk_annotations (org_id, project_id, repo_name, file_path, note) "
            "VALUES ($1, $2, 'desk', 'src/app.py', 'entry point')",
            org, project,
        )
        await execute(
            "INSERT INTO index_requests (org_id, project_id, repo_name) VALUES ($1, $2, 'desk')", org, project
        )
        (cache / f"org_{org}" / "desk").mkdir(parents=True)
        returned = await tenancy.ensure_tenant_storage()
        again = await tenancy.ensure_tenant_storage()
        repo_id = await fetchval("SELECT id FROM repos WHERE org_id = $1 AND name = 'desk'", org)
        return {
            "org": org, "project": project, "returned": returned, "again": again, "repo_id": repo_id,
            "shape": await _shape(),
            "linked": {
                table: await fetchval(f"SELECT array_agg(DISTINCT repo_id) FROM {table}")
                for table in ("repo_chunks", "repo_symbols", "chunk_annotations", "index_requests")
            },
            "selections": await fetch("SELECT project_id, repo_id FROM project_repos"),
        }

    out = run_db(scenario)
    assert out["returned"] == out["org"] and out["again"] == out["org"]
    _assert_repo_catalog_shape(out["shape"])
    assert all(ids == [out["repo_id"]] for ids in out["linked"].values()), out["linked"]
    assert out["selections"] == [{"project_id": out["project"], "repo_id": out["repo_id"]}]
    org_dir = cache / f"org_{out['org']}"
    assert (org_dir / f"repo_{out['repo_id']}").is_dir()
    assert not (org_dir / "desk").exists()


def test_rows_without_an_organization_wait_for_the_default_org(baseline_database, monkeypatch):
    monkeypatch.setattr(config, "get_forge_config", lambda: ForgeConfig())

    async def scenario():
        await execute("INSERT INTO repos (name, type, path) VALUES ('tools', 'local', '/repos/tools')")
        await execute(
            "INSERT INTO repo_chunks (repo_name, file_path, chunk_index, content) "
            "VALUES ('tools', 'run.sh', 0, 'echo run')"
        )
        before_setup = await tenancy.ensure_tenant_storage()
        deferred = await _shape()
        await seed_user("admin")
        org = await tenancy.ensure_tenant_storage()
        return {
            "before_setup": before_setup, "deferred": deferred, "org": org, "shape": await _shape(),
            "repo": await fetch("SELECT id, org_id, name FROM repos"),
            "chunk_repo": await fetchval("SELECT repo_id FROM repo_chunks"),
            "selected": await fetchval(
                "SELECT count(*) FROM project_repos pr JOIN projects p ON p.id = pr.project_id "
                "WHERE p.org_id = $1", org,
            ) if org else None,
        }

    out = run_db(scenario)
    assert out["before_setup"] is None
    assert "repo_chunks.repo_name" in out["deferred"]["columns"]
    assert out["org"] is not None
    _assert_repo_catalog_shape(out["shape"])
    assert [(r["org_id"], r["name"]) for r in out["repo"]] == [(out["org"], "tools")]
    assert out["chunk_repo"] == out["repo"][0]["id"]
    assert out["selected"] == 1


def test_the_conversion_waits_for_the_repo_catalog_lock(pg_database):
    """A second instance booting at the same time blocks until the first one is done."""

    async def scenario():
        holder = await asyncpg.connect(config.get_settings().database_url)
        try:
            await holder.execute("SELECT pg_advisory_lock($1::bigint)", REPO_CATALOG_LOCK_KEY)
            task = asyncio.create_task(apply_repo_catalog_migration())
            await asyncio.sleep(1.0)
            blocked = not task.done()
            waiting = await holder.fetchval(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted "
                "AND classid = 0 AND objid = $1 AND objsubid = 1",
                REPO_CATALOG_LOCK_KEY,
            )
            await holder.execute("SELECT pg_advisory_unlock($1::bigint)", REPO_CATALOG_LOCK_KEY)
            report = await asyncio.wait_for(task, timeout=60)
            left = await holder.fetchval(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND objid = $1",
                REPO_CATALOG_LOCK_KEY,
            )
        finally:
            await holder.close()
        return blocked, waiting, report, left

    blocked, waiting, report, left = run_db(scenario)
    assert blocked
    assert waiting == 1
    assert report == EMPTY_REPORT
    assert left == 0
