"""MCP surface of the write-approval flow: polling and the shared proposal helpers."""
from __future__ import annotations

import logging
from typing import Any, Optional

from .server import mcp
from .permissions import requires_permission
from .context import get_current_principal

logger = logging.getLogger(__name__)


def current_requester() -> tuple[str, Optional[int], str]:
    """(kind, id, label) of the MCP caller, stored on the write request."""
    principal = get_current_principal()
    if principal is None:
        return ("anonymous", None, "anonymous")
    return (principal.kind, principal.id, principal.label)


def pending_response(request_id: int, preview: dict[str, Any]) -> dict[str, Any]:
    """The reply a write tool returns instead of executing."""
    return {
        "status": "pending_approval",
        "request_id": request_id,
        "preview": preview,
        "message": (
            "An organization admin must approve this change. "
            f"Poll with write_request_status({request_id})."
        ),
    }


@mcp.tool()
@requires_permission("context-read")
async def write_request_status(request_id: int) -> dict:
    """Check a write request you proposed with db_execute or ssh_write_file.

    A write proposed without the write permission is not executed until an
    organization admin approves it. This returns where it stands and, once
    executed, what it did.

    Args:
        request_id: the id returned by db_execute / ssh_write_file.

    Returns:
        dict with `state` (pending, approved, rejected, executed, failed,
        expired), `decision_note`, `result` when executed, `error` when failed.
    """
    from .. import write_requests as service
    from .context import require_project_id, resolve_org_id

    org_id = await resolve_org_id()
    project_id = await require_project_id()
    record = await service.get(request_id, org_id=org_id, project_id=project_id)
    if record is None:
        return {"status": "error", "error": f"Write request {request_id} not found"}
    return {
        "status": "ok",
        "request_id": record["id"],
        "kind": record["kind"],
        "target": record["target"],
        "state": record["status"],
        "decision_note": record["decision_note"],
        "result": record["result"],
        "error": record["error"],
        "created_at": record["created_at"],
        "expires_at": record["expires_at"],
    }
