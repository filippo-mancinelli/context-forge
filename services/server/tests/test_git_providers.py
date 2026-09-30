import asyncio

import pytest

from src import git_providers
from src.catalog import repos as repo_catalog
from src.config import RepoConfig
from src.org_settings import OrgSettings


def _selected(monkeypatch, repo):
    async def fake_resolve(org_id, project_id, name):
        assert (org_id, project_id) == (1, 4)
        if repo is None:
            raise repo_catalog.RepoNotAvailableError(f"Repository '{name}' is not available in this project")
        return repo

    monkeypatch.setattr(git_providers.repo_catalog, "resolve_project_repo", fake_resolve)


def _org_settings(monkeypatch, **overrides):
    async def fake_get_org_settings(org_id):
        return OrgSettings(**overrides)

    monkeypatch.setattr(git_providers, "get_org_settings", fake_get_org_settings)


def test_resolve_github_repo(monkeypatch):
    _selected(monkeypatch, RepoConfig(name="larkin-asterchat", type="github",
                                      url="https://github.com/larkin/asterchat.git", branch="main", token="tok-1"))
    target = asyncio.run(git_providers.resolve_repo(1, 4, "larkin-asterchat"))
    assert target["provider"] == "github"
    assert target["project_path"] == "larkin/asterchat"
    assert target["api_base"] == "https://api.github.com"
    assert git_providers.provider_headers(target)["Authorization"] == "Bearer tok-1"


def test_resolve_selfhosted_gitlab(monkeypatch):
    _selected(monkeypatch, RepoConfig(name="desk", type="gitlab", url="https://git.example.org/aster/desk.git",
                                      branch="main", token="glpat-1"))
    target = asyncio.run(git_providers.resolve_repo(1, 4, "desk"))
    assert target["api_base"] == "https://git.example.org/api/v4"
    assert target["project_path"] == "aster/desk"
    assert git_providers.provider_headers(target)["PRIVATE-TOKEN"] == "glpat-1"


def test_local_repo_rejected(monkeypatch):
    _selected(monkeypatch, RepoConfig(name="loc", type="local", path="/repos/loc", branch="main"))
    with pytest.raises(git_providers.GitProviderError, match="local"):
        asyncio.run(git_providers.resolve_repo(1, 4, "loc"))


def test_repo_not_selected_by_the_project_is_rejected(monkeypatch):
    _selected(monkeypatch, None)
    with pytest.raises(git_providers.GitProviderError, match="not available in this project"):
        asyncio.run(git_providers.resolve_repo(1, 4, "missing"))


def test_repo_without_token_falls_back_to_org_settings(monkeypatch):
    """No per-repo token override: falls back to the calling org's token, never a global one."""
    _selected(monkeypatch, RepoConfig(name="larkin-asterchat", type="github",
                                      url="https://github.com/larkin/asterchat.git", branch="main"))
    _org_settings(monkeypatch, github_token="org-tok")
    assert asyncio.run(git_providers.resolve_repo(1, 4, "larkin-asterchat"))["token"] == "org-tok"


def test_repo_token_override_wins_over_org_settings(monkeypatch):
    _selected(monkeypatch, RepoConfig(name="larkin-asterchat", type="github",
                                      url="https://github.com/larkin/asterchat.git", branch="main", token="repo-tok"))
    _org_settings(monkeypatch, github_token="org-tok")
    assert asyncio.run(git_providers.resolve_repo(1, 4, "larkin-asterchat"))["token"] == "repo-tok"
