"""REST API routes for API contracts (OpenAPI specs / GraphQL schemas)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...contracts import service
from ...contracts.service import CONTRACT_TYPES, ContractNotFoundError
from ..deps import ActiveOrg, get_active_org, require_role

router = APIRouter(prefix="/contracts", tags=["contracts"])


class ContractCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    type: str  # 'openapi' | 'graphql'
    source_url: Optional[str] = None
    raw_spec: Optional[str] = None  # pasted spec / introspection JSON
    description: Optional[str] = None


class ContractRefreshRequest(BaseModel):
    raw_spec: Optional[str] = None  # replace pasted content on refresh


@router.get("")
async def list_contracts(org: ActiveOrg = Depends(get_active_org)):
    contracts = await service.list_contracts(org.org_id)
    return {"contracts": contracts, "types": list(CONTRACT_TYPES)}


@router.post("")
async def create_contract(req: ContractCreateRequest, org: ActiveOrg = Depends(require_role("member"))):
    if not req.source_url and not req.raw_spec:
        raise HTTPException(status_code=400, detail="Provide a source URL or paste the spec content")
    try:
        contract = await service.create_contract(org.org_id, req.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        if "api_contracts_org_id_name_key" in str(e):
            raise HTTPException(status_code=400, detail=f"Contract '{req.name}' already exists")
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "ok", "contract": contract}


# NOTE: declared before the /{contract_id} routes so the literal path wins.
@router.get("/search")
async def search_endpoints(
    q: str,
    limit: int = 100,
    org: ActiveOrg = Depends(get_active_org),
):
    endpoints = await service.list_endpoints(org.org_id, search=q, limit=min(limit, 500))
    return {"endpoints": endpoints, "count": len(endpoints)}


@router.post("/{contract_id}/refresh")
async def refresh_contract(
    contract_id: int,
    req: Optional[ContractRefreshRequest] = None,
    org: ActiveOrg = Depends(require_role("member")),
):
    try:
        contract = await service.refresh_contract(
            org.org_id, contract_id, raw_spec=(req.raw_spec if req else None)
        )
    except ContractNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"status": "ok", "contract": contract}


@router.delete("/{contract_id}")
async def delete_contract(contract_id: int, org: ActiveOrg = Depends(require_role("member"))):
    try:
        await service.delete_contract(org.org_id, contract_id)
    except ContractNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"status": "ok"}


@router.get("/{contract_id}/endpoints")
async def list_endpoints(
    contract_id: int,
    tag: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 500,
    org: ActiveOrg = Depends(get_active_org),
):
    try:
        endpoints = await service.list_endpoints(
            org.org_id, contract_ref=contract_id, tag=tag, search=search, limit=min(limit, 1000)
        )
    except ContractNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"endpoints": endpoints, "count": len(endpoints)}


@router.get("/{contract_id}/endpoint")
async def get_endpoint(
    contract_id: int,
    method: str,
    path: str,
    org: ActiveOrg = Depends(get_active_org),
):
    try:
        return await service.get_endpoint(org.org_id, contract_id, method, path)
    except ContractNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
