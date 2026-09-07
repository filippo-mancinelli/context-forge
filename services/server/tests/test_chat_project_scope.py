import inspect


def test_chat_routes_use_active_project():
    import src.api.routes.chat as chat_routes

    src_text = inspect.getsource(chat_routes)
    assert "get_active_project" in src_text
    assert "get_active_org" not in src_text


def test_memory_routes_use_project_namespace():
    import src.api.routes.memory as memory_routes

    src_text = inspect.getsource(memory_routes)
    assert "get_active_project" in src_text
    assert "project.namespace" in src_text


def test_chat_sessions_scoped_by_project():
    import src.api.routes.chat_sessions as cs

    for fn in (
        cs.list_sessions, cs.create_session, cs.get_session, cs.update_session,
        cs.delete_session, cs.share_session, cs.unshare_session,
    ):
        assert "project_id" in inspect.getsource(fn), fn.__name__
