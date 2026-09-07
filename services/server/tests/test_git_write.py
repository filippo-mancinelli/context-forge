import asyncio
import os
import subprocess
import sys

import pytest

from src import config
from src.config import RepoConfig
from src.indexer import git_write


def _git(*args, cwd=None):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def remote_repo(tmp_path):
    origin = tmp_path / "origin.git"
    _git("init", "--bare", "--initial-branch=main", str(origin))
    seed = tmp_path / "seed"
    _git("clone", str(origin), str(seed))
    (seed / "app.py").write_text("print('v1')\n", encoding="utf-8")
    _git("add", "-A", cwd=str(seed))
    _git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "seed", cwd=str(seed))
    _git("push", "origin", "main", cwd=str(seed))
    return origin


def test_commit_and_push_roundtrip(tmp_path, remote_repo, monkeypatch):
    monkeypatch.setattr(config.get_settings(), "agent_workspaces_dir",
                        str(tmp_path / "ws"), raising=False)
    repo = RepoConfig(name="demo", type="github",
                      url=remote_repo.as_uri(), branch="main")

    async def main():
        path = await git_write.prepare_workspace(repo, org_id=1, base_branch="main")
        sha = await git_write.commit_and_push(
            path, "fix/ticket-123",
            [{"path": "app.py", "content": "print('v2')\n"},
             {"path": "docs/note.md", "content": "fix\n"}],
            "fix: ticket 123", "ContextForge Agent", "forge@lascaux.it")
        return sha

    sha = asyncio.run(main())
    assert len(sha) == 40
    out = subprocess.run(["git", "ls-remote", str(remote_repo)],
                         check=True, capture_output=True, text=True).stdout
    assert "refs/heads/fix/ticket-123" in out


def test_path_traversal_blocked(tmp_path, remote_repo, monkeypatch):
    monkeypatch.setattr(config.get_settings(), "agent_workspaces_dir",
                        str(tmp_path / "ws"), raising=False)
    repo = RepoConfig(name="demo", type="github", url=remote_repo.as_uri(), branch="main")

    async def main():
        path = await git_write.prepare_workspace(repo, org_id=1, base_branch="main")
        await git_write.commit_and_push(
            path, "evil", [{"path": "../outside.txt", "content": "x"}],
            "m", "a", "a@a")

    with pytest.raises(git_write.GitWriteError, match="unsafe path"):
        asyncio.run(main())


def test_absolute_file_path_rejected(tmp_path, remote_repo, monkeypatch):
    monkeypatch.setattr(config.get_settings(), "agent_workspaces_dir",
                        str(tmp_path / "ws"), raising=False)
    repo = RepoConfig(name="demo", type="github", url=remote_repo.as_uri(), branch="main")

    async def main():
        path = await git_write.prepare_workspace(repo, org_id=1, base_branch="main")
        await git_write.commit_and_push(
            path, "evil-abs", [{"path": "/etc/passwd", "content": "x"}],
            "m", "a", "a@a")

    with pytest.raises(git_write.GitWriteError, match="unsafe path"):
        asyncio.run(main())


def test_delete_file_removes_and_commits(tmp_path, remote_repo, monkeypatch):
    monkeypatch.setattr(config.get_settings(), "agent_workspaces_dir",
                        str(tmp_path / "ws"), raising=False)
    repo = RepoConfig(name="demo", type="github", url=remote_repo.as_uri(), branch="main")

    async def main():
        path = await git_write.prepare_workspace(repo, org_id=1, base_branch="main")
        await git_write.commit_and_push(
            path, "chore/remove-app",
            [{"path": "app.py", "delete": True}],
            "chore: remove app.py", "a", "a@a")
        return path

    path = asyncio.run(main())
    assert not os.path.exists(os.path.join(path, "app.py"))
    log = subprocess.run(["git", "log", "-1", "--name-status"], cwd=path,
                         check=True, capture_output=True, text=True).stdout
    assert "D\tapp.py" in log


def test_write_target_is_directory_rejected(tmp_path, remote_repo, monkeypatch):
    monkeypatch.setattr(config.get_settings(), "agent_workspaces_dir",
                        str(tmp_path / "ws"), raising=False)
    repo = RepoConfig(name="demo", type="github", url=remote_repo.as_uri(), branch="main")

    async def main():
        path = await git_write.prepare_workspace(repo, org_id=1, base_branch="main")
        os.makedirs(os.path.join(path, "adir"))
        await git_write.commit_and_push(
            path, "evil-dir", [{"path": "adir", "content": "x"}],
            "m", "a", "a@a")

    with pytest.raises(git_write.GitWriteError, match="directory"):
        asyncio.run(main())


def test_local_type_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(config.get_settings(), "agent_workspaces_dir",
                        str(tmp_path / "ws"), raising=False)
    repo = RepoConfig(name="demo", type="local", path="/repos/demo")

    with pytest.raises(git_write.GitWriteError):
        asyncio.run(git_write.prepare_workspace(repo, org_id=1, base_branch="main"))


def test_repo_name_traversal_blocked(tmp_path, monkeypatch):
    ws_dir = tmp_path / "ws"
    monkeypatch.setattr(config.get_settings(), "agent_workspaces_dir",
                        str(ws_dir), raising=False)
    repo = RepoConfig(name="../../repos-cache/pwned", type="github",
                      url="https://github.com/org/repo.git", branch="main")

    with pytest.raises(git_write.GitWriteError, match="invalid repository name"):
        asyncio.run(git_write.prepare_workspace(repo, org_id=1, base_branch="main"))

    # The guard must fire before any rmtree/clone, so nothing should have
    # been created outside — or even inside — the org workspace dir.
    assert not (tmp_path / "repos-cache").exists()
    assert not ws_dir.exists()


def test_repo_name_with_subpath_allowed(tmp_path, remote_repo, monkeypatch):
    ws_dir = tmp_path / "ws"
    monkeypatch.setattr(config.get_settings(), "agent_workspaces_dir",
                        str(ws_dir), raising=False)
    repo = RepoConfig(name="owner/repo", type="github",
                      url=remote_repo.as_uri(), branch="main")

    path = asyncio.run(git_write.prepare_workspace(repo, org_id=1, base_branch="main"))

    expected = os.path.realpath(str(ws_dir / "org_1" / "owner" / "repo"))
    assert os.path.realpath(path) == expected
    assert os.path.isfile(os.path.join(path, "app.py"))


def test_clone_failure_does_not_leak_token(tmp_path, monkeypatch):
    """Clone failure with injected token does not leak it in error message.

    This test exercises a REAL injected-token error: monkeypatch _run_git to
    return stderr containing the injected token, use an http(s) repo URL so
    _clone_url actually injects the token, and assert the raised GitWriteError
    message does NOT contain the secret token and DOES contain the redaction
    marker.
    """
    monkeypatch.setattr(config.get_settings(), "agent_workspaces_dir",
                        str(tmp_path / "ws"), raising=False)

    repo = RepoConfig(name="demo", type="github",
                      url="https://github.com/org/repo.git", branch="main",
                      token="SECRETTOKEN")

    # Monkeypatch _run_git to simulate a clone failure with injected token in stderr
    original_run_git = git_write._run_git

    async def mock_run_git(*args, **kwargs):
        if args and args[0] == "clone":
            stderr = "fatal: unable to access 'https://oauth2:SECRETTOKEN@github.com/org/repo.git/': Not Found"
            return (128, "", stderr)
        return await original_run_git(*args, **kwargs)

    monkeypatch.setattr(git_write, "_run_git", mock_run_git)

    with pytest.raises(git_write.GitWriteError) as excinfo:
        asyncio.run(git_write.prepare_workspace(repo, org_id=1, base_branch="main"))

    message = str(excinfo.value)
    assert "SECRETTOKEN" not in message, f"Token leaked in error: {message}"
    assert "oauth2:***@" in message, f"Redaction marker not found in error: {message}"
    assert "demo" in message


def test_push_failure_redaction_without_token(tmp_path, remote_repo, monkeypatch):
    """Push failure redaction works even when token parameter is not provided.

    This verifies the unconditional regex pattern scrub works caller-independently:
    even if commit_and_push is called without passing token, any oauth2:*@ pattern
    in git stderr is still redacted.
    """
    monkeypatch.setattr(config.get_settings(), "agent_workspaces_dir",
                        str(tmp_path / "ws"), raising=False)

    repo = RepoConfig(name="demo", type="github", url=remote_repo.as_uri(), branch="main")

    original_run_git = git_write._run_git

    async def mock_run_git(*args, **kwargs):
        if args and args[0] == "push":
            stderr = "fatal: unable to access 'https://oauth2:LEAKED@github.com/org/repo.git/': Permission denied"
            return (128, "", stderr)
        return await original_run_git(*args, **kwargs)

    monkeypatch.setattr(git_write, "_run_git", mock_run_git)

    async def main():
        path = await git_write.prepare_workspace(repo, org_id=1, base_branch="main")
        # Intentionally NOT passing token to commit_and_push
        await git_write.commit_and_push(
            path, "test-branch",
            [{"path": "test.txt", "content": "test"}],
            "test commit", "Author", "author@example.com")

    with pytest.raises(git_write.GitWriteError) as excinfo:
        asyncio.run(main())

    message = str(excinfo.value)
    assert "LEAKED" not in message, f"Token leaked in error: {message}"
    assert "oauth2:***@" in message, f"Redaction marker not found in error: {message}"


def test_redact_strips_token_and_injected_url_form():
    text = ("fatal: unable to access "
            "'https://oauth2:SECRETTOKEN@github.com/org/repo.git/': "
            "The requested URL returned error: 403")

    redacted = git_write._redact(text, "SECRETTOKEN")

    assert "SECRETTOKEN" not in redacted
    assert "oauth2:***" in redacted


def test_redact_unconditional_pattern_without_token():
    """Redaction works even without an exact token parameter."""
    text = ("fatal: unable to access "
            "'https://oauth2:ANYTOKEN@github.com/org/repo.git/': "
            "The requested URL returned error: 403")

    # Call _redact without passing a token parameter
    redacted = git_write._redact(text)

    assert "ANYTOKEN" not in redacted
    assert "oauth2:***@" in redacted
