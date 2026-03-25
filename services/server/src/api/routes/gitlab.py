"""GitLab integration API routes."""
from __future__ import annotations

from typing import Optional
from urllib.parse import quote_plus

import httpx
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from ...config import RepoConfig, get_forge_config, get_settings
from ...indexer.indexer import sync_repos_config
from ...runtime_state import persist_runtime_config
from ..security import require_valid_token_or_raise

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
    return "https://gitlab.com/api/v4"


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
    authorization: str | None = Header(default=None),
):
    """List GitLab repositories accessible to the configured token."""
    await require_valid_token_or_raise(authorization)

    settings = get_settings()
    if not settings.gitlab_token:
        raise HTTPException(status_code=400, detail="GitLab token not configured")

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{_gitlab_base_url()}/projects",
            headers=_gitlab_headers(settings.gitlab_token),
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


@router.get("/search")
async def search_gitlab_repos(
    q: str,
    authorization: str | None = Header(default=None),
):
    """Search GitLab repositories visible to the configured token."""
    await require_valid_token_or_raise(authorization)

    settings = get_settings()
    if not settings.gitlab_token:
        raise HTTPException(status_code=400, detail="GitLab token not configured")

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{_gitlab_base_url()}/projects",
            headers=_gitlab_headers(settings.gitlab_token),
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
    authorization: str | None = Header(default=None),
):
    """Add a GitLab repository to the runtime config."""
    await require_valid_token_or_raise(authorization)

    cfg = get_forge_config()
    repo_name = req.full_name.replace("/", "-")
    if any(repo.name == repo_name for repo in cfg.repos):
        raise HTTPException(status_code=400, detail="Repository already configured")

    settings = get_settings()
    if not settings.gitlab_token:
        raise HTTPException(status_code=400, detail="GitLab token not configured")

    encoded = quote_plus(req.full_name)
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{_gitlab_base_url()}/projects/{encoded}",
            headers=_gitlab_headers(settings.gitlab_token),
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

    await persist_runtime_config(cfg)
    await sync_repos_config()

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
