"""GitHub integration API routes."""
from __future__ import annotations

from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...org_settings import get_org_settings
from ..deps import ActiveOrg, get_active_org

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


@router.get("/repos", response_model=list[GitHubRepo])
async def list_github_repos(
    page: int = 1,
    per_page: int = 100,
    org: ActiveOrg = Depends(get_active_org),
):
    """List GitHub repositories accessible to the configured token."""
    s = await get_org_settings(org.org_id)
    if not s.github_token:
        raise HTTPException(status_code=400, detail="GitHub token not configured")

    headers = {
        "Authorization": f"token {s.github_token}",
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


@router.get("/branches")
async def list_github_branches(
    owner: str,
    repo: str,
    org: ActiveOrg = Depends(get_active_org),
):
    """List branches for a GitHub repository."""
    s = await get_org_settings(org.org_id)
    if not s.github_token:
        raise HTTPException(status_code=400, detail="GitHub token not configured")

    headers = {
        "Authorization": f"token {s.github_token}",
        "Accept": "application/vnd.github.v3+json",
    }

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/branches",
            headers=headers,
            params={"per_page": 100},
            timeout=30.0,
        )
        if resp.status_code != 200:
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"GitHub API error: {resp.text}",
            )

        branches = resp.json()
        return [
            {"name": b["name"], "is_default": False}
            for b in branches
        ]


@router.get("/search")
async def search_github_repos(
    q: str,
    org: ActiveOrg = Depends(get_active_org),
):
    """Search GitHub repositories."""
    s = await get_org_settings(org.org_id)
    if not s.github_token:
        raise HTTPException(status_code=400, detail="GitHub token not configured")

    headers = {
        "Authorization": f"token {s.github_token}",
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
