"""Git repository management — clone and pull remote repos."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from ..config import RepoConfig, get_settings

logger = logging.getLogger(__name__)


def get_repo_local_path(repo: RepoConfig, org_id: int | None = None) -> str:
    """Return the local filesystem path for a repo (inside the container).

    Remote repos are cached under a per-organization subdirectory so the same
    repository name can be reused across organizations without colliding.
    """
    if repo.type == "local":
        return repo.path or f"/repos/{repo.name}"
    settings = get_settings()
    base = Path(settings.repos_cache_dir)
    if org_id is not None:
        base = base / f"org_{org_id}"
    return str(base / repo.name)


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


async def get_head_commit(local_path: str) -> str | None:
    """Return the current HEAD commit SHA, or None if not a resolvable git repo."""
    code, out, _ = await _run_git("rev-parse", "HEAD", cwd=local_path)
    if code != 0:
        return None
    sha = out.strip()
    return sha or None


async def commit_exists(local_path: str, sha: str) -> bool:
    """Return True if ``sha`` resolves to a commit object present in the repo."""
    if not sha:
        return False
    code, _, _ = await _run_git("cat-file", "-e", f"{sha}^{{commit}}", cwd=local_path)
    return code == 0


async def get_changed_files(
    local_path: str, old_sha: str, new_sha: str
) -> tuple[set[str], set[str]] | None:
    """Diff two commits and return ``(changed_paths, deleted_paths)``.

    ``changed_paths`` are added/modified/type-changed files that must be
    re-chunked; ``deleted_paths`` are removed files whose chunks should be
    dropped. Renames are decomposed into a delete + an add (``--no-renames``) so
    the caller can treat every path uniformly. Returns ``None`` if the diff
    cannot be computed (e.g. a missing commit object), signalling the caller to
    fall back to a full re-index. ``core.quotePath=false`` keeps non-ASCII paths
    literal rather than octal-escaped.
    """
    code, out, err = await _run_git(
        "-c", "core.quotePath=false",
        "diff", "--name-status", "--no-renames", old_sha, new_sha,
        cwd=local_path,
    )
    if code != 0:
        logger.warning("git diff %s..%s failed in %s: %s", old_sha, new_sha, local_path, err.strip())
        return None

    changed: set[str] = set()
    deleted: set[str] = set()
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status, path = parts[0], parts[-1]
        if status.startswith("D"):
            deleted.add(path)
        else:  # A, M, T, and anything else that leaves a file in the tree
            changed.add(path)
    return changed, deleted


async def ensure_repo_cloned(repo: RepoConfig, org_id: int | None = None) -> str:
    """Clone a remote repo if not already present. Returns local path."""
    if repo.type == "local":
        return get_repo_local_path(repo, org_id)

    local_path = get_repo_local_path(repo, org_id)
    path = Path(local_path)

    token = repo.token
    if not token:
        settings = get_settings()
        if repo.type == "github" and settings.github_token:
            token = settings.github_token
        elif repo.type == "gitlab" and settings.gitlab_token:
            token = settings.gitlab_token

    clone_url = _inject_token(repo.url, token) if token else repo.url

    if path.exists() and (path / ".git").exists():
        # Keep the stored remote URL in sync with the current token.
        await _run_git("remote", "set-url", "origin", clone_url, cwd=local_path)
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


async def pull_all_repos(repos: list[RepoConfig], org_id: int | None = None) -> None:
    """Pull updates for all remote repos concurrently."""
    remote_repos = [r for r in repos if r.type != "local"]
    if not remote_repos:
        return
    tasks = [ensure_repo_cloned(r, org_id) for r in remote_repos]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for repo, result in zip(remote_repos, results):
        if isinstance(result, Exception):
            logger.error("Failed to update %s: %s", repo.name, result)
