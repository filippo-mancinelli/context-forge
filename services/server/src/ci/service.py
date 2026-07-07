"""Live CI/CD context for configured repositories.

No storage: runs and logs are fetched on demand from the GitHub Actions or
GitLab CI APIs using the tokens already configured for repository indexing.
Failure logs are ANSI-stripped and tail-truncated so agents get the error,
not megabytes of build output.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional
from urllib.parse import quote, urlparse

import httpx

from ..config import get_settings
from ..org_config import get_org_config

logger = logging.getLogger(__name__)

LOG_TAIL_CHARS = 8000
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07")


class CiError(Exception):
    pass


def _tail(text: str, max_chars: int = LOG_TAIL_CHARS) -> str:
    cleaned = _ANSI_RE.sub("", text or "")
    if len(cleaned) <= max_chars:
        return cleaned
    return "…(truncated)…\n" + cleaned[-max_chars:]


async def _resolve_repo(org_id: int, repo_name: str) -> dict[str, Any]:
    cfg = await get_org_config(org_id)
    repo = next((r for r in cfg.repos if r.name == repo_name), None)
    if repo is None:
        raise CiError(f"Repository '{repo_name}' not found")
    if repo.type not in ("github", "gitlab") or not repo.url:
        raise CiError(
            f"Repository '{repo_name}' has no CI provider (type '{repo.type}'); "
            "only github/gitlab repos with a URL are supported"
        )
    settings = get_settings()
    token = repo.token or (settings.github_token if repo.type == "github" else settings.gitlab_token)
    parsed = urlparse(repo.url)
    project_path = parsed.path.strip("/").removesuffix(".git")
    if not project_path:
        raise CiError(f"Cannot derive project path from URL '{repo.url}'")
    return {
        "provider": repo.type,
        "project_path": project_path,
        # Self-hosted GitLab instances live on their own host; GitHub is fixed.
        "api_base": "https://api.github.com" if repo.type == "github"
        else f"{parsed.scheme}://{parsed.netloc}/api/v4",
        "token": token,
    }


def _headers(target: dict[str, Any]) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if target["token"]:
        if target["provider"] == "github":
            headers["Authorization"] = f"Bearer {target['token']}"
            headers["Accept"] = "application/vnd.github+json"
        else:
            headers["PRIVATE-TOKEN"] = target["token"]
    return headers


# --------------------------------------------------------------------------- #
# Recent runs
# --------------------------------------------------------------------------- #
async def recent_runs(org_id: int, repo_name: str, limit: int = 10) -> list[dict[str, Any]]:
    target = await _resolve_repo(org_id, repo_name)
    limit = max(1, min(limit, 30))
    async with httpx.AsyncClient(timeout=15, headers=_headers(target)) as client:
        if target["provider"] == "github":
            resp = await client.get(
                f"{target['api_base']}/repos/{target['project_path']}/actions/runs",
                params={"per_page": limit},
            )
            resp.raise_for_status()
            return [
                {
                    "id": r["id"],
                    "name": r.get("name") or r.get("display_title"),
                    "status": r.get("status"),
                    "conclusion": r.get("conclusion"),
                    "branch": r.get("head_branch"),
                    "commit": (r.get("head_sha") or "")[:10],
                    "event": r.get("event"),
                    "url": r.get("html_url"),
                    "created_at": r.get("created_at"),
                    "updated_at": r.get("updated_at"),
                }
                for r in resp.json().get("workflow_runs", [])
            ]

        resp = await client.get(
            f"{target['api_base']}/projects/{quote(target['project_path'], safe='')}/pipelines",
            params={"per_page": limit},
        )
        resp.raise_for_status()
        return [
            {
                "id": p["id"],
                "name": p.get("name") or f"pipeline #{p['id']}",
                "status": p.get("status"),
                # Normalize onto the GitHub-style status/conclusion pair.
                "conclusion": p.get("status"),
                "branch": p.get("ref"),
                "commit": (p.get("sha") or "")[:10],
                "event": p.get("source"),
                "url": p.get("web_url"),
                "created_at": p.get("created_at"),
                "updated_at": p.get("updated_at"),
            }
            for p in resp.json()
        ]


# --------------------------------------------------------------------------- #
# Failure detail: failed jobs + error log tail
# --------------------------------------------------------------------------- #
async def failure_detail(
    org_id: int, repo_name: str, run_id: Optional[int] = None, max_log_chars: int = LOG_TAIL_CHARS
) -> dict[str, Any]:
    target = await _resolve_repo(org_id, repo_name)
    max_log_chars = max(500, min(max_log_chars, 20000))
    async with httpx.AsyncClient(timeout=30, headers=_headers(target), follow_redirects=True) as client:
        if target["provider"] == "github":
            return await _github_failure(client, target, run_id, max_log_chars)
        return await _gitlab_failure(client, target, run_id, max_log_chars)


async def _github_failure(
    client: httpx.AsyncClient, target: dict[str, Any], run_id: Optional[int], max_log_chars: int
) -> dict[str, Any]:
    base = f"{target['api_base']}/repos/{target['project_path']}"
    if run_id is None:
        resp = await client.get(
            f"{base}/actions/runs", params={"status": "completed", "per_page": 20}
        )
        resp.raise_for_status()
        failed = [r for r in resp.json().get("workflow_runs", []) if r.get("conclusion") == "failure"]
        if not failed:
            return {"found": False, "message": "No failed workflow runs among the last 20 completed runs"}
        run = failed[0]
        run_id = run["id"]
    else:
        resp = await client.get(f"{base}/actions/runs/{run_id}")
        resp.raise_for_status()
        run = resp.json()

    jobs_resp = await client.get(f"{base}/actions/runs/{run_id}/jobs", params={"per_page": 50})
    jobs_resp.raise_for_status()
    failed_jobs = []
    for job in jobs_resp.json().get("jobs", []):
        if job.get("conclusion") != "failure":
            continue
        failed_steps = [
            s.get("name") for s in job.get("steps", []) if s.get("conclusion") == "failure"
        ]
        log = ""
        try:
            log_resp = await client.get(f"{base}/actions/jobs/{job['id']}/logs")
            if log_resp.status_code == 200:
                log = _tail(log_resp.text, max_log_chars)
        except httpx.HTTPError as e:  # log download is best-effort
            log = f"(log unavailable: {e})"
        failed_jobs.append(
            {"name": job.get("name"), "failed_steps": failed_steps, "log_tail": log, "url": job.get("html_url")}
        )

    return {
        "found": True,
        "run": {
            "id": run["id"],
            "name": run.get("name") or run.get("display_title"),
            "branch": run.get("head_branch"),
            "commit": (run.get("head_sha") or "")[:10],
            "conclusion": run.get("conclusion"),
            "url": run.get("html_url"),
            "created_at": run.get("created_at"),
        },
        "failed_jobs": failed_jobs,
    }


async def _gitlab_failure(
    client: httpx.AsyncClient, target: dict[str, Any], run_id: Optional[int], max_log_chars: int
) -> dict[str, Any]:
    base = f"{target['api_base']}/projects/{quote(target['project_path'], safe='')}"
    if run_id is None:
        resp = await client.get(f"{base}/pipelines", params={"status": "failed", "per_page": 1})
        resp.raise_for_status()
        pipelines = resp.json()
        if not pipelines:
            return {"found": False, "message": "No failed pipelines found"}
        run_id = pipelines[0]["id"]

    pipe_resp = await client.get(f"{base}/pipelines/{run_id}")
    pipe_resp.raise_for_status()
    pipeline = pipe_resp.json()

    jobs_resp = await client.get(f"{base}/pipelines/{run_id}/jobs", params={"per_page": 100})
    jobs_resp.raise_for_status()
    failed_jobs = []
    for job in jobs_resp.json():
        if job.get("status") != "failed":
            continue
        log = ""
        try:
            trace_resp = await client.get(f"{base}/jobs/{job['id']}/trace")
            if trace_resp.status_code == 200:
                log = _tail(trace_resp.text, max_log_chars)
        except httpx.HTTPError as e:
            log = f"(log unavailable: {e})"
        failed_jobs.append(
            {
                "name": job.get("name"),
                "failed_steps": [job.get("stage")] if job.get("stage") else [],
                "log_tail": log,
                "url": job.get("web_url"),
            }
        )

    return {
        "found": True,
        "run": {
            "id": pipeline["id"],
            "name": pipeline.get("name") or f"pipeline #{pipeline['id']}",
            "branch": pipeline.get("ref"),
            "commit": (pipeline.get("sha") or "")[:10],
            "conclusion": pipeline.get("status"),
            "url": pipeline.get("web_url"),
            "created_at": pipeline.get("created_at"),
        },
        "failed_jobs": failed_jobs,
    }
