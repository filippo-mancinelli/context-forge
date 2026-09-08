import inspect

from src import search
from src.kb import store


def test_kb_store_signatures_take_project():
    assert list(inspect.signature(store.save_upload).parameters)[:2] == ["org_id", "project_id"]
    assert list(inspect.signature(store.delete_document).parameters)[:2] == ["org_id", "project_id"]
    assert list(inspect.signature(store.search_documents).parameters)[:2] == ["org_id", "project_id"]


def test_kb_search_sql_filters_project():
    assert "project_id" in search._kb_hybrid_sql(1536)
    assert "project_id" in search._kb_vector_sql(1536)
    assert "project_id" in inspect.signature(search.search_kb_chunks).parameters


def test_kb_chunks_inherit_document_project():
    src_text = inspect.getsource(store._run_extraction)
    assert "project_id" in src_text


def test_kb_routes_and_tools_scoped():
    import src.api.routes.knowledge as kb_routes
    import src.mcp.knowledge as kb_tools

    assert "get_active_project" in inspect.getsource(kb_routes)
    assert "get_active_org" not in inspect.getsource(kb_routes)
    assert "require_project_id" in inspect.getsource(kb_tools)
