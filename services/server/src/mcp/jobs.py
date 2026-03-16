"""MCP tools for async job execution.

Solves the MCP timeout problem for slow HTTP calls (e.g. AI agents that take 30-90s).
Instead of blocking, the tool submits a job and returns a job_id immediately.
The agent then polls job_status() / job_result() until done.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Optional

import httpx

from .server import mcp
from ..db import get_pool

logger = logging.getLogger(__name__)


async def _execute_http_job(job_id: str, url: str, method: str, payload: dict, headers: dict) -> None:
    """Background task: run HTTP call and update job status in DB."""
    pool = await get_pool()

    async def _update(status: str, result: Any = None, error: str = None):
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE jobs
                SET status = $1, result = $2, error_message = $3, updated_at = NOW()
                WHERE id = $4
                """,
                status,
                json.dumps(result) if result is not None else None,
                error,
                job_id,
            )

    await _update("running")
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            if method.upper() == "GET":
                resp = await client.get(url, headers=headers)
            else:
                resp = await client.post(url, json=payload, headers=headers)

        try:
            result_data = resp.json()
        except Exception:
            result_data = {"text": resp.text, "status_code": resp.status_code}

        if resp.is_success:
            await _update("done", result_data)
        else:
            await _update("error", result_data, f"HTTP {resp.status_code}")
    except Exception as e:
        logger.error("Job %s failed: %s", job_id, e)
        await _update("error", error=str(e))


@mcp.tool()
async def job_submit(
    url: str,
    method: str = "POST",
    payload: Optional[dict] = None,
    headers: Optional[dict] = None,
) -> dict:
    """Submit a long-running HTTP request as an async background job.

    Returns immediately with a job_id. Use job_status() to poll for completion
    and job_result() to retrieve the result.

    Ideal for slow AI agents, data pipelines, or any HTTP endpoint that takes
    more than a few seconds to respond (which would otherwise cause MCP timeouts).

    Args:
        url: The HTTP URL to call
        method: HTTP method: "GET" or "POST" (default "POST")
        payload: Request body as a dict (for POST requests)
        headers: Optional HTTP headers (e.g. {"Authorization": "Bearer token"})

    Returns:
        dict with job_id to use with job_status() and job_result()

    Example:
        job = job_submit(url="http://askmechat:8000/api/v1/query",
                         payload={"question": "How many users today?"})
        # later:
        status = job_status(job["job_id"])
        result = job_result(job["job_id"])
    """
    job_id = str(uuid.uuid4())
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO jobs (id, tool, params, status)
            VALUES ($1, 'http', $2, 'pending')
            """,
            job_id,
            json.dumps({"url": url, "method": method, "payload": payload or {}, "headers": headers or {}}),
        )

    asyncio.create_task(
        _execute_http_job(job_id, url, method, payload or {}, headers or {})
    )

    return {
        "status": "ok",
        "job_id": job_id,
        "message": f"Job submitted. Poll with job_status('{job_id}')",
    }


@mcp.tool()
async def job_status(job_id: str) -> dict:
    """Check the status of a submitted async job.

    Args:
        job_id: The job ID returned by job_submit()

    Returns:
        dict with status: "pending" | "running" | "done" | "error"
        When done or error, also includes the result or error_message.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, status, error_message, created_at, updated_at FROM jobs WHERE id = $1",
            job_id,
        )

    if not row:
        return {"status": "error", "error": f"Job not found: {job_id}"}

    return {
        "status": "ok",
        "job_id": job_id,
        "job_status": row["status"],
        "error_message": row["error_message"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
        "hint": "Call job_result() to get the full result when job_status is 'done'",
    }


@mcp.tool()
async def job_result(job_id: str) -> dict:
    """Retrieve the result of a completed async job.

    Args:
        job_id: The job ID returned by job_submit()

    Returns:
        dict with the job result, or an error if the job is not yet done or failed.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, status, result, error_message FROM jobs WHERE id = $1",
            job_id,
        )

    if not row:
        return {"status": "error", "error": f"Job not found: {job_id}"}

    if row["status"] == "pending" or row["status"] == "running":
        return {
            "status": "not_ready",
            "job_status": row["status"],
            "message": f"Job is still {row['status']}. Try again in a few seconds.",
        }

    if row["status"] == "error":
        return {
            "status": "error",
            "job_id": job_id,
            "error": row["error_message"],
        }

    raw_result = row["result"]
    result = json.loads(raw_result) if isinstance(raw_result, str) else raw_result
    return {
        "status": "ok",
        "job_id": job_id,
        "result": result,
    }
