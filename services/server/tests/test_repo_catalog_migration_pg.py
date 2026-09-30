import json

import pytest

from src import config
from src.catalog import repo_migration
from src.datasources.secrets import decrypt_secret
from tests.pgutil import (
    execute, fetch, fetchval, make_legacy_repo_schema, requires_pg, run_db, seed_org, seed_project,
)

pytestmark = requires_pg


@pytest.fixture(autouse=True)
def _settings(monkeypatch, tmp_path):
    monkeypatch.setattr(config.get_settings(), "encryption_key", "test-key")
    monkeypatch.setattr(config.get_settings(), "repos_cache_dir", str(tmp_path))


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


async def _legacy_repo(org_id, project_id, name, url, branch="main", chunks=0, type_="gitlab"):
    await execute(
        "INSERT INTO repos (org_id, project_id, name, type, url, branch, status, total_chunks) "
        "VALUES ($1, $2, $3, $4, $5, $6, 'indexed', $7)",
        org_id, project_id, name, type_, url, branch, chunks,
    )
    for index in range(chunks):
        await execute(
            "INSERT INTO repo_chunks (org_id, project_id, repo_name, file_path, chunk_index, content) "
            "VALUES ($1, $2, $3, 'src/app.py', $4, $5)",
            org_id, project_id, name, index, f"{name} chunk {index}",
        )
    await execute(
        "INSERT INTO repo_symbols (org_id, project_id, repo_name, file_path, name, kind) "
        "VALUES ($1, $2, $3, 'src/app.py', 'Main', 'def')",
        org_id, project_id, name,
    )


def test_copies_of_the_same_url_and_branch_become_one_repository(pg_database, tmp_path):
    async def scenario():
        await make_legacy_repo_schema()
        org = await seed_org("acme")
        p1 = await seed_project(org, "alpha")
        p2 = await seed_project(org, "beta")
        p3 = await seed_project(org, "gamma")
        await _legacy_repo(org, p1, "astersign", "https://git.example.org/aster/AsterSign", "master", chunks=2)
        await _legacy_repo(org, p2, "sign-client-a", "https://git.example.org/aster/AsterSign", "develop", chunks=3)
        await _legacy_repo(org, p3, "sign-client-b", "http://git.example.org/aster/AsterSign.git", "develop", chunks=5)
        await _legacy_repo(org, p1, "aster-desk", "https://git.example.org/aster/aster-desk", chunks=1)
        await _legacy_repo(org, p2, "desk-copy", "https://git.example.org/aster/aster-desk/", chunks=4)
        org_dir = tmp_path / f"org_{org}"
        (org_dir / "aster-desk").mkdir(parents=True)
        (org_dir / "desk-copy").mkdir()
        report = await repo_migration.apply_repo_catalog_migration()
        repos = {r["name"]: r for r in await fetch("SELECT id, name, url, branch FROM repos")}
        selections = await fetch(
            "SELECT r.name, pr.project_id FROM project_repos pr JOIN repos r ON r.id = pr.repo_id"
        )
        chunks = {
            r["name"]: r["n"]
            for r in await fetch(
                "SELECT r.name, count(*) AS n FROM repo_chunks c JOIN repos r ON r.id = c.repo_id GROUP BY r.name"
            )
        }
        legacy_columns = await fetchval(
            "SELECT count(*) FROM information_schema.columns WHERE column_name IN ('repo_name', 'project_id') "
            "AND table_name IN ('repos', 'repo_chunks', 'repo_symbols', 'chunk_annotations')"
        )
        return {
            "report": report,
            "repos": repos,
            "selections": {(r["name"], r["project_id"]) for r in selections},
            "chunks": chunks,
            "symbols": await fetchval("SELECT count(*) FROM repo_symbols"),
            "legacy_columns": legacy_columns,
            "projects": (p1, p2, p3),
            "org_dir": org_dir,
        }

    out = run_db(scenario)
    p1, p2, p3 = out["projects"]
    assert set(out["repos"]) == {"astersign", "AsterSign@develop", "aster-desk"}
    assert out["repos"]["AsterSign@develop"]["url"] == "https://git.example.org/aster/AsterSign"
    assert out["selections"] == {
        ("astersign", p1), ("AsterSign@develop", p2), ("AsterSign@develop", p3),
        ("aster-desk", p1), ("aster-desk", p2),
    }
    # Canonico: per AsterSign@develop la riga con più chunk, per aster-desk quella che ha già il nome.
    assert out["chunks"] == {"astersign": 2, "AsterSign@develop": 5, "aster-desk": 1}
    assert out["symbols"] == 3
    assert out["legacy_columns"] == 0
    report = out["report"]
    assert (report["repos_before"], report["repos_after"]) == (5, 3)
    assert (report["chunks_deleted"], report["symbols_deleted"]) == (7, 2)
    assert sorted((m["name"], m["rows"]) for m in report["merged"]) == [("AsterSign@develop", 2), ("aster-desk", 2)]
    assert len(report["renamed"]) == 1 and report["renamed"][0].endswith("sign-client-b -> AsterSign@develop")
    desk_id = out["repos"]["aster-desk"]["id"]
    assert (out["org_dir"] / f"repo_{desk_id}").is_dir()
    assert not (out["org_dir"] / "aster-desk").exists()
    assert not (out["org_dir"] / "desk-copy").exists()


def test_annotations_follow_the_canonical_repository_and_identical_notes_merge(pg_database):
    async def scenario():
        await make_legacy_repo_schema()
        org = await seed_org("acme")
        p1 = await seed_project(org, "alpha")
        p2 = await seed_project(org, "beta")
        await _legacy_repo(org, p1, "desk", "https://git.example.org/aster/desk", chunks=2)
        await _legacy_repo(org, p2, "desk-copy", "https://git.example.org/aster/desk.git", chunks=1)
        for project_id, repo_name, note in (
            (p1, "desk", "retry loop"), (p2, "desk-copy", "retry loop"), (p2, "desk-copy", "slow query"),
        ):
            await execute(
                "INSERT INTO chunk_annotations (org_id, project_id, repo_name, file_path, start_line, end_line, note) "
                "VALUES ($1, $2, $3, 'src/app.py', 10, 20, $4)",
                org, project_id, repo_name, note,
            )
        report = await repo_migration.apply_repo_catalog_migration()
        notes = await fetch(
            "SELECT r.name, a.note FROM chunk_annotations a JOIN repos r ON r.id = a.repo_id ORDER BY a.note"
        )
        return report, [(n["name"], n["note"]) for n in notes]

    report, notes = run_db(scenario)
    assert notes == [("desk", "retry loop"), ("desk", "slow query")]
    assert report["annotations_merged"] == 1


def test_configured_repositories_are_imported_with_an_encrypted_token(pg_database):
    async def scenario():
        await make_legacy_repo_schema()
        org = await seed_org("acme")
        default = await seed_project(org, "default")
        await seed_project(org, "other")
        forge = {
            "repos": [{"name": "widget", "type": "github", "url": "https://github.com/acme/widget.git",
                       "branch": "main", "token": "ghp_secret"}],
            "indexing": {"schedule": "0 3 * * *"},
        }
        await execute(
            "INSERT INTO org_runtime_config (org_id, forge_config) VALUES ($1, $2::jsonb)", org, json.dumps(forge)
        )
        report = await repo_migration.apply_repo_catalog_migration()
        repo = (await fetch("SELECT id, url, token_enc, status FROM repos WHERE name = 'widget'"))[0]
        selected = await fetchval("SELECT project_id FROM project_repos WHERE repo_id = $1", repo["id"])
        stored = await fetchval("SELECT forge_config FROM org_runtime_config WHERE org_id = $1", org)
        return report, repo, selected, default, _json(stored)

    report, repo, selected, default, stored = run_db(scenario)
    assert report["imported"] == 1
    assert repo["url"] == "https://github.com/acme/widget" and repo["status"] == "pending"
    assert decrypt_secret(repo["token_enc"]) == "ghp_secret"
    assert selected == default
    assert "repos" not in stored and stored["indexing"]["schedule"] == "0 3 * * *"


def test_a_second_run_changes_nothing(pg_database):
    async def scenario():
        await make_legacy_repo_schema()
        org = await seed_org("acme")
        p1 = await seed_project(org, "alpha")
        p2 = await seed_project(org, "beta")
        await _legacy_repo(org, p1, "desk", "https://git.example.org/aster/desk", chunks=1)
        await _legacy_repo(org, p2, "desk-copy", "https://git.example.org/aster/desk", chunks=1)
        await repo_migration.apply_repo_catalog_migration()
        second = await repo_migration.apply_repo_catalog_migration()
        return second, await fetchval("SELECT count(*) FROM repos"), await fetchval("SELECT count(*) FROM project_repos")

    second, repos, selections = run_db(scenario)
    assert second == repo_migration.EMPTY_REPORT
    assert (repos, selections) == (1, 2)


def test_the_migration_opens_its_own_connection_without_a_command_timeout(pg_database, monkeypatch):
    """Le tabelle derivate possono essere grandi: una connessione del pool condiviso
    (db.get_pool, command_timeout=60) farebbe scadere le ALTER TABLE su un catalogo
    ampio. La migrazione deve aprire una connessione dedicata con timeout disattivato."""

    async def scenario():
        calls = []
        real_connect = repo_migration.asyncpg.connect

        async def spy_connect(dsn, **kwargs):
            calls.append((dsn, kwargs))
            return await real_connect(dsn, **kwargs)

        monkeypatch.setattr(repo_migration.asyncpg, "connect", spy_connect)
        await repo_migration.apply_repo_catalog_migration()
        return calls

    calls = run_db(scenario)
    assert len(calls) == 1
    dsn, kwargs = calls[0]
    assert dsn == config.get_settings().database_url
    assert kwargs["command_timeout"] is None


def test_names_that_differ_only_in_case_are_made_unique(pg_database):
    async def scenario():
        await make_legacy_repo_schema()
        org = await seed_org("acme")
        p1 = await seed_project(org, "alpha")
        await _legacy_repo(org, p1, "App", "https://git.example.org/a/app")
        await _legacy_repo(org, p1, "app", "https://git.example.org/b/app")
        report = await repo_migration.apply_repo_catalog_migration()
        return report, [r["name"] for r in await fetch("SELECT name FROM repos")]

    report, names = run_db(scenario)
    assert {n.lower() for n in names} == {"app", "app-2"}
    assert len(report["renamed"]) == 1


def test_a_local_row_does_not_collide_with_a_remote_of_the_same_url_and_branch(pg_database):
    async def scenario():
        await make_legacy_repo_schema()
        org = await seed_org("acme")
        p1 = await seed_project(org, "alpha")
        p2 = await seed_project(org, "beta")
        p3 = await seed_project(org, "gamma")
        # Le due righe hanno lo stesso URL e lo stesso branch: senza azzerare
        # l'URL della riga locale, repos_org_url_branch_idx (org_id, lower(url),
        # branch) le tratterebbe come lo stesso repository remoto e fallirebbe.
        await _legacy_repo(org, p1, "astersign", "https://git.example.org/aster/astersign", "master", chunks=1)
        await _legacy_repo(org, p2, "astersign-checkout", "https://git.example.org/aster/astersign", "master",
                            chunks=0, type_="local")
        # Un legacy remoto senza branch: il raggruppamento lo tratta già come
        # "main", ma finora la colonna restava NULL sulla riga finale.
        await _legacy_repo(org, p3, "widget", "https://git.example.org/acme/widget", branch=None, chunks=1)
        report = await repo_migration.apply_repo_catalog_migration()
        rows = {r["name"]: r for r in await fetch("SELECT name, type, url, branch FROM repos")}
        return report, rows

    report, rows = run_db(scenario)
    assert set(rows) == {"astersign", "astersign-checkout", "widget"}
    assert rows["astersign"]["url"] == "https://git.example.org/aster/astersign"
    assert rows["astersign"]["branch"] == "master"
    assert rows["astersign-checkout"]["type"] == "local"
    assert rows["astersign-checkout"]["url"] is None
    assert rows["widget"]["branch"] == "main"
    assert report["repos_after"] == 3


def test_a_repository_left_without_symbols_gets_a_full_reindex(pg_database):
    async def scenario():
        await make_legacy_repo_schema()
        org = await seed_org("acme")
        p1 = await seed_project(org, "alpha")
        p2 = await seed_project(org, "beta")
        await execute(
            "INSERT INTO repos (org_id, project_id, name, type, url, branch, status, total_chunks, "
            "indexed_commit) VALUES ($1, $2, $3, 'gitlab', $4, 'main', 'indexed', 3, 'abc123')",
            org, p1, "stale-symbols", "https://git.example.org/aster/stale-symbols",
        )
        await execute(
            "INSERT INTO repo_chunks (org_id, project_id, repo_name, file_path, chunk_index, content) "
            "VALUES ($1, $2, 'stale-symbols', 'src/app.py', 0, 'chunk')",
            org, p1,
        )
        # I simboli sono registrati sotto un altro progetto di quello della riga
        # del repository: la migrazione li rilega solo quando i due coincidono.
        await execute(
            "INSERT INTO repo_symbols (org_id, project_id, repo_name, file_path, name, kind) "
            "VALUES ($1, $2, 'stale-symbols', 'src/app.py', 'Main', 'def')",
            org, p2,
        )
        await repo_migration.apply_repo_catalog_migration()
        repo = (await fetch(
            "SELECT status, total_chunks, indexed_commit FROM repos WHERE name = 'stale-symbols'"
        ))[0]
        symbols_left = await fetchval("SELECT count(*) FROM repo_symbols")
        return repo, symbols_left

    repo, symbols_left = run_db(scenario)
    assert repo["status"] == "indexed" and repo["total_chunks"] == 3
    assert repo["indexed_commit"] is None
    assert symbols_left == 0


def test_the_catalog_schema_imports_configured_repositories_once(pg_database):
    async def scenario():
        org = await seed_org("acme")
        default = await seed_project(org, "default")
        forge = {"repos": [{"name": "widget", "type": "gitlab", "url": "https://git.example.org/acme/widget",
                            "branch": "develop"}]}
        await execute(
            "INSERT INTO org_runtime_config (org_id, forge_config) VALUES ($1, $2::jsonb)", org, json.dumps(forge)
        )
        first = await repo_migration.apply_repo_catalog_migration()
        await execute(
            "UPDATE org_runtime_config SET forge_config = $2::jsonb WHERE org_id = $1", org, json.dumps(forge)
        )
        second = await repo_migration.apply_repo_catalog_migration()
        rows = await fetch("SELECT r.name, pr.project_id FROM repos r JOIN project_repos pr ON pr.repo_id = r.id")
        return first, second, rows, default

    first, second, rows, default = run_db(scenario)
    assert first["imported"] == 1 and second["imported"] == 0
    assert rows == [{"name": "widget", "project_id": default}]
