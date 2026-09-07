import inspect


def test_job_submit_writes_org_and_project():
    import src.mcp.jobs as jobs_tools

    src_text = inspect.getsource(jobs_tools.job_submit)
    assert "resolve_org_id" in src_text
    assert "require_project_id" in src_text
    assert "org_id, project_id" in src_text


def test_job_reads_filter_project():
    import src.mcp.jobs as jobs_tools

    for fn in (jobs_tools.job_status, jobs_tools.job_result):
        assert "project_id" in inspect.getsource(fn), fn.__name__


def test_rest_jobs_scoped_by_project():
    import src.api.routes.jobs as jobs_routes

    src_text = inspect.getsource(jobs_routes)
    assert "get_active_project" in src_text
    assert "get_active_org" not in src_text
    assert "project_id" in inspect.getsource(jobs_routes.list_jobs)
    assert "project_id" in inspect.getsource(jobs_routes.get_job)
