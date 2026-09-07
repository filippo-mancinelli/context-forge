import asyncio
import pytest

from src import git_providers


class FakeResp:
    def __init__(self, payload, status_code=201, json_raises=False, text=""):
        self._payload = payload
        self.status_code = status_code
        self._json_raises = json_raises
        self.text = text

    def json(self):
        if self._json_raises:
            raise ValueError("Invalid JSON")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"http {self.status_code}")


def _fake_client(expected_url, expected_json, payload, status_code=201, json_raises=False, text=""):
    class FakeClient:
        def __init__(self, *a, **kw): ...
        async def __aenter__(self):
            return self
        async def __aexit__(self, *exc):
            return False
        async def post(self, url, headers=None, json=None):
            assert url == expected_url
            for key, value in expected_json.items():
                assert json[key] == value
            return FakeResp(payload, status_code=status_code, json_raises=json_raises, text=text)
    return FakeClient


def test_github_pr(monkeypatch):
    target = {"provider": "github", "project_path": "lascaux/askmechat",
              "token": "t", "api_base": "https://api.github.com"}
    monkeypatch.setattr(git_providers.httpx, "AsyncClient", _fake_client(
        "https://api.github.com/repos/lascaux/askmechat/pulls",
        {"title": "Fix", "head": "fix/x", "base": "main"},
        {"html_url": "https://github.com/lascaux/askmechat/pull/12", "number": 12},
    ))
    result = asyncio.run(git_providers.create_pull_request(target, "fix/x", "main", "Fix", "body"))
    assert result == {"url": "https://github.com/lascaux/askmechat/pull/12", "number": 12}


def test_gitlab_mr(monkeypatch):
    target = {"provider": "gitlab", "project_path": "askme/desk",
              "token": "t", "api_base": "https://git.lascaux.it/api/v4"}
    monkeypatch.setattr(git_providers.httpx, "AsyncClient", _fake_client(
        "https://git.lascaux.it/api/v4/projects/askme%2Fdesk/merge_requests",
        {"source_branch": "fix/x", "target_branch": "main", "title": "Fix"},
        {"web_url": "https://git.lascaux.it/askme/desk/-/merge_requests/3", "iid": 3},
    ))
    result = asyncio.run(git_providers.create_pull_request(target, "fix/x", "main", "Fix", "body"))
    assert result == {"url": "https://git.lascaux.it/askme/desk/-/merge_requests/3", "number": 3}


def test_github_pr_422_json_error(monkeypatch):
    target = {"provider": "github", "project_path": "lascaux/askmechat",
              "token": "t", "api_base": "https://api.github.com"}
    monkeypatch.setattr(git_providers.httpx, "AsyncClient", _fake_client(
        "https://api.github.com/repos/lascaux/askmechat/pulls",
        {"title": "Fix", "head": "fix/x", "base": "main"},
        {"message": "Validation Failed"},
        status_code=422,
    ))
    with pytest.raises(git_providers.GitProviderError) as exc_info:
        asyncio.run(git_providers.create_pull_request(target, "fix/x", "main", "Fix", "body"))
    assert "422" in str(exc_info.value)


def test_gitlab_mr_500_non_json_error(monkeypatch):
    target = {"provider": "gitlab", "project_path": "askme/desk",
              "token": "t", "api_base": "https://git.lascaux.it/api/v4"}
    monkeypatch.setattr(git_providers.httpx, "AsyncClient", _fake_client(
        "https://git.lascaux.it/api/v4/projects/askme%2Fdesk/merge_requests",
        {"source_branch": "fix/x", "target_branch": "main", "title": "Fix"},
        {},
        status_code=500,
        json_raises=True,
        text="<html><body>Internal Server Error</body></html>",
    ))
    with pytest.raises(git_providers.GitProviderError) as exc_info:
        asyncio.run(git_providers.create_pull_request(target, "fix/x", "main", "Fix", "body"))
    assert "500" in str(exc_info.value)
    assert "GitProviderError" in str(type(exc_info.value))


def test_github_pr_3xx_redirect(monkeypatch):
    target = {"provider": "github", "project_path": "lascaux/askmechat",
              "token": "t", "api_base": "https://api.github.com"}
    monkeypatch.setattr(git_providers.httpx, "AsyncClient", _fake_client(
        "https://api.github.com/repos/lascaux/askmechat/pulls",
        {"title": "Fix", "head": "fix/x", "base": "main"},
        {},
        status_code=302,
    ))
    with pytest.raises(git_providers.GitProviderError) as exc_info:
        asyncio.run(git_providers.create_pull_request(target, "fix/x", "main", "Fix", "body"))
    assert "302" in str(exc_info.value)
