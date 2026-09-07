"""MCP tools to propose code changes: branch + commit + push, then PR/MR."""
from __future__ import annotations

try:
    from fastmcp.exceptions import ToolError
except ImportError:  # older fastmcp
    ToolError = RuntimeError

from ..db import get_pool
from ..git_providers import GitProviderError, create_pull_request, resolve_repo
from ..indexer.git_write import GitWriteError, commit_and_push, prepare_workspace
from .context import require_project_id, resolve_org_id
from .permissions import requires_permission
from .server import mcp

# Identity used for commits made on behalf of an agent — there is no
# per-user git identity for MCP callers (API keys / service tokens).
AGENT_AUTHOR_NAME = "ContextForge Agent"
AGENT_AUTHOR_EMAIL = "context-forge@example.com"


@mcp.tool()
@requires_permission("repo-write")
async def repo_commit_files(repo_name: str, branch_name: str, files: list[dict],
                            commit_message: str, base_branch: str | None = None) -> dict:
    """Create a branch on a github/gitlab repo and push file changes to it.

    Args:
        repo_name: Repository name (from repo_list)
        branch_name: Name of the new/existing branch to push to. Must not be
            the repo's default branch — commits always go to a dedicated branch.
        files: List of {"path": "rel/path", "content": "COMPLETE new file content"}
            or {"path": "rel/path", "delete": true}
        commit_message: Commit message
        base_branch: Branch to start from (default: the repo's configured branch)

    Follow with repo_open_pr to open the pull request for human review. Never
    pushes to the repo's default branch itself.

    Returns:
        dict with the branch, base_branch, commit_sha, and files_changed count
    """
    org_id = await resolve_org_id()
    project_id = await require_project_id()

    pool = await get_pool()
    async with pool.acquire() as conn:
        in_project = await conn.fetchval(
            "SELECT 1 FROM repos WHERE org_id=$1 AND project_id=$2 AND name=$3",
            org_id, project_id, repo_name,
        )
    if not in_project:
        return {"status": "error", "error": f"Repository '{repo_name}' not found"}

    try:
        target = await resolve_repo(org_id, repo_name)
        repo = target["repo"]
        if branch_name == repo.branch:
            raise GitWriteError(
                f"cannot commit to '{repo.branch}': that is the repository's default "
                "branch. Use a dedicated branch name for repo_commit_files."
            )
        base = base_branch or repo.branch
        path = await prepare_workspace(repo, org_id, base)
        sha = await commit_and_push(path, branch_name, files, commit_message,
                                    AGENT_AUTHOR_NAME, AGENT_AUTHOR_EMAIL)
    except (GitProviderError, GitWriteError) as exc:
        raise ToolError(str(exc))
    return {"status": "ok", "repo": repo_name, "branch": branch_name,
            "base_branch": base, "commit_sha": sha, "files_changed": len(files)}


@mcp.tool()
@requires_permission("repo-write")
async def repo_open_pr(repo_name: str, branch_name: str, title: str,
                       description: str = "", base_branch: str | None = None) -> dict:
    """Open a pull request (GitHub) or merge request (GitLab) from an existing branch.

    Args:
        repo_name: Repository name (from repo_list)
        branch_name: Source branch to open the PR from (as pushed by repo_commit_files)
        title: PR/MR title
        description: PR/MR description body
        base_branch: Branch to merge into (default: the repo's configured branch)

    Use after repo_commit_files. Returns the PR URL for the human reviewer;
    include it in your final answer.

    Returns:
        dict with pr_url and pr_number
    """
    org_id = await resolve_org_id()
    project_id = await require_project_id()

    pool = await get_pool()
    async with pool.acquire() as conn:
        in_project = await conn.fetchval(
            "SELECT 1 FROM repos WHERE org_id=$1 AND project_id=$2 AND name=$3",
            org_id, project_id, repo_name,
        )
    if not in_project:
        return {"status": "error", "error": f"Repository '{repo_name}' not found"}

    try:
        target = await resolve_repo(org_id, repo_name)
        base = base_branch or target["repo"].branch
        pr = await create_pull_request(target, branch_name, base, title, description)
    except GitProviderError as exc:
        raise ToolError(str(exc))
    return {"status": "ok", "repo": repo_name, "branch": branch_name,
            "base_branch": base, "pr_url": pr["url"], "pr_number": pr["number"]}
