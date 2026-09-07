import inspect

from src import search


def test_repo_search_sql_filters_project():
    for sql in (search._repo_hybrid_sql(1536), search._repo_vector_sql(1536), search._SYMBOL_SEARCH_SQL):
        assert "project_id" in sql


def test_repo_search_signatures_take_project():
    for fn in (search.search_repo_chunks, search.search_repo_symbols):
        assert "project_id" in inspect.signature(fn).parameters


def test_mcp_repo_tools_resolve_project():
    import src.mcp.repos as mcp_repos
    import src.mcp.code_tools as code_tools

    assert "require_project_id" in inspect.getsource(mcp_repos)
    assert "require_project_id" in inspect.getsource(code_tools)


def test_rest_repo_routes_use_active_project():
    import src.api.routes.repos as repos_routes

    src_text = inspect.getsource(repos_routes)
    assert "get_active_project" in src_text
    assert "get_active_org" not in src_text
    assert "require_project_role" in src_text


def test_update_repo_rename_rebinds_project():
    """After a rename, sync_repos_config inserts a fresh 'repos' row (no
    conflict to preserve the old project_id), so the rename branch must
    re-bind it to the active project the same way create_repo does."""
    import src.api.routes.repos as repos_routes

    src_text = inspect.getsource(repos_routes.update_repo)
    assert "UPDATE repos SET project_id" in src_text

    rename_branch = src_text.split("await sync_repos_config(org.org_id)", 1)[1]
    assert "UPDATE repos SET project_id" in rename_branch


def test_code_explain_checks_repo_in_project():
    import src.mcp.code_tools as code_tools

    src_text = inspect.getsource(code_tools.code_explain)
    assert "require_project_id" in src_text
    assert "project_id" in src_text
