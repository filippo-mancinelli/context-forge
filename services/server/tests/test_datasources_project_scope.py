import inspect

from src.datasources import service


def test_project_views_take_org_and_project():
    for fn in (
        service.list_connections, service.get_connection, service.resolve_connection,
        service.schema_overview, service.describe_table, service.run_query,
        service.run_write, service.query_log,
    ):
        assert list(inspect.signature(fn).parameters)[:2] == ["org_id", "project_id"], fn.__name__


def test_catalog_operations_take_the_org_only():
    for fn in (
        service.list_catalog_connections, service.get_catalog_connection, service.find_connection,
        service.create_connection, service.update_connection, service.delete_connection,
        service.test_connection, service.mark_pending_secret,
    ):
        params = list(inspect.signature(fn).parameters)
        assert params[0] == "org_id" and "project_id" not in params, fn.__name__


def test_query_log_insert_carries_the_project():
    assert "project_id" in inspect.getsource(service.run_query)


def test_tools_stay_project_scoped():
    import src.mcp.datasources as ds_tools

    assert "require_project_id" in inspect.getsource(ds_tools)
