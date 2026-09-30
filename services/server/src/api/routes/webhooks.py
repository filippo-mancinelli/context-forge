"""Webhook endpoint for push-triggered incremental re-indexing and auto-memory.

A git host (GitHub, GitLab, or a custom caller) POSTs here on push; the pushed
repository is recognized by its normalized URL among the catalog repositories
and one index request is queued for each match. When the payload carries a
usable ``ref`` (``refs/heads/<branch>``, as GitHub and GitLab both send), only
catalog repositories on that branch match, so a push to one branch does not
reindex or auto-memorize a different branch of the same URL; without such a
``ref`` matching stays URL-only. Actual indexing runs incrementally in the
scheduler loop, re-processing only the files changed since the last indexed
commit.

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
    ref = payload.get("ref")
    pushed_branch = (
        ref[len("refs/heads/"):].strip()
        if isinstance(ref, str) and ref.startswith("refs/heads/")
        else None
    ) or None

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, org_id, name, url, branch FROM repos WHERE type IN ('github', 'gitlab') AND url IS NOT NULL"
        )
        # Il riconoscimento è per URL: un nome uguale può appartenere a un altro
        # repository. Con un ref utilizzabile si confronta anche il branch, così
        # un push su un branch non tocca il repository di un altro branch dello
        # stesso URL.
        matches = [
            (r["org_id"], r["id"], r["name"])
            for r in rows
            if _normalize_git_url(r["url"]) in normalized_urls
            and (pushed_branch is None or (r["branch"] or "main") == pushed_branch)
        ]
        for org_id, repo_id, _ in matches:
            await conn.execute(
                "INSERT INTO index_requests (org_id, repo_id) VALUES ($1, $2)", org_id, repo_id
            )

    if matches:
        logger.info("Webhook queued incremental index for %d repo(s): %s",
                    len(matches), ", ".join(m[2] for m in matches))

    # ── Auto-memory from commit messages ──────────────────────────────
    # Conventional Commits messages become memories in the namespace of every
    # project that selected the pushed repository: memory search reads a single
    # namespace, so each project needs its own copy. A match without selecting
    # projects produces no memory; there is no fallback to a shared namespace.
    # A project that selected more than one matched repository (e.g. two
    # branches of the same URL before a ref narrowed the match) still gets one
    # memory per commit: namespaces are deduped before writing.
    auto_memories = 0
    try:
        commits = payload.get("commits") or []
        if isinstance(commits, list) and commits and matches:
            from ...mcp.memory import _get_memory

            async with pool.acquire() as conn:
                ns_rows = await conn.fetch(
                    "SELECT pr.repo_id, p.memory_namespace FROM project_repos pr "
                    "JOIN projects p ON p.id = pr.project_id WHERE pr.repo_id = ANY($1::bigint[])",
                    [repo_id for _, repo_id, _ in matches],
                )
            namespaces: dict[int, list[str]] = {}
            for r in ns_rows:
                namespaces.setdefault(r["repo_id"], []).append(r["memory_namespace"])

            # Only auto-memorize Conventional Commits
            conv_prefixes = ("feat", "fix", "docs", "style", "refactor",
                             "perf", "test", "build", "ci", "chore", "revert")
            parsed_commits = []
            for commit in commits:
                if not isinstance(commit, dict):
                    continue
                msg = (commit.get("message") or "").strip()
                if not msg:
                    continue
                first_word = msg.split(":")[0].split("(")[0].strip().lower()
                if first_word not in conv_prefixes:
                    continue
                # Truncate long messages
                short = msg[:300]
                author = (commit.get("author") or {}).get("name", "")
                parsed_commits.append((short, author))

            targets_by_ns: dict[str, tuple[int, str]] = {}
            for org_id, repo_id, repo_name in matches:
                for ns in namespaces.get(repo_id) or []:
                    targets_by_ns.setdefault(ns, (org_id, repo_name))

            if parsed_commits:
                for ns, (org_id, repo_name) in targets_by_ns.items():
                    mem = await _get_memory(org_id)
                    for short, author in parsed_commits:
                        meta = {"source": "webhook", "type": "commit", "repo": repo_name}
                        if author:
                            meta["author"] = author
                        mem.add(f"COMMIT [{repo_name}]: {short}", user_id=ns, metadata=meta)
                        auto_memories += 1
        if auto_memories:
            logger.info("Webhook auto-memorized %d commit(s)", auto_memories)
    except Exception:
        # Auto-memory is best-effort; never fail the webhook for it.
        pass

    return {
        "status": "queued" if matches else "no_match",
        "repos": [m[2] for m in matches],
        "count": len(matches),
    }
