"""Write requests: agent proposals for SQL and file writes, approved by a human.

A caller without the write permission does not execute: the tool stores the
statement (or file content) here with a preview, and an organization admin
approves it, which is when the existing executors actually run.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from . import tenancy
from .db import get_pool
from .mcp.audit import scrub_text

logger = logging.getLogger(__name__)

STATUSES = ("pending", "approved", "rejected", "executed", "failed", "expired")
KIND_PERMISSION = {"db_execute": "db-write", "ssh_write_file": "ssh-write"}

_FIELDS = (
    "id, org_id, project_id, kind, status, target, payload, preview, reason, "
    "requested_by_kind, requested_by_id, requested_by, decided_by, decided_at, "
    "decision_note, result, error, created_at, expires_at"
)

MAX_LIST_LIMIT = 200


class WriteRequestError(Exception):
    pass


class WriteRequestNotFound(WriteRequestError):
    pass


class WriteRequestForbidden(WriteRequestError):
    pass


class WriteRequestState(WriteRequestError):
    pass


def _to_dict(row: Any) -> dict[str, Any]:
    d = dict(row)
    for key in ("payload", "preview", "result"):
        if isinstance(d.get(key), str):
            d[key] = json.loads(d[key] or "null")
    for key in ("created_at", "decided_at", "expires_at"):
        if d.get(key) is not None and hasattr(d[key], "isoformat"):
            d[key] = d[key].isoformat()
    return d


async def create(
    *,
    org_id: int,
    project_id: int,
    kind: str,
    target: str,
    payload: dict[str, Any],
    preview: dict[str, Any],
    reason: str = "",
    requested_by_kind: str,
    requested_by_id: Optional[int],
    requested_by: str,
) -> dict[str, Any]:
    """Store a pending proposal and return the created record."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            INSERT INTO write_requests
                (org_id, project_id, kind, target, payload, preview, reason,
                 requested_by_kind, requested_by_id, requested_by)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, $8, $9, $10)
            RETURNING {_FIELDS}
            """,
            org_id,
            project_id,
            kind,
            target,
            json.dumps(payload),
            json.dumps(preview),
            reason or None,
            requested_by_kind,
            requested_by_id,
            requested_by,
        )
    return _to_dict(row)


async def get(
    request_id: int, org_id: Optional[int] = None, project_id: Optional[int] = None
) -> Optional[dict[str, Any]]:
    """Fetch one request; returns None when it is outside the given org/project."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_FIELDS} FROM write_requests WHERE id=$1", request_id
        )
    if row is None:
        return None
    record = _to_dict(row)
    if org_id is not None and record["org_id"] != org_id:
        return None
    if project_id is not None and record["project_id"] != project_id:
        return None
    return record


async def list_for_org(
    org_id: int, status: Optional[str] = None, limit: int = 50, offset: int = 0
) -> dict[str, Any]:
    limit = max(1, min(int(limit), MAX_LIST_LIMIT))
    offset = max(0, int(offset))
    pool = await get_pool()
    async with pool.acquire() as conn:
        if status:
            rows = await conn.fetch(
                f"SELECT {_FIELDS} FROM write_requests WHERE org_id=$1 AND status=$2 "
                f"ORDER BY created_at DESC LIMIT $3 OFFSET $4",
                org_id, status, limit, offset,
            )
            total = await conn.fetchval(
                "SELECT count(*) FROM write_requests WHERE org_id=$1 AND status=$2",
                org_id, status,
            )
        else:
            rows = await conn.fetch(
                f"SELECT {_FIELDS} FROM write_requests WHERE org_id=$1 "
                f"ORDER BY created_at DESC LIMIT $2 OFFSET $3",
                org_id, limit, offset,
            )
            total = await conn.fetchval(
                "SELECT count(*) FROM write_requests WHERE org_id=$1", org_id
            )
        pending = await conn.fetchval(
            "SELECT count(*) FROM write_requests WHERE org_id=$1 AND status='pending'",
            org_id,
        )
    return {
        "requests": [_to_dict(r) for r in rows],
        "total": int(total or 0),
        "pending": int(pending or 0),
    }


async def expire_pending() -> int:
    """Mark overdue pending requests as expired; returns how many."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE write_requests SET status='expired' "
            "WHERE status='pending' AND expires_at < NOW()"
        )
    try:
        return int(str(result).split()[-1])
    except (ValueError, IndexError):
        return 0


async def _require_approver(record: dict[str, Any], user_id: int) -> None:
    """The approver must hold the write permission the request needs."""
    role = await tenancy.get_membership_role(record["org_id"], user_id)
    if role is None:
        raise WriteRequestForbidden("Not a member of this organization")
    if role == "owner":
        return
    permission = KIND_PERMISSION.get(record["kind"], "")
    perms = await tenancy.resolve_role_permissions(record["org_id"], role)
    if "*" not in perms and permission not in perms:
        raise WriteRequestForbidden(
            f"Approving a {record['kind']} request requires the '{permission}' permission"
        )


def _scope_text(database: Optional[str], schema: Optional[str]) -> str:
    return ".".join(part for part in (database, schema) if part) or "(connection default)"


async def _pinned_scope(record: dict[str, Any]) -> dict[str, Any]:
    """The scope the statement was proposed and previewed on, unchanged since.

    Raises WriteRequestError when the request predates scopes, or the scope
    was removed, renamed or re-pointed: the write never runs elsewhere.
    """
    from .datasources import service

    payload, target = record["payload"], record["target"]
    scope_id = payload.get("scope_id")
    if scope_id is None:
        raise WriteRequestError(
            f"This request on '{target}' was proposed before database scopes existed: "
            "propose the change again."
        )
    try:
        scope = await service.get_scope_by_id(record["org_id"], record["project_id"], scope_id)
    except service.ConnectionNotFoundError:
        raise WriteRequestError(
            f"The data source '{target}' is no longer in this project: propose the change again."
        ) from None
    if scope["alias"] != target:
        raise WriteRequestError(
            f"The data source '{target}' was renamed to '{scope['alias']}' after the proposal: "
            "propose the change again."
        )
    proposed = (payload.get("database"), payload.get("schema"))
    current = service.effective_scope(scope)
    if current != proposed:
        raise WriteRequestError(
            f"The scope of '{target}' changed from '{_scope_text(*proposed)}' to "
            f"'{_scope_text(*current)}' after the proposal: propose the change again."
        )
    return scope


async def _run_db_execute(record: dict[str, Any]) -> dict[str, Any]:
    from .datasources import service

    scope = await _pinned_scope(record)
    payload = record["payload"]
    return await service.run_write(
        record["org_id"], record["project_id"], scope["scope_id"],
        payload["sql"], source="approval", by_scope_id=True,
        expected_scope=(record["target"], payload.get("database"), payload.get("schema")),
    )


async def _pinned_folder(record: dict[str, Any]) -> dict[str, Any]:
    """The SSH folder the file was proposed and previewed on, unchanged since.

    Loaded by id within the project's selection, never by name. Raises
    WriteRequestError when the request predates pinning, or the folder was
    removed, deselected, renamed or moved to another machine or root.
    """
    from .ssh_sources import service as ssh_service

    payload = record["payload"]
    name = payload.get("source")
    source_id = payload.get("ssh_source_id")
    if source_id is None:
        raise WriteRequestError(
            f"This request on '{record['target']}' was proposed before approvals were pinned "
            "to the folder: propose the change again."
        )
    folder = await ssh_service.get_source(
        record["org_id"], record["project_id"], int(source_id), include_secret=True
    )
    if folder is None:
        raise WriteRequestError(
            f"The SSH folder '{name}' is no longer in this project: propose the change again."
        )
    if folder["name"] != name:
        raise WriteRequestError(
            f"The SSH folder '{name}' was renamed to '{folder['name']}' after the proposal: "
            "propose the change again."
        )
    if folder["machine_id"] != payload.get("machine_id") or folder["root_path"] != payload.get("root_path"):
        raise WriteRequestError(
            f"The SSH folder '{name}' was moved from '{payload.get('root_path')}' on machine "
            f"#{payload.get('machine_id')} to '{folder['root_path']}' on machine "
            f"#{folder['machine_id']} after the proposal: propose the change again."
        )
    return folder


async def _run_ssh_write(record: dict[str, Any]) -> dict[str, Any]:
    from .ssh_sources import client
    from .ssh_sources import service as ssh_service

    payload = record["payload"]
    source = await _pinned_folder(record)
    return await asyncio.to_thread(
        client.write_file, ssh_service.decrypted_conn(source),
        payload["path"], payload["content"],
    )


async def _execute(record: dict[str, Any]) -> dict[str, Any]:
    if record["kind"] == "db_execute":
        return await _run_db_execute(record)
    if record["kind"] == "ssh_write_file":
        return await _run_ssh_write(record)
    raise WriteRequestError(f"Unknown write request kind '{record['kind']}'")


async def _finish(
    request_id: int, status: str, result: Optional[dict] = None, error: Optional[str] = None
) -> dict[str, Any]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE write_requests SET status=$2, result=$3::jsonb, error=$4 "
            f"WHERE id=$1 RETURNING {_FIELDS}",
            request_id, status,
            json.dumps(result) if result is not None else None,
            error,
        )
    return _to_dict(row)


async def approve(request_id: int, user_id: int, note: str = "") -> dict[str, Any]:
    """Approve and execute a pending request under the approver's identity."""
    record = await get(request_id)
    if record is None:
        raise WriteRequestNotFound(f"Write request {request_id} not found")
    await _require_approver(record, user_id)
    if record["status"] != "pending":
        raise WriteRequestState(
            f"Write request {request_id} is '{record['status']}', not pending"
        )

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE write_requests SET status='approved', decided_by=$2, "
            f"decided_at=NOW(), decision_note=$3 "
            f"WHERE id=$1 AND status='pending' AND expires_at > NOW() "
            f"RETURNING {_FIELDS}",
            request_id, user_id, note or None,
        )
    if row is None:
        raise WriteRequestState(
            f"Write request {request_id} is no longer pending or has expired"
        )
    approved = _to_dict(row)

    try:
        result = await _execute(approved)
    except Exception as e:  # noqa: BLE001 - the failure belongs on the request
        logger.warning("Write request %s failed: %s", request_id, e)
        return await _finish(request_id, "failed", error=scrub_text(str(e)))
    return await _finish(request_id, "executed", result=result)


async def reject(request_id: int, user_id: int, note: str = "") -> dict[str, Any]:
    record = await get(request_id)
    if record is None:
        raise WriteRequestNotFound(f"Write request {request_id} not found")
    await _require_approver(record, user_id)
    if record["status"] != "pending":
        raise WriteRequestState(
            f"Write request {request_id} is '{record['status']}', not pending"
        )
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE write_requests SET status='rejected', decided_by=$2, "
            f"decided_at=NOW(), decision_note=$3 "
            f"WHERE id=$1 AND status='pending' RETURNING {_FIELDS}",
            request_id, user_id, note or None,
        )
    if row is None:
        raise WriteRequestState(f"Write request {request_id} is no longer pending")
    return _to_dict(row)
