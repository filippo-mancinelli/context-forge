import inspect

from src import db
from src.contracts import service


def test_api_contracts_unique_key_moves_to_project():
    src_text = inspect.getsource(db.apply_project_migration)
    assert "api_contracts_project_name_key" in src_text


def test_service_signatures_take_project():
    for fn in (
        service.list_contracts, service.get_contract, service.create_contract,
        service.refresh_contract, service.delete_contract,
        service.list_endpoints, service.get_endpoint,
    ):
        assert list(inspect.signature(fn).parameters)[:2] == ["org_id", "project_id"], fn.__name__


def test_endpoints_insert_carries_project():
    src_text = inspect.getsource(service._store_parse_result)
    assert "project_id" in src_text


def test_routes_and_tools_scoped():
    import src.api.routes.contracts as c_routes
    import src.mcp.contracts as c_tools

    assert "get_active_project" in inspect.getsource(c_routes)
    assert "get_active_org" not in inspect.getsource(c_routes)
    assert "require_project_id" in inspect.getsource(c_tools)
