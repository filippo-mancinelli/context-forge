import inspect

from src import db
from src.datasources import service


def test_db_connections_unique_key_moves_to_project():
    src_text = inspect.getsource(db.apply_project_migration)
    assert "db_connections_project_name_key" in src_text


def test_service_signatures_take_project():
    for fn in (
        service.list_connections, service.get_connection, service.resolve_connection,
        service.create_connection, service.update_connection, service.delete_connection,
        service.test_connection, service.schema_overview, service.describe_table,
        service.run_query, service.query_log,
    ):
        assert list(inspect.signature(fn).parameters)[:2] == ["org_id", "project_id"], fn.__name__


def test_query_log_insert_carries_project():
    src_text = inspect.getsource(service.run_query)
    assert "project_id" in src_text


def test_routes_and_tools_scoped():
    import src.api.routes.datasources as ds_routes
    import src.mcp.datasources as ds_tools

    assert "get_active_project" in inspect.getsource(ds_routes)
    assert "get_active_org" not in inspect.getsource(ds_routes)
    assert "require_project_id" in inspect.getsource(ds_tools)
