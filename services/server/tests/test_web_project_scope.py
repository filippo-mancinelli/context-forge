import inspect

from src import db, search
from src.web import crawler, store


def test_web_unique_keys_move_to_project():
    src_text = inspect.getsource(db.apply_project_migration)
    assert "web_pages_project_url_key" in src_text
    assert "web_sites_project_root_url_key" in src_text


def test_web_store_signatures_take_project():
    assert list(inspect.signature(store.add_url).parameters)[:2] == ["org_id", "project_id"]
    assert list(inspect.signature(store.delete_page).parameters)[:2] == ["org_id", "project_id"]
    assert list(inspect.signature(store.search_pages).parameters)[:2] == ["org_id", "project_id"]
    assert list(inspect.signature(crawler.add_site).parameters)[:2] == ["org_id", "project_id"]
    assert "ON CONFLICT (project_id, url)" in inspect.getsource(store.add_url)


def test_web_search_sql_filters_project():
    assert "project_id" in search._WEB_HYBRID_SQL
    assert "project_id" in search._WEB_VECTOR_SQL


def test_web_routes_and_tools_scoped():
    import src.api.routes.web as web_routes
    import src.mcp.web as web_tools

    assert "get_active_project" in inspect.getsource(web_routes)
    assert "get_active_org" not in inspect.getsource(web_routes)
    assert "require_project_id" in inspect.getsource(web_tools)
