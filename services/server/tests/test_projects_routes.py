import inspect


def test_router_registered_in_app():
    """Verify projects router is imported and registered in app.py."""
    import src.api.app as app_module
    src_text = inspect.getsource(app_module)
    assert "projects" in src_text and "include_router" in src_text


def test_delete_guards_last_project():
    from src.api.routes import projects as routes
    src_text = inspect.getsource(routes.delete_project_route)
    assert "count_projects" in src_text
