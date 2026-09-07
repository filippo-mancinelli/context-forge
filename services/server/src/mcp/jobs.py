"""MCP tools for async job execution.

Solves the MCP timeout problem for slow HTTP calls (e.g. AI agents that take 30-90s).
Instead of blocking, the tool submits a job and returns a job_id immediately.
The agent then polls job_status() / job_result() until done.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Optional

import httpx

from .server import mcp
from .permissions import requires_permission
from ..db import get_pool

logger = logging.getLogger(__name__)

JOB_STATUSES = ("pending", "running", "done", "error", "dead")
BASE_BACKOFF_SECONDS = 5
MAX_BACKOFF_SECONDS = 300
RETRYABLE_STATUS_CODES = frozenset({408, 429})


def next_backoff_seconds(attempts: int) -> int:
    """Wait before the next attempt: 5s * 4^(attempts-1), capped at five minutes."""
    exponent = max(attempts - 1, 0)
    return min(BASE_BACKOFF_SECONDS * (4 ** exponent), MAX_BACKOFF_SECONDS)


def classify_http_status(status_code: int) -> str:
    """Map an HTTP response status to 'done', 'retry' or 'error'."""
    if 200 <= status_code < 300:
        return "done"
    if status_code in RETRYABLE_STATUS_CODES or status_code >= 500:
        return "retry"
    return "error"


def classify_exception(exc: BaseException) -> str:
    """Timeouts and connection failures are retryable; anything else is permanent."""
    return "retry" if isinstance(exc, httpx.TransportError) else "error"


async def finalize_job(
    job_id: str,
    outcome: str,
    attempts: int,
    max_attempts: int,
    result: Any = None,
    error: Optional[str] = None,
) -> str:
    """Write one attempt's outcome. Returns the resulting job status."""
    if outcome == "done":
        status, delay = "done", 0
    elif outcome == "retry" and attempts < max_attempts:
        status, delay = "pending", next_backoff_seconds(attempts)
    elif outcome == "retry":
        status, delay = "dead", 0
    else:
        status, delay = "error", 0

    terminal_error = error if status in ("error", "dead") else None
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE jobs
            SET status = $1,
                result = COALESCE($2::jsonb, result),
                last_error = $3,
                error_message = COALESCE($4, error_message),
                next_attempt_at = NOW() + make_interval(secs => $5),
                updated_at = NOW()
            WHERE id = $6
            """,
            status,
            json.dumps(result) if result is not None else None,
            error,
            terminal_error,
            float(delay),
            job_id,
        )
    return status


async def run_claimed_job(job: dict) -> None:
    """Execute one claimed job row and persist its outcome."""
    job_id = str(job["id"])
    params = job["params"]
    if isinstance(params, str):
        params = json.loads(params)
    params = params or {}

    url = params.get("url", "")
    method = (params.get("method") or "POST").upper()
    payload = params.get("payload") or {}
    headers = params.get("headers") or {}

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            if method == "GET":
                resp = await client.get(url, headers=headers)
            else:
                resp = await client.post(url, json=payload, headers=headers)
        try:
            result_data = resp.json()
        except Exception:
            result_data = {"text": resp.text, "status_code": resp.status_code}
        outcome = classify_http_status(resp.status_code)
        error = None if outcome == "done" else f"HTTP {resp.status_code}"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Job %s attempt %s failed: %s", job_id, job["attempts"], exc)
        result_data = None
        outcome = classify_exception(exc)
        error = str(exc) or exc.__class__.__name__

    await finalize_job(
        job_id, outcome, int(job["attempts"]), int(job["max_attempts"]), result_data, error
    )


@mcp.tool()
@requires_permission("jobs")
async def job_submit(
    url: str,
    method: str = "POST",
    payload: Optional[dict] = None,
    headers: Optional[dict] = None,
    max_attempts: int = 3,
) -> dict:
    """Submit a long-running HTTP request as an async background job.

    Returns immediately with a job_id. Use job_status() to poll for completion
    and job_result() to retrieve the result.

    Ideal for slow AI agents, data pipelines, or any HTTP endpoint that takes
    more than a few seconds to respond (which would otherwise cause MCP timeouts).

    The job is executed by the server's scheduler, so it survives a restart.
    Transient failures (5xx, 408, 429, timeouts, connection errors) are retried
    with exponential backoff; after max_attempts the job becomes "dead".

    Args:
        url: The HTTP URL to call
        method: HTTP method: "GET" or "POST" (default "POST")
        payload: Request body as a dict (for POST requests)
        headers: Optional HTTP headers (e.g. {"Authorization": "Bearer token"})
        max_attempts: How many times to try before dead-lettering (1-10, default 3)

    Returns:
        dict with job_id to use with job_status() and job_result()

    Example:
        job = job_submit(url="http://my-service:8005/api/v1/query",
                         payload={"question": "How many users today?"})
        # later:
        status = job_status(job["job_id"])
        result = job_result(job["job_id"])
    """
    from .context import resolve_org_id, require_project_id

    if not 1 <= max_attempts <= 10:
        return {"status": "error", "error": "max_attempts must be between 1 and 10"}

    job_id = str(uuid.uuid4())
    org_id = await resolve_org_id()
    project_id = await require_project_id()
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO jobs (id, tool, params, status, org_id, project_id,
                              max_attempts, next_attempt_at)
            VALUES ($1, 'http', $2, 'pending', $3, $4, $5, NOW())
            """,
            job_id,
            json.dumps({"url": url, "method": method, "payload": payload or {}, "headers": headers or {}}),
            org_id,
            project_id,
            max_attempts,
        )

    return {
        "status": "ok",
        "job_id": job_id,
        "message": f"Job submitted. Poll with job_status('{job_id}')",
    }


@mcp.tool()
@requires_permission("jobs")
async def job_status(job_id: str) -> dict:
    """Check the status of a submitted async job.

    Args:
        job_id: The job ID returned by job_submit()

    Returns:
        dict with status: "pending" | "running" | "done" | "error" | "dead",
        the attempt counters, and when the next attempt is due.
    """
    from .context import require_project_id

    project_id = await require_project_id()
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, status, error_message, attempts, max_attempts, "
            "next_attempt_at, last_error, created_at, updated_at "
            "FROM jobs WHERE id = $1 AND project_id = $2",
            job_id, project_id,
        )

    if not row:
        return {"status": "error", "error": f"Job not found: {job_id}"}

    next_attempt_at = row["next_attempt_at"]
    return {
        "status": "ok",
        "job_id": job_id,
        "job_status": row["status"],
        "error_message": row["error_message"],
        "last_error": row["last_error"],
        "attempts": row["attempts"],
        "max_attempts": row["max_attempts"],
        "next_attempt_at": next_attempt_at.isoformat() if next_attempt_at else None,
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
        "hint": "Call job_result() to get the full result when job_status is 'done'",
    }


@mcp.tool()
@requires_permission("jobs")
async def job_result(job_id: str) -> dict:
    """Retrieve the result of a completed async job.

    Args:
        job_id: The job ID returned by job_submit()

    Returns:
        dict with the job result, or an error if the job is not yet done, failed,
        or exhausted its retries ("dead").
    """
    from .context import require_project_id

    project_id = await require_project_id()
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, status, result, error_message, last_error, attempts, max_attempts "
            "FROM jobs WHERE id = $1 AND project_id = $2",
            job_id, project_id,
        )

    if not row:
        return {"status": "error", "error": f"Job not found: {job_id}"}

    if row["status"] in ("pending", "running"):
        return {
            "status": "not_ready",
            "job_status": row["status"],
            "attempts": row["attempts"],
            "max_attempts": row["max_attempts"],
            "message": f"Job is still {row['status']}. Try again in a few seconds.",
        }

    if row["status"] in ("error", "dead"):
        return {
            "status": "error",
            "job_id": job_id,
            "job_status": row["status"],
            "error": row["error_message"] or row["last_error"],
            "attempts": row["attempts"],
            "max_attempts": row["max_attempts"],
        }

    raw_result = row["result"]
    result = json.loads(raw_result) if isinstance(raw_result, str) else raw_result
    return {
        "status": "ok",
        "job_id": job_id,
        "result": result,
    }
