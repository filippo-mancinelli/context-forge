"""REST API for the approval queue of agent-proposed writes (org admin/owner)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ... import write_requests as service
from ..deps import ActiveOrg, get_current_user_id, require_role

router = APIRouter(prefix="/write-requests", tags=["write-requests"])


class DecisionRequest(BaseModel):
    note: str = ""


@router.get("")
async def list_requests(
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    org: ActiveOrg = Depends(require_role("admin")),
):
    if status is not None and status not in service.STATUSES:
        raise HTTPException(status_code=422, detail=f"Unknown status '{status}'")
    # Admins and owners already see every project of the organization.
    return await service.list_for_org(org.org_id, status=status, limit=limit, offset=offset)


@router.get("/{request_id}")
async def get_request(
    request_id: int, org: ActiveOrg = Depends(require_role("admin"))
):
    record = await service.get(request_id, org_id=org.org_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Write request not found")
    return {"request": record}


def _decision_error(exc: Exception) -> HTTPException:
    if isinstance(exc, service.WriteRequestForbidden):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, service.WriteRequestNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=409, detail=str(exc))


@router.post("/{request_id}/approve")
async def approve_request(
    request_id: int,
    req: DecisionRequest,
    org: ActiveOrg = Depends(require_role("admin")),
    user_id: int = Depends(get_current_user_id),
):
    # Scoped to the active org first so a cross-org id 404s instead of leaking.
    if await service.get(request_id, org_id=org.org_id) is None:
        raise HTTPException(status_code=404, detail="Write request not found")
    try:
        record = await service.approve(request_id, user_id, req.note)
    except service.WriteRequestError as e:
        raise _decision_error(e) from e
    return {"status": "ok", "request": record}


@router.post("/{request_id}/reject")
async def reject_request(
    request_id: int,
    req: DecisionRequest,
    org: ActiveOrg = Depends(require_role("admin")),
    user_id: int = Depends(get_current_user_id),
):
    if await service.get(request_id, org_id=org.org_id) is None:
        raise HTTPException(status_code=404, detail="Write request not found")
    try:
        record = await service.reject(request_id, user_id, req.note)
    except service.WriteRequestError as e:
        raise _decision_error(e) from e
    return {"status": "ok", "request": record}
