import asyncio

from src.indexer import git_manager
from src.config import RepoConfig
from src.org_settings import OrgSettings
from src.telegram import capture_agent


def test_git_manager_uses_org_token(monkeypatch):
    async def fake_org_settings(org_id):
        assert org_id == 9
        return OrgSettings(github_token="org-token")

    monkeypatch.setattr(git_manager, "get_org_settings", fake_org_settings)
    repo = RepoConfig(name="x", type="github", url="https://github.com/acme/x.git")
    token = asyncio.run(git_manager._resolve_repo_token(repo, 9))
    assert token == "org-token"


def test_capture_agent_llm_family_from_org_settings():
    s = OrgSettings(llm_provider="anthropic", llm_model="claude-haiku-4-5",
                    anthropic_api_key="ak")
    family = capture_agent._llm_family(s)
    assert family == {"family": "anthropic", "model": "claude-haiku-4-5", "api_key": "ak"}
