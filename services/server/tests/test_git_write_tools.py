import asyncio

import pytest

from src.mcp import git_write_tools as gwt
from src.mcp import permissions as perms


def _underlying(tool):
    return getattr(tool, "fn", tool)


class FakeConn:
    def __init__(self, fetchval_result=1):
        self.fetchval_result = fetchval_result
        self.fetchval_calls = []

    async def fetchval(self, query, *args):
        self.fetchval_calls.append((query, args))
        return self.fetchval_result


class FakeAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return FakeAcquire(self.conn)


@pytest.fixture
def wired(monkeypatch):
    class Repo:
        name = "demo"
        branch = "main"

    async def fake_resolve_org():
        return 1

    async def fake_require_project_id():
        return 42

    async def fake_get_pool():
        return FakePool(FakeConn(fetchval_result=1))

    async def fake_resolve_repo(org_id, repo_name):
        return {"provider": "github", "project_path": "l/demo",
                "token": "t", "api_base": "https://api.github.com", "repo": Repo()}

    async def fake_prepare(repo, org_id, base_branch):
        return "/ws/demo"

    async def fake_commit(path, branch, files, msg, an, ae):
        return "a" * 40

    async def fake_pr(target, head, base, title, body):
        return {"url": "https://github.com/l/demo/pull/9", "number": 9}

    monkeypatch.setattr(gwt, "resolve_org_id", fake_resolve_org)
    monkeypatch.setattr(gwt, "require_project_id", fake_require_project_id)
    monkeypatch.setattr(gwt, "get_pool", fake_get_pool)
    monkeypatch.setattr(gwt, "resolve_repo", fake_resolve_repo)
    monkeypatch.setattr(gwt, "prepare_workspace", fake_prepare)
    monkeypatch.setattr(gwt, "commit_and_push", fake_commit)
    monkeypatch.setattr(gwt, "create_pull_request", fake_pr)


@pytest.fixture(autouse=True)
def _reset_permissions():
    yield
    perms.set_current_permissions(None)


def test_commit_files_happy_path(wired):
    perms.set_current_permissions(None)
    result = asyncio.run(_underlying(gwt.repo_commit_files)(
        "demo", "fix/t-1", [{"path": "a.py", "content": "x"}], "fix: t-1"))
    assert result["commit_sha"] == "a" * 40
    assert result["branch"] == "fix/t-1"
    assert result["base_branch"] == "main"


def test_open_pr_happy_path(wired):
    perms.set_current_permissions(None)
    result = asyncio.run(_underlying(gwt.repo_open_pr)(
        "demo", "fix/t-1", "Fix ticket 1", "details"))
    assert result == {"status": "ok", "repo": "demo", "branch": "fix/t-1",
                      "base_branch": "main", "pr_url": "https://github.com/l/demo/pull/9",
                      "pr_number": 9}


def test_requires_repo_write_permission(wired):
    perms.set_current_permissions(frozenset({"context-read"}))
    with pytest.raises(Exception, match="repo-write"):
        asyncio.run(_underlying(gwt.repo_commit_files)(
            "demo", "b", [{"path": "a", "content": "x"}], "m"))


def test_commit_files_rejects_default_branch(wired):
    """Amendment #4: repo_commit_files must never target the repo's default branch."""
    perms.set_current_permissions(None)
    with pytest.raises(Exception, match="default branch"):
        asyncio.run(_underlying(gwt.repo_commit_files)(
            "demo", "main", [{"path": "a.py", "content": "x"}], "fix: t-1"))


def test_commit_files_rejects_when_no_project_selected(wired, monkeypatch):
    async def fake_require_project_id():
        raise gwt.ToolError("No project selected")

    async def boom_prepare(*a, **k):
        raise AssertionError("prepare_workspace must not run without a selected project")

    monkeypatch.setattr(gwt, "require_project_id", fake_require_project_id)
    monkeypatch.setattr(gwt, "prepare_workspace", boom_prepare)
    perms.set_current_permissions(None)
    with pytest.raises(Exception, match="No project selected"):
        asyncio.run(_underlying(gwt.repo_commit_files)(
            "demo", "fix/t-1", [{"path": "a.py", "content": "x"}], "fix: t-1"))


def test_open_pr_rejects_when_no_project_selected(wired, monkeypatch):
    async def fake_require_project_id():
        raise gwt.ToolError("No project selected")

    async def boom_pr(*a, **k):
        raise AssertionError("create_pull_request must not run without a selected project")

    monkeypatch.setattr(gwt, "require_project_id", fake_require_project_id)
    monkeypatch.setattr(gwt, "create_pull_request", boom_pr)
    perms.set_current_permissions(None)
    with pytest.raises(Exception, match="No project selected"):
        asyncio.run(_underlying(gwt.repo_open_pr)(
            "demo", "fix/t-1", "Fix ticket 1", "details"))


def test_commit_files_rejects_repo_in_other_project(wired, monkeypatch):
    async def fake_get_pool():
        return FakePool(FakeConn(fetchval_result=None))

    async def boom_prepare(*a, **k):
        raise AssertionError("prepare_workspace must not run for a repo outside the project")

    monkeypatch.setattr(gwt, "get_pool", fake_get_pool)
    monkeypatch.setattr(gwt, "prepare_workspace", boom_prepare)
    perms.set_current_permissions(None)
    result = asyncio.run(_underlying(gwt.repo_commit_files)(
        "demo", "fix/t-1", [{"path": "a.py", "content": "x"}], "fix: t-1"))
    assert result["status"] == "error"
    assert "not found" in result["error"]


def test_open_pr_rejects_repo_in_other_project(wired, monkeypatch):
    async def fake_get_pool():
        return FakePool(FakeConn(fetchval_result=None))

    async def boom_pr(*a, **k):
        raise AssertionError("create_pull_request must not run for a repo outside the project")

    monkeypatch.setattr(gwt, "get_pool", fake_get_pool)
    monkeypatch.setattr(gwt, "create_pull_request", boom_pr)
    perms.set_current_permissions(None)
    result = asyncio.run(_underlying(gwt.repo_open_pr)(
        "demo", "fix/t-1", "Fix ticket 1", "details"))
    assert result["status"] == "error"
    assert "not found" in result["error"]
