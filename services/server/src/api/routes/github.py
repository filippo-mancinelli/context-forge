"""GitHub integration API routes."""
from __future__ import annotations

from typing import Optional

import httpx
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from ...config import RepoConfig, get_forge_config, get_settings
from ...indexer.indexer import sync_repos_config
from ...runtime_state import persist_runtime_config
from ..security import require_valid_token_or_raise

router = APIRouter(prefix="/github", tags=["github"])


class GitHubRepo(BaseModel):
    id: int
    name: str
    full_name: str
    description: Optional[str] = None
    url: str
    clone_url: str
    default_branch: str
    private: bool
    language: Optional[str] = None
    stargazers_count: int = 0
    fork: bool = False


class AddGitHubRepoRequest(BaseModel):
    full_name: str
    branch: Optional[str] = None


@router.get("/repos", response_model=list[GitHubRepo])
async def list_github_repos(
    page: int = 1,
    per_page: int = 100,
    authorization: str | None = Header(default=None),
):
    """List GitHub repositories accessible to the configured token."""
    await require_valid_token_or_raise(authorization)
    
    settings = get_settings()
    if not settings.github_token:
        raise HTTPException(status_code=400, detail="GitHub token not configured")
    
    headers = {
        "Authorization": f"token {settings.github_token}",
        "Accept": "application/vnd.github.v3+json",
    }
    
    async with httpx.AsyncClient() as client:
        # Get user's repos
        repos_resp = await client.get(
            "https://api.github.com/user/repos",
            headers=headers,
            params={
                "sort": "updated",
                "direction": "desc",
                "per_page": per_page,
                "page": page,
                "affiliation": "owner,collaborator,organization_member",
            },
            timeout=30.0,
        )
        
        if repos_resp.status_code != 200:
            raise HTTPException(
                status_code=repos_resp.status_code,
                detail=f"GitHub API error: {repos_resp.text}",
            )
        
        repos_data = repos_resp.json()
        repos = []
        for r in repos_data:
            repos.append(GitHubRepo(
                id=r["id"],
                name=r["name"],
                full_name=r["full_name"],
                description=r.get("description"),
                url=r["html_url"],
                clone_url=r["clone_url"],
                default_branch=r["default_branch"],
                private=r["private"],
                language=r.get("language"),
                stargazers_count=r.get("stargazers_count", 0),
                fork=r.get("fork", False),
            ))
        
        return repos


@router.post("/repos/add")
async def add_github_repo(
    req: AddGitHubRepoRequest,
    authorization: str | None = Header(default=None),
):
    """Add a GitHub repository to the forge config."""
    await require_valid_token_or_raise(authorization)

    cfg = get_forge_config()
    
    # Check if repo already exists
    existing = next((r for r in cfg.repos if r.name == req.full_name.replace("/", "-")), None)
    if existing:
        raise HTTPException(status_code=400, detail="Repository already configured")
    
    # Add new repo
    branch = req.branch or "main"
    repo_url = f"https://github.com/{req.full_name}"
    
    # Add to config (will be synced to DB)
    cfg.repos.append(RepoConfig(
        name=req.full_name.replace("/", "-"),
        type="github",
        url=repo_url,
        branch=branch,
    ))

    await persist_runtime_config(cfg)
    await sync_repos_config()
    
    return {
        "status": "ok",
        "message": f"Repository {req.full_name} added",
        "repo": {
            "name": req.full_name.replace("/", "-"),
            "type": "github",
            "url": repo_url,
            "branch": branch,
        },
    }


@router.get("/search")
async def search_github_repos(
    q: str,
    authorization: str | None = Header(default=None),
):
    """Search GitHub repositories."""
    await require_valid_token_or_raise(authorization)
    
    settings = get_settings()
    if not settings.github_token:
        raise HTTPException(status_code=400, detail="GitHub token not configured")
    
    headers = {
        "Authorization": f"token {settings.github_token}",
        "Accept": "application/vnd.github.v3+json",
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.github.com/search/repositories",
            headers=headers,
            params={
                "q": q,
                "sort": "stars",
                "order": "desc",
                "per_page": 30,
            },
            timeout=30.0,
        )
        
        if resp.status_code != 200:
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"GitHub API error: {resp.text}",
            )
        
        data = resp.json()
        repos = []
        for r in data.get("items", []):
            repos.append(GitHubRepo(
                id=r["id"],
                name=r["name"],
                full_name=r["full_name"],
                description=r.get("description"),
                url=r["html_url"],
                clone_url=r["clone_url"],
                default_branch=r["default_branch"],
                private=r["private"],
                language=r.get("language"),
                stargazers_count=r.get("stargazers_count", 0),
                fork=r.get("fork", False),
            ))
        
        return {"repos": repos, "total_count": data.get("total_count", 0)}
