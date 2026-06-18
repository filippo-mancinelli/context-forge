"""MCP API key management routes (scoped to the active organization)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from ... import tenancy
from ..deps import ActiveOrg, get_active_org, get_current_user_id, require_role
from ..security import (
    create_mcp_api_key,
    get_mcp_api_key,
    list_mcp_api_keys,
    revoke_mcp_api_key,
    validate_mcp_api_key,
)

router = APIRouter(prefix="/mcp/keys", tags=["mcp-keys"])


class CreateKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    scope: str = Field(default="read,write", pattern=r"^(read|write|admin)(,(read|write|admin))*$")
    expires_days: int | None = Field(default=None, ge=1, le=365)


class CreateKeyResponse(BaseModel):
    key: str
    id: int
    name: str
    scope: str
    expires_at: str | None


@router.post("", response_model=CreateKeyResponse)
async def create_key(
    req: CreateKeyRequest,
    user_id: int = Depends(get_current_user_id),
    org: ActiveOrg = Depends(require_role("member")),
):
    """Create a new MCP API key for the active organization. Shown only once."""
    raw_key = await create_mcp_api_key(
        name=req.name,
        scope=req.scope,
        created_by=user_id,
        expires_days=req.expires_days,
        org_id=org.org_id,
    )

    keys = await list_mcp_api_keys(org_id=org.org_id)
    key_info = next((k for k in keys if k["name"] == req.name), None)
    if not key_info:
        raise HTTPException(status_code=500, detail="Failed to create key")

    return CreateKeyResponse(
        key=raw_key,
        id=key_info["id"],
        name=key_info["name"],
        scope=key_info["scope"],
        expires_at=key_info["expires_at"].isoformat() if key_info["expires_at"] else None,
    )


@router.get("")
async def list_keys(org: ActiveOrg = Depends(get_active_org)):
    """List MCP API keys for the active organization."""
    keys = await list_mcp_api_keys(org_id=org.org_id)
    for key in keys:
        for field in ("created_at", "last_used_at", "expires_at"):
            if key.get(field):
                key[field] = key[field].isoformat()
    return {"keys": keys}


@router.delete("/{key_id}")
async def revoke_key(
    key_id: int,
    user_id: int = Depends(get_current_user_id),
    org: ActiveOrg = Depends(get_active_org),
):
    """Revoke an MCP API key. Requires it to belong to the active organization,
    and either admin role or ownership of the key."""
    key = await get_mcp_api_key(key_id)
    if not key or key.get("org_id") != org.org_id:
        raise HTTPException(status_code=404, detail="Key not found")

    is_owner = key.get("created_by") == user_id
    if not is_owner and not tenancy.role_at_least(org.role, "admin"):
        raise HTTPException(status_code=403, detail="Only the key creator or an org admin can revoke this key")

    success = await revoke_mcp_api_key(key_id, org_id=org.org_id)
    if not success:
        raise HTTPException(status_code=404, detail="Key not found")
    return {"status": "ok"}


@router.post("/validate")
async def validate_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Validate an MCP API key (for testing)."""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing API key")
    key_info = await validate_mcp_api_key(x_api_key)
    if not key_info:
        raise HTTPException(status_code=401, detail="Invalid or expired API key")
    return {"valid": True, "key": {"id": key_info["id"], "name": key_info["name"], "scope": key_info["scope"]}}
