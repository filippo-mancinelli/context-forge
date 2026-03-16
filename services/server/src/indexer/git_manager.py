"""Git repository management — clone and pull remote repos."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from ..config import RepoConfig, get_settings

logger = logging.getLogger(__name__)


def get_repo_local_path(repo: RepoConfig) -> str:
    """Return the local filesystem path for a repo (inside the container)."""
    if repo.type == "local":
        return repo.path or f"/repos/{repo.name}"
    settings = get_settings()
    return str(Path(settings.repos_cache_dir) / repo.name)


def _inject_token(url: str, token: str) -> str:
    """Inject a token into a Git HTTPS URL for authentication."""
    parsed = urlparse(url)
    netloc = f"oauth2:{token}@{parsed.hostname}"
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


async def _run_git(*args: str, cwd: str = None) -> tuple[int, str, str]:
    """Run a git command asynchronously."""
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout.decode(), stderr.decode()


async def ensure_repo_cloned(repo: RepoConfig) -> str:
    """Clone a remote repo if not already present. Returns local path."""
    if repo.type == "local":
        return get_repo_local_path(repo)

    local_path = get_repo_local_path(repo)
    path = Path(local_path)

    clone_url = repo.url
    if repo.token:
        clone_url = _inject_token(repo.url, repo.token)

    if path.exists() and (path / ".git").exists():
        logger.info("Pulling latest changes for %s", repo.name)
        code, out, err = await _run_git("pull", "--ff-only", cwd=local_path)
        if code != 0:
            logger.warning("git pull failed for %s: %s", repo.name, err)
            # Try a full reset if pull failed (e.g. diverged)
            await _run_git("fetch", "--all", cwd=local_path)
            await _run_git("reset", "--hard", f"origin/{repo.branch}", cwd=local_path)
        else:
            logger.info("Pulled %s: %s", repo.name, out.strip())
    else:
        path.mkdir(parents=True, exist_ok=True)
        logger.info("Cloning %s from %s", repo.name, repo.url)
        code, out, err = await _run_git(
            "clone", "--depth=1", "--branch", repo.branch, clone_url, local_path
        )
        if code != 0:
            raise RuntimeError(f"git clone failed for {repo.name}: {err}")
        logger.info("Cloned %s successfully", repo.name)

    return local_path


async def pull_all_repos(repos: list[RepoConfig]) -> None:
    """Pull updates for all remote repos concurrently."""
    remote_repos = [r for r in repos if r.type != "local"]
    if not remote_repos:
        return
    tasks = [ensure_repo_cloned(r) for r in remote_repos]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for repo, result in zip(remote_repos, results):
        if isinstance(result, Exception):
            logger.error("Failed to update %s: %s", repo.name, result)
