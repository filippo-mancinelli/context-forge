import asyncio

import pytest

from src import git_providers
from src.config import RepoConfig
from src.org_settings import OrgSettings


def _cfg(repos):
    class Cfg:
        pass
    c = Cfg()
    c.repos = repos
    return c


def _org_config(monkeypatch, repos):
    async def fake_get_org_config(org_id):
        return _cfg(repos)

    monkeypatch.setattr(git_providers, "get_org_config", fake_get_org_config)


def _org_settings(monkeypatch, **overrides):
    async def fake_get_org_settings(org_id):
        return OrgSettings(**overrides)

    monkeypatch.setattr(git_providers, "get_org_settings", fake_get_org_settings)


def test_resolve_github_repo(monkeypatch):
    repo = RepoConfig(name="lascaux-askmechat", type="github",
                       url="https://github.com/lascaux/askmechat.git",
                       branch="main", token="tok-1")
    _org_config(monkeypatch, [repo])
    target = asyncio.run(git_providers.resolve_repo(1, "lascaux-askmechat"))
    assert target["provider"] == "github"
    assert target["project_path"] == "lascaux/askmechat"
    assert target["api_base"] == "https://api.github.com"
    assert target["token"] == "tok-1"
    headers = git_providers.provider_headers(target)
    assert headers["Authorization"] == "Bearer tok-1"


def test_resolve_selfhosted_gitlab(monkeypatch):
    repo = RepoConfig(name="desk", type="gitlab",
                       url="https://git.lascaux.it/askme/desk.git",
                       branch="main", token="glpat-1")
    _org_config(monkeypatch, [repo])
    target = asyncio.run(git_providers.resolve_repo(1, "desk"))
    assert target["api_base"] == "https://git.lascaux.it/api/v4"
    assert target["project_path"] == "askme/desk"
    assert git_providers.provider_headers(target)["PRIVATE-TOKEN"] == "glpat-1"


def test_local_repo_rejected(monkeypatch):
    repo = RepoConfig(name="loc", type="local", path="/repos/loc", branch="main")
    _org_config(monkeypatch, [repo])
    with pytest.raises(git_providers.GitProviderError, match="local"):
        asyncio.run(git_providers.resolve_repo(1, "loc"))


def test_unknown_repo_rejected(monkeypatch):
    _org_config(monkeypatch, [])
    with pytest.raises(git_providers.GitProviderError, match="not found"):
        asyncio.run(git_providers.resolve_repo(1, "missing"))


def test_repo_without_token_falls_back_to_org_settings(monkeypatch):
    """No per-repo token override: falls back to the *calling org's* token,
    never a global/env one."""
    repo = RepoConfig(name="lascaux-askmechat", type="github",
                       url="https://github.com/lascaux/askmechat.git",
                       branch="main")
    _org_config(monkeypatch, [repo])
    _org_settings(monkeypatch, github_token="org-tok")
    target = asyncio.run(git_providers.resolve_repo(1, "lascaux-askmechat"))
    assert target["token"] == "org-tok"


def test_repo_token_override_wins_over_org_settings(monkeypatch):
    repo = RepoConfig(name="lascaux-askmechat", type="github",
                       url="https://github.com/lascaux/askmechat.git",
                       branch="main", token="repo-tok")
    _org_config(monkeypatch, [repo])
    _org_settings(monkeypatch, github_token="org-tok")
    target = asyncio.run(git_providers.resolve_repo(1, "lascaux-askmechat"))
    assert target["token"] == "repo-tok"
