"""Webhook endpoint for push-triggered incremental re-indexing and auto-memory.

A git host (GitHub, GitLab, or a custom caller) POSTs here on push; we match the
pushed repository against configured repos and queue an index request for each
match. Actual indexing runs incrementally in the scheduler loop, re-processing
only the files changed since the last indexed commit.

Additionally, commit messages following Conventional Commits are automatically
saved as persistent memories so agents can discover recent changes.

Authentication uses a shared secret (``WEBHOOK_SECRET``); the endpoint is
disabled (503) when no secret is configured. It is exempt from the session-auth
guard so external hooks can reach it, and instead verifies the provider's
signature/token directly.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, Header, HTTPException, Request

from ...config import get_settings
from ...db import get_pool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Keys in a push payload that may carry a repository URL or identifier. Covers
# GitHub (`repository`) and GitLab (`project` / `repository`) shapes, plus a
# top-level `repo` for custom callers.
_REPO_URL_KEYS = (
    "clone_url", "git_http_url", "http_url_to_repo", "html_url",
    "ssh_url", "url", "full_name", "path_with_namespace", "name",
)


def _normalize_git_url(url: str) -> str:
    """Reduce a git URL/identifier to a comparable ``host/owner/repo`` form."""
    url = url.strip().lower()
    if url.startswith("git@"):
        url = url[len("git@"):].replace(":", "/", 1)
    for prefix in ("https://", "http://", "git://", "ssh://"):
        if url.startswith(prefix):
            url = url[len(prefix):]
    if "@" in url.split("/", 1)[0]:
        # Strip userinfo (e.g. oauth2:token@host/...).
        url = url.split("@", 1)[1]
    if url.endswith(".git"):
        url = url[:-len(".git")]
    return url.strip("/")


def _verify_secret(
    secret: str,
    body: bytes,
    gh_sig: str | None,
    gl_token: str | None,
    generic: str | None,
) -> bool:
    """Verify a webhook against the shared secret using whichever scheme applies."""
    if gh_sig:
        expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, gh_sig)
    if gl_token:
        return hmac.compare_digest(secret, gl_token)
    if generic:
        return hmac.compare_digest(secret, generic)
    return False


def _extract_repo_identifiers(payload: dict) -> list[str]:
    """Collect candidate repo URLs/names from a push payload."""
    identifiers: list[str] = []
    for container in (payload.get("repository"), payload.get("project")):
        if isinstance(container, dict):
            for key in _REPO_URL_KEYS:
                value = container.get(key)
                if isinstance(value, str) and value:
                    identifiers.append(value)
    for key in ("repo", "repository_url", "url"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            identifiers.append(value)
    return identifiers


@router.post("/index")
async def webhook_index(
    request: Request,
    x_hub_signature_256: str | None = Header(None),
    x_gitlab_token: str | None = Header(None),
    x_webhook_secret: str | None = Header(None),
):
    """Queue incremental re-indexing for repos matching a push webhook payload."""
    secret = (get_settings().webhook_secret or "").strip()
    if not secret:
        raise HTTPException(status_code=503, detail="Webhooks are disabled (set WEBHOOK_SECRET)")

    body = await request.body()
    if not _verify_secret(secret, body, x_hub_signature_256, x_gitlab_token, x_webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid or missing webhook signature")

    try:
        payload = json.loads(body or b"{}")
    except (ValueError, TypeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    candidates = _extract_repo_identifiers(payload)
    if not candidates:
        return {"status": "ignored", "reason": "no repository identifier in payload", "count": 0}

    normalized_urls = {_normalize_git_url(c) for c in candidates}
    raw_names = set(candidates)

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT org_id, name, url FROM repos WHERE type IN ('github', 'gitlab')"
        )
        matches = [
            (r["org_id"], r["name"])
            for r in rows
            if (r["url"] and _normalize_git_url(r["url"]) in normalized_urls)
            or r["name"] in raw_names
        ]
        for org_id, name in matches:
            await conn.execute(
                "INSERT INTO index_requests (org_id, repo_name) VALUES ($1, $2)", org_id, name
            )

    if matches:
        logger.info("Webhook queued incremental index for %d repo(s): %s",
                    len(matches), ", ".join(m[1] for m in matches))

    # ── Auto-memory from commit messages ──────────────────────────────
    # Extract commits from the push payload and create memories for any
    # Conventional Commits messages so agents discover recent changes.
    auto_memories = 0
    try:
        commits = payload.get("commits") or []
        if isinstance(commits, list) and commits:
            from ....mcp.memory import _get_memory
            from ....config import get_forge_config
            mem = _get_memory()
            cfg = get_forge_config()
            uid = cfg.memory.user_id
            for commit in commits:
                if not isinstance(commit, dict):
                    continue
                msg = (commit.get("message") or "").strip()
                if not msg:
                    continue
                # Only auto-memorize Conventional Commits
                conv_prefixes = ("feat", "fix", "docs", "style", "refactor",
                                 "perf", "test", "build", "ci", "chore", "revert")
                first_word = msg.split(":")[0].split("(")[0].strip().lower()
                if first_word not in conv_prefixes:
                    continue
                # Truncate long messages
                short = msg[:300]
                repo_name = commit.get("repo", "")
                author = (commit.get("author") or {}).get("name", "")
                meta = {"source": "webhook", "type": "commit"}
                if repo_name:
                    meta["repo"] = repo_name
                if author:
                    meta["author"] = author
                mem.add(f"COMMIT [{repo_name}]: {short}", user_id=uid, metadata=meta)
                auto_memories += 1
        if auto_memories:
            logger.info("Webhook auto-memorized %d commit(s)", auto_memories)
    except Exception:
        # Auto-memory is best-effort; never fail the webhook for it.
        pass

    return {
        "status": "queued" if matches else "no_match",
        "repos": [m[1] for m in matches],
        "count": len(matches),
    }
