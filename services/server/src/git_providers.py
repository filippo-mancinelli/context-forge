"""Shared GitHub/GitLab helpers: resolve a configured repo to its API target.

Used by CI read operations (``ci.service``) and by git-write operations
(branch/PR tools). Tokens resolve per organization: an explicit per-repo
token override wins, otherwise the token configured for the calling
organization (``org_settings``) is used — there is no global fallback.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlparse

import httpx

from .org_config import get_org_config
from .org_settings import get_org_settings


class GitProviderError(Exception):
    pass


async def resolve_repo(org_id: int, repo_name: str) -> dict[str, Any]:
    """Resolve a configured repo to its provider, API base, project path, and token.

    Raises GitProviderError if the repo is unknown, is a local repo (no
    remote provider), or has no URL to derive an API target from.
    """
    cfg = await get_org_config(org_id)
    repo = next((r for r in cfg.repos if r.name == repo_name), None)
    if repo is None:
        raise GitProviderError(f"Repository '{repo_name}' not found")
    if repo.type not in ("github", "gitlab") or not repo.url:
        raise GitProviderError(
            f"Repository '{repo_name}' has no git provider (type '{repo.type}'); "
            "only github/gitlab repos with a URL are supported"
        )

    token = repo.token
    if not token:
        s = await get_org_settings(org_id)
        token = s.github_token if repo.type == "github" else s.gitlab_token

    parsed = urlparse(repo.url)
    project_path = parsed.path.strip("/").removesuffix(".git")
    if not project_path:
        raise GitProviderError(f"Cannot derive project path from URL '{repo.url}'")

    return {
        "provider": repo.type,
        "project_path": project_path,
        # Self-hosted GitLab instances live on their own host; GitHub is fixed.
        "api_base": "https://api.github.com" if repo.type == "github"
        else f"{parsed.scheme}://{parsed.netloc}/api/v4",
        "token": token,
        "repo": repo,
    }


def provider_headers(target: dict[str, Any]) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if target["token"]:
        if target["provider"] == "github":
            headers["Authorization"] = f"Bearer {target['token']}"
            headers["Accept"] = "application/vnd.github+json"
        else:
            headers["PRIVATE-TOKEN"] = target["token"]
    return headers


async def create_pull_request(target: dict, head: str, base: str, title: str, body: str) -> dict:
    """Open a PR (GitHub) or MR (GitLab) on the resolved repo target.

    Raises GitProviderError on a non-2xx response from the provider API.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        if target["provider"] == "github":
            resp = await client.post(
                f"{target['api_base']}/repos/{target['project_path']}/pulls",
                headers=provider_headers(target),
                json={"title": title, "head": head, "base": base, "body": body},
            )
        else:
            encoded = quote(target["project_path"], safe="")
            resp = await client.post(
                f"{target['api_base']}/projects/{encoded}/merge_requests",
                headers=provider_headers(target),
                json={"source_branch": head, "target_branch": base,
                      "title": title, "description": body},
            )
    if not (200 <= resp.status_code < 300):
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        raise GitProviderError(f"PR creation failed ({resp.status_code}): {detail}")
    data = resp.json()
    return {"url": data.get("html_url") or data.get("web_url"),
            "number": data.get("number") or data.get("iid")}
