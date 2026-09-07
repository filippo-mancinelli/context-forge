"""GitLab integration API routes."""
from __future__ import annotations

from typing import Optional
from urllib.parse import quote_plus

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...config import RepoConfig, get_settings
from ...indexer.indexer import sync_repos_config
from ...org_config import get_org_config, persist_org_config
from ...org_settings import get_org_settings
from ...projects import bind_repo_to_project, get_repo_project_name
from ..deps import ActiveOrg, ActiveProject, get_active_org, require_project_role

router = APIRouter(prefix="/gitlab", tags=["gitlab"])


class GitLabRepo(BaseModel):
    id: int
    name: str
    full_name: str
    description: Optional[str] = None
    url: str
    clone_url: str
    default_branch: str
    private: bool
    language: Optional[str] = None
    star_count: int = 0
    forked_from_project: bool = False


class AddGitLabRepoRequest(BaseModel):
    full_name: str
    branch: Optional[str] = None


def _gitlab_headers(token: str) -> dict[str, str]:
    return {
        "PRIVATE-TOKEN": token,
        "Accept": "application/json",
    }


def _gitlab_base_url() -> str:
    """GitLab API v4 base URL.

    Configurable via GITLAB_URL (self-hosted instance root or a full /api/v4
    URL); defaults to gitlab.com. No host is hardcoded beyond that default.
    """
    raw = (get_settings().gitlab_url or "https://gitlab.com").strip().rstrip("/")
    return raw if raw.endswith("/api/v4") else f"{raw}/api/v4"


def _map_repo(project: dict) -> GitLabRepo:
    visibility = project.get("visibility", "private")
    namespace = project.get("path_with_namespace") or project.get("name_with_namespace") or project["name"]
    return GitLabRepo(
        id=project["id"],
        name=project["name"],
        full_name=namespace,
        description=project.get("description"),
        url=project["web_url"],
        clone_url=project.get("http_url_to_repo") or project["web_url"],
        default_branch=project.get("default_branch") or "main",
        private=visibility != "public",
        language=None,
        star_count=project.get("star_count", 0),
        forked_from_project=bool(project.get("forked_from_project")),
    )


@router.get("/repos", response_model=list[GitLabRepo])
async def list_gitlab_repos(
    page: int = 1,
    per_page: int = 100,
    org: ActiveOrg = Depends(get_active_org),
):
    """List GitLab repositories accessible to the configured token."""
    s = await get_org_settings(org.org_id)
    if not s.gitlab_token:
        raise HTTPException(status_code=400, detail="GitLab token not configured")

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{_gitlab_base_url()}/projects",
            headers=_gitlab_headers(s.gitlab_token),
            params={
                "membership": True,
                "owned": False,
                "simple": True,
                "order_by": "last_activity_at",
                "sort": "desc",
                "per_page": per_page,
                "page": page,
            },
            timeout=30.0,
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=f"GitLab API error: {resp.text}")

    return [_map_repo(project) for project in resp.json()]


@router.get("/branches")
async def list_gitlab_branches(
    full_name: str,
    org: ActiveOrg = Depends(get_active_org),
):
    """List branches for a GitLab repository.

    Repositories routinely have far more branches than a single API page
    holds, so we page through the branches endpoint until it is exhausted
    (bounded by a safety cap). Returning only the first page would hide any
    branch beyond it (e.g. ``hotfix/*`` sorting after the first 100 names),
    breaking the client-side branch filter.
    """
    s = await get_org_settings(org.org_id)
    if not s.gitlab_token:
        raise HTTPException(status_code=400, detail="GitLab token not configured")

    encoded = quote_plus(full_name)
    url = f"{_gitlab_base_url()}/projects/{encoded}/repository/branches"
    headers = _gitlab_headers(s.gitlab_token)
    collected: list[dict] = []
    max_pages = 30  # cap: 30 * 100 = 3000 branches
    async with httpx.AsyncClient() as client:
        for page in range(1, max_pages + 1):
            resp = await client.get(
                url,
                headers=headers,
                params={"per_page": 100, "page": page},
                timeout=30.0,
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=f"GitLab API error: {resp.text}")
            batch = resp.json()
            # GitLab returns a dict with an error key for missing projects
            if isinstance(batch, dict):
                raise HTTPException(status_code=404, detail=batch.get("message", "Project not found"))
            if not batch:
                break
            collected.extend(batch)
            if not resp.headers.get("X-Next-Page"):
                break

    return [
        {"name": b["name"], "is_default": b.get("default", False)}
        for b in collected
    ]


@router.get("/search")
async def search_gitlab_repos(
    q: str,
    org: ActiveOrg = Depends(get_active_org),
):
    """Search GitLab repositories visible to the configured token."""
    s = await get_org_settings(org.org_id)
    if not s.gitlab_token:
        raise HTTPException(status_code=400, detail="GitLab token not configured")

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{_gitlab_base_url()}/projects",
            headers=_gitlab_headers(s.gitlab_token),
            params={
                "membership": True,
                "search": q,
                "simple": True,
                "order_by": "last_activity_at",
                "sort": "desc",
                "per_page": 50,
            },
            timeout=30.0,
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=f"GitLab API error: {resp.text}")

    repos = [_map_repo(project) for project in resp.json()]
    return {"repos": repos, "total_count": len(repos)}


@router.post("/repos/add")
async def add_gitlab_repo(
    req: AddGitLabRepoRequest,
    org: ActiveProject = Depends(require_project_role("member")),
):
    """Add a GitLab repository to the active project."""
    cfg = await get_org_config(org.org_id)
    repo_name = req.full_name.replace("/", "-")
    if any(repo.name == repo_name for repo in cfg.repos):
        holder = await get_repo_project_name(org.org_id, repo_name)
        detail = (
            f"Repository already configured in project '{holder}'"
            if holder
            else "Repository already configured"
        )
        raise HTTPException(status_code=400, detail=detail)

    s = await get_org_settings(org.org_id)
    if not s.gitlab_token:
        raise HTTPException(status_code=400, detail="GitLab token not configured")

    encoded = quote_plus(req.full_name)
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{_gitlab_base_url()}/projects/{encoded}",
            headers=_gitlab_headers(s.gitlab_token),
            timeout=30.0,
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=f"GitLab API error: {resp.text}")

    project = resp.json()
    branch = req.branch or project.get("default_branch") or "main"
    repo_url = project["web_url"]

    cfg.repos.append(
        RepoConfig(
            name=repo_name,
            type="gitlab",
            url=repo_url,
            branch=branch,
        )
    )

    await persist_org_config(org.org_id, cfg)
    await sync_repos_config(org.org_id)
    await bind_repo_to_project(org.org_id, org.project_id, repo_name)

    return {
        "status": "ok",
        "message": f"Repository {req.full_name} added",
        "repo": {
            "name": repo_name,
            "type": "gitlab",
            "url": repo_url,
            "branch": branch,
        },
    }
