"""MCP tools for CI/CD context (GitHub Actions / GitLab CI)."""
from __future__ import annotations

import logging
from typing import Optional

from .server import mcp

logger = logging.getLogger(__name__)


@mcp.tool()
async def ci_runs(repo: str, limit: int = 10) -> dict:
    """List recent CI runs (GitHub Actions workflow runs / GitLab CI pipelines) for a repository.

    The repository must be a configured github/gitlab repo (see repo_list).
    Runs are fetched live from the provider using the configured token.

    Args:
        repo: Repository name (from repo_list)
        limit: Maximum runs to return (default 10, max 30)

    Returns:
        dict with runs (id, name, status, conclusion, branch, commit, url, created_at)
    """
    from ..ci import service
    from ..ci.service import CiError
    from .context import resolve_org_id

    org_id = await resolve_org_id()
    try:
        runs = await service.recent_runs(org_id, repo, limit=limit)
    except CiError as e:
        return {"status": "error", "error": str(e)}
    except Exception as e:  # noqa: BLE001
        logger.error("ci_runs failed: %s", e)
        return {"status": "error", "error": str(e)}
    return {"status": "ok", "repo": repo, "runs": runs, "count": len(runs)}


@mcp.tool()
async def ci_failure(repo: str, run_id: Optional[int] = None, max_log_chars: int = 8000) -> dict:
    """Get why a CI run failed: the failed jobs/steps and the tail of their error logs.

    Without run_id, inspects the most recent failed run. Use this to answer
    "why is the pipeline red" — the log tail usually contains the actual error.

    Args:
        repo: Repository name (from repo_list)
        run_id: Specific run/pipeline id (from ci_runs); defaults to the latest failure
        max_log_chars: Maximum characters of log tail per failed job (default 8000)

    Returns:
        dict with the run info and failed_jobs (name, failed_steps, log_tail, url)
    """
    from ..ci import service
    from ..ci.service import CiError
    from .context import resolve_org_id

    org_id = await resolve_org_id()
    try:
        detail = await service.failure_detail(org_id, repo, run_id=run_id, max_log_chars=max_log_chars)
    except CiError as e:
        return {"status": "error", "error": str(e)}
    except Exception as e:  # noqa: BLE001
        logger.error("ci_failure failed: %s", e)
        return {"status": "error", "error": str(e)}
    return {"status": "ok", "repo": repo, **detail}
