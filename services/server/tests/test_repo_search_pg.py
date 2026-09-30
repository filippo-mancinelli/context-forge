import pytest

from src import config, search
from src.catalog import repos as repo_catalog
from src.catalog import selections
from src.mcp import code_tools, context, permissions
from src.org_settings import OrgSettings
from tests.pgutil import execute, requires_pg, run_db, seed_org, seed_project

pytestmark = requires_pg


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(config.get_settings(), "encryption_key", "test-key")


def _fn(tool):
    return getattr(tool, "fn", tool)


async def _catalog():
    org = await seed_org("acme")
    alpha = await seed_project(org, "alpha")
    beta = await seed_project(org, "beta")
    desk = await repo_catalog.create_repo(org, {"name": "desk", "type": "local", "path": "/repos/desk"})
    sign = await repo_catalog.create_repo(org, {"name": "sign", "type": "local", "path": "/repos/sign"})
    await selections.select_resource(org, alpha, "repos", desk["id"], None, False)
    await selections.select_resource(org, beta, "repos", desk["id"], None, False)
    await selections.select_resource(org, beta, "repos", sign["id"], None, False)
    for repo in (desk, sign):
        await execute(
            "INSERT INTO repo_chunks (org_id, repo_id, file_path, chunk_index, chunk_type, content, metadata, embedding) "
            "VALUES ($1, $2, 'src/app.py', 0, 'function_definition', $3, $4::jsonb, '[1,0,0]')",
            org, repo["id"], f"def assign_ticket(): return '{repo['name']}'",
            '{"name": "assign_ticket", "start_line": 1}',
        )
    return org, alpha, beta


@pytest.mark.parametrize("hybrid", [True, False])
def test_search_returns_only_chunks_of_selected_repositories(pg_database, monkeypatch, hybrid):
    async def fake_embed(text, org_id):
        return [1.0, 0.0, 0.0]

    async def three_dims(org_id):
        # The search casts to the org's dimension (HNSW expression): match the fixture vectors.
        return OrgSettings(embeddings_dims=3)

    monkeypatch.setattr(search, "embed_text", fake_embed)
    monkeypatch.setattr(search, "get_org_settings", three_dims)
    monkeypatch.setattr(search, "hybrid_enabled", lambda: hybrid)

    async def scenario():
        org, alpha, beta = await _catalog()
        return (
            await search.search_repo_chunks(org, "assign ticket", project_id=alpha),
            await search.search_repo_chunks(org, "assign ticket", repos=["sign"], project_id=beta),
            await search.search_repo_symbols(org, "assign", project_id=alpha),
        )

    alpha_hits, beta_hits, alpha_symbols = run_db(scenario)
    assert [h["repo_name"] for h in alpha_hits] == ["desk"]
    assert [h["repo_name"] for h in beta_hits] == ["sign"]
    assert [s["repo_name"] for s in alpha_symbols] == ["desk"]


def test_an_annotation_is_shared_by_every_project_that_selected_the_repository(pg_database):
    async def scenario():
        org, alpha, beta = await _catalog()
        gamma = await seed_project(org, "gamma")
        permissions.set_current_permissions(None)
        context.set_current_org_id(org)
        try:
            context.set_current_project_id(alpha)
            created = await _fn(code_tools.repo_annotate)("desk", "src/app.py", "retry loop", 10, 20)
            context.set_current_project_id(beta)
            listed = await _fn(code_tools.repo_annotations)("desk")
            context.set_current_project_id(gamma)
            refused = await _fn(code_tools.repo_annotations)("desk")
        finally:
            context.set_current_org_id(None)
            context.set_current_project_id(None)
        return created, listed, refused

    created, listed, refused = run_db(scenario)
    assert created["status"] == "ok"
    assert [a["note"] for a in listed["annotations"]] == ["retry loop"]
    assert refused["status"] == "error" and "not available in this project" in refused["error"]
