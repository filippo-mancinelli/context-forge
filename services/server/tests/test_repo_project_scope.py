import inspect

from src import search


def test_repo_search_sql_filters_project():
    for sql in (search._repo_hybrid_sql(1536), search._repo_vector_sql(1536), search._SYMBOL_SEARCH_SQL):
        assert "project_repos" in sql


def test_repo_search_signatures_take_project():
    for fn in (search.search_repo_chunks, search.search_repo_symbols):
        assert "project_id" in inspect.signature(fn).parameters


def test_mcp_repo_tools_resolve_project():
    import src.mcp.code_tools as code_tools
    import src.mcp.repos as mcp_repos

    assert "require_project_id" in inspect.getsource(mcp_repos)
    assert "require_project_id" in inspect.getsource(code_tools)


def test_rest_repo_routes_are_a_read_only_project_view():
    import src.api.routes.repos as repos_routes

    src_text = inspect.getsource(repos_routes)
    assert "get_active_project" in src_text
    assert "get_active_org" not in src_text
    assert "require_project_role" in src_text
    for removed in ("create_repo", "update_repo", "delete_repo", "persist_org_config"):
        assert removed not in src_text


def test_code_explain_checks_repo_in_project():
    import src.mcp.code_tools as code_tools

    src_text = inspect.getsource(code_tools.code_explain)
    assert "require_project_id" in src_text
    assert "resolve_project_repo" in src_text
