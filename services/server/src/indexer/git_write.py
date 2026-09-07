"""Agent-driven git writes: fresh workspace clone, file changes, commit, push.

Workspaces live under ``Settings.agent_workspaces_dir`` — a directory distinct
from the indexer's read-only clone cache (``repos_cache_dir``). Nothing here
may touch the indexer cache: a write workflow always starts from a fresh
clone of its own so a botched agent run can never corrupt the shared index.
"""
from __future__ import annotations

import os
import re
import shutil

from ..config import get_settings
from ..org_settings import get_org_settings
from .git_manager import _inject_token, _run_git


class GitWriteError(Exception):
    pass


def _redact(text: str, token: str | None = None) -> str:
    """Strip git credential tokens out of raw git output.

    Git commonly echoes the remote URL verbatim in its stderr on failure
    (``fatal: unable to access 'https://oauth2:<token>@host/...'``); that
    text ends up in ``GitWriteError`` messages and anything that logs
    ``str(exc)``, so the token must never survive into it.

    Redaction is caller-independent: if a specific ``token`` is provided,
    it is replaced exactly (belt and braces). Regardless, all embedded git
    credentials of the form ``oauth2:<anything>@`` are unconditionally
    scrubbed via regex so that even if a caller forgets to pass the token,
    the most common form of leak is still blocked.
    """
    # Unconditional regex scrub: oauth2:<anything>@ → oauth2:***@
    # This blocks the leak even if token parameter is not provided.
    text = re.sub(r"oauth2:[^@\s/]+@", "oauth2:***@", text)

    # If a specific token was provided, also scrub it exactly (belt and braces).
    if token:
        text = text.replace(f"oauth2:{token}", "oauth2:***").replace(token, "***")

    return text


def _workspace_path(org_id: int, repo_name: str) -> str:
    """Resolve the per-org workspace directory for ``repo_name``.

    ``repo_name`` is member-settable (``POST /repos``, no character
    validation) and may legitimately contain subpaths — e.g. a GitHub
    ``owner/repo`` style name — so slashes are allowed. Only escaping the
    per-org workspace directory itself (via ``..`` or an absolute name) is
    rejected, checked after resolving ``.``/``..``/symlinks via realpath so
    it can't be bypassed the way a plain string check could.
    """
    base = os.path.join(get_settings().agent_workspaces_dir, f"org_{org_id}")
    base_real = os.path.realpath(base)
    path = os.path.realpath(os.path.join(base_real, repo_name))
    if path != base_real and not path.startswith(base_real + os.sep):
        raise GitWriteError("invalid repository name")
    return path


async def _clone_url(repo, org_id: int) -> tuple[str, str | None]:
    """Resolve the authenticated clone/push URL for a repo.

    Token resolution mirrors ``git_providers.resolve_repo``: a per-repo token
    override wins, otherwise the token configured for the calling
    organization (``org_settings``) is used — there is no global fallback.
    Non-http(s) URLs (file://, ssh://, git@...) never carry an injected
    token, and the org lookup is skipped entirely for them.

    Returns ``(url, token)``. The token is handed back only so a failed
    clone/push can redact it out of git's stderr — it must never itself be
    logged or included in an exception message.
    """
    if not repo.url or not repo.url.startswith("http"):
        return repo.url, None
    token = repo.token
    if not token:
        s = await get_org_settings(org_id)
        if repo.type == "github":
            token = s.github_token
        elif repo.type == "gitlab":
            token = s.gitlab_token
    if token:
        return _inject_token(repo.url, token), token
    return repo.url, None


async def prepare_workspace(repo, org_id: int, base_branch: str) -> str:
    """Clone a fresh, single-branch workspace for agent-driven writes.

    Any previous workspace for this org/repo is discarded first so every
    write starts from a clean checkout of ``base_branch``.
    """
    if repo.type not in ("github", "gitlab"):
        raise GitWriteError(
            f"repository '{repo.name}' has type '{repo.type}'; only github/gitlab "
            "repos support git writes"
        )

    path = _workspace_path(org_id, repo.name)
    shutil.rmtree(path, ignore_errors=True)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    clone_url, token = await _clone_url(repo, org_id)
    code, _, err = await _run_git(
        "clone", "--single-branch", "--branch", base_branch, clone_url, path
    )
    if code != 0:
        raise GitWriteError(
            f"clone of '{repo.name}' ({base_branch}) failed: {_redact(err.strip(), token)}"
        )
    return path


def _safe_join(root: str, rel: str) -> str:
    """Resolve ``rel`` under ``root``, refusing anything that would escape it.

    Guards against both absolute paths (which would replace ``root``
    outright when joined) and ``..`` traversal (caught after resolving
    symlinks/`.`/`..` via realpath) — an agent-supplied ``files[].path``
    must never be able to write outside its own workspace.
    """
    if os.path.isabs(rel):
        raise GitWriteError(f"unsafe path: {rel}")
    root_real = os.path.realpath(root)
    full = os.path.realpath(os.path.join(root_real, rel))
    if full != root_real and not full.startswith(root_real + os.sep):
        raise GitWriteError(f"unsafe path: {rel}")
    return full


async def commit_and_push(path: str, branch_name: str, files: list[dict],
                          message: str, author_name: str, author_email: str,
                          token: str | None = None) -> str:
    """Apply ``files`` on a new branch in ``path``, commit, and push it.

    ``files`` items are ``{"path": str, "content": str}`` (write/overwrite)
    or ``{"path": str, "delete": True}`` (remove). Returns the new commit
    SHA. Raises ``GitWriteError`` on any unsafe path, a write target that is
    an existing directory, or a failed git step.

    ``token`` is the credential (if any) that ``prepare_workspace`` /
    ``_clone_url`` embedded in this workspace's ``origin`` remote — pass it
    through so a failed push can't echo it back in the raised error.
    """
    if not files:
        raise GitWriteError("no file changes provided")
    code, _, err = await _run_git("checkout", "-b", branch_name, cwd=path)
    if code != 0:
        raise GitWriteError(f"branch '{branch_name}' creation failed: {err.strip()}")
    for change in files:
        target = _safe_join(path, change["path"])
        if change.get("delete"):
            if os.path.isfile(target):
                os.remove(target)
            continue
        if os.path.isdir(target):
            raise GitWriteError(f"cannot write '{change['path']}': target is a directory")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(change.get("content", ""))
    await _run_git("add", "-A", cwd=path)
    code, _, err = await _run_git(
        "-c", f"user.name={author_name}", "-c", f"user.email={author_email}",
        "commit", "-m", message, cwd=path,
    )
    if code != 0:
        raise GitWriteError(f"commit failed: {err.strip()}")
    code, _, err = await _run_git("push", "-u", "origin", "--", branch_name, cwd=path)
    if code != 0:
        raise GitWriteError(f"push failed: {_redact(err.strip(), token)}")
    code, sha, _ = await _run_git("rev-parse", "HEAD", cwd=path)
    return sha.strip()
