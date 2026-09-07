"""Read-only REST over the MCP tool-call audit trail (org admins and owners)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ... import tenancy
from ...db import get_pool
from ..deps import get_current_user_id

router = APIRouter(prefix="/organizations", tags=["tool-calls"])

MAX_LIMIT = 200
# Filtro principal per sottostringa, con i metacaratteri LIKE neutralizzati.
_LIKE_ESCAPE = "\\"
_PRINCIPAL_LIKE = r"principal ILIKE '%' || ${n} || '%' ESCAPE '\'"
WINDOW_HOURS = {"24h": 24, "7d": 168}

_COLUMNS = (
    "id, org_id, project_id, principal_kind, principal_id, principal, "
    "tool, permission, outcome, duration_ms, error, args_summary, created_at"
)


async def _require_admin(org_id: int, user_id: int) -> str:
    role = await tenancy.get_membership_role(org_id, user_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    if not tenancy.role_at_least(role, "admin"):
        raise HTTPException(status_code=403, detail="Requires 'admin' role or higher")
    return role


def _escape_like(value: str) -> str:
    """Neutralizza i metacaratteri LIKE digitati dall'utente."""
    escaped = value.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
    return escaped.replace("%", _LIKE_ESCAPE + "%").replace("_", _LIKE_ESCAPE + "_")


def _build_filters(
    org_id: int,
    tool: Optional[str],
    outcome: Optional[str],
    principal: Optional[str],
    project_id: Optional[int],
    since: Optional[datetime],
) -> tuple[str, list]:
    clauses = ["org_id = $1"]
    params: list = [org_id]
    for column, value in (
        ("tool", tool),
        ("outcome", outcome),
        ("principal", principal),
        ("project_id", project_id),
    ):
        if value is None or value == "":
            continue
        if column == "principal":
            # Ricerca per sottostringa: il principal e' un'etichetta leggibile.
            params.append(_escape_like(str(value)))
            clauses.append(_PRINCIPAL_LIKE.format(n=len(params)))
        else:
            params.append(value)
            clauses.append(f"{column} = ${len(params)}")
    if since is not None:
        params.append(since)
        clauses.append(f"created_at >= ${len(params)}")
    return "WHERE " + " AND ".join(clauses), params


def _serialize_call(row) -> dict:
    out = dict(row)
    created = out.get("created_at")
    if created is not None and hasattr(created, "isoformat"):
        out["created_at"] = created.isoformat()
    summary = out.get("args_summary")
    if isinstance(summary, str):
        try:
            out["args_summary"] = json.loads(summary)
        except ValueError:
            out["args_summary"] = None
    return out


@router.get("/{org_id}/tool-calls")
async def list_tool_calls(
    org_id: int,
    limit: int = 50,
    offset: int = 0,
    tool: Optional[str] = None,
    outcome: Optional[str] = None,
    principal: Optional[str] = None,
    project_id: Optional[int] = None,
    since: Optional[str] = None,
    user_id: int = Depends(get_current_user_id),
):
    """Recent MCP tool calls for the organization, newest first."""
    await _require_admin(org_id, user_id)

    parsed_since: Optional[datetime] = None
    if since:
        try:
            parsed_since = datetime.fromisoformat(since)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid 'since': expected ISO 8601")
        # Un timestamp senza offset e' inteso UTC, come created_at.
        if parsed_since.tzinfo is None:
            parsed_since = parsed_since.replace(tzinfo=timezone.utc)

    limit = max(1, min(int(limit), MAX_LIMIT))
    offset = max(0, int(offset))
    where, params = _build_filters(org_id, tool, outcome, principal, project_id, parsed_since)

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {_COLUMNS} FROM mcp_tool_calls {where} "
            f"ORDER BY created_at DESC, id DESC "
            f"LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}",
            *params,
            limit,
            offset,
        )
        total = await conn.fetchval(f"SELECT COUNT(*) FROM mcp_tool_calls {where}", *params)
    return {"calls": [_serialize_call(r) for r in rows], "total": int(total or 0)}


@router.get("/{org_id}/tool-calls/stats")
async def tool_call_stats(
    org_id: int,
    window: str = "24h",
    user_id: int = Depends(get_current_user_id),
):
    """Aggregates over the window: per tool, per principal, and totals."""
    await _require_admin(org_id, user_id)
    hours = WINDOW_HOURS.get(window)
    if hours is None:
        raise HTTPException(status_code=422, detail="Invalid 'window': use 24h or 7d")

    aggregates = (
        "COUNT(*)::int AS calls, "
        "COUNT(*) FILTER (WHERE outcome = 'error')::int AS errors, "
        "COUNT(*) FILTER (WHERE outcome = 'denied')::int AS denied, "
        "COUNT(*) FILTER (WHERE outcome = 'rate_limited')::int AS rate_limited, "
        "COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms), 0)::int AS p95_ms"
    )
    window_where = "WHERE org_id = $1 AND created_at >= NOW() - make_interval(hours => $2)"

    pool = await get_pool()
    async with pool.acquire() as conn:
        by_tool = await conn.fetch(
            f"SELECT tool, {aggregates} FROM mcp_tool_calls {window_where} "
            "GROUP BY tool ORDER BY calls DESC",
            org_id,
            hours,
        )
        by_principal = await conn.fetch(
            f"SELECT principal, {aggregates} FROM mcp_tool_calls {window_where} "
            "GROUP BY principal ORDER BY calls DESC",
            org_id,
            hours,
        )
        totals = await conn.fetchrow(
            "SELECT COUNT(*)::int AS calls, "
            "COUNT(*) FILTER (WHERE outcome = 'ok')::int AS ok, "
            "COUNT(*) FILTER (WHERE outcome = 'error')::int AS errors, "
            "COUNT(*) FILTER (WHERE outcome = 'denied')::int AS denied, "
            "COUNT(*) FILTER (WHERE outcome = 'rate_limited')::int AS rate_limited "
            f"FROM mcp_tool_calls {window_where}",
            org_id,
            hours,
        )

    totals_dict = (
        dict(totals)
        if totals
        else {"calls": 0, "ok": 0, "errors": 0, "denied": 0, "rate_limited": 0}
    )
    return {
        "by_tool": [dict(r) for r in by_tool],
        "by_principal": [dict(r) for r in by_principal],
        "totals": totals_dict,
        "total": totals_dict.get("calls", 0),
    }
