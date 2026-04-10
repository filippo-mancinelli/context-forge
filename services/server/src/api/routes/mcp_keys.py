"""MCP API key management routes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..security import (
    create_mcp_api_key,
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
async def create_key(req: CreateKeyRequest, user_id: int = 1):
    """Create a new MCP API key. The key is shown only once."""
    # TODO: Get real user_id from session after auth is implemented
    raw_key = await create_mcp_api_key(
        name=req.name,
        scope=req.scope,
        created_by=user_id,
        expires_days=req.expires_days,
    )

    # Get the created key's ID and metadata
    keys = await list_mcp_api_keys(user_id)
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
async def list_keys(user_id: int = 1):
    """List all MCP API keys for the current user."""
    # TODO: Get real user_id from session after auth is implemented
    keys = await list_mcp_api_keys(user_id)
    for key in keys:
        if key.get("created_at"):
            key["created_at"] = key["created_at"].isoformat()
        if key.get("last_used_at"):
            key["last_used_at"] = key["last_used_at"].isoformat()
        if key.get("expires_at"):
            key["expires_at"] = key["expires_at"].isoformat()
    return {"keys": keys}


@router.delete("/{key_id}")
async def revoke_key(key_id: int, user_id: int = 1):
    """Revoke an MCP API key."""
    # TODO: Verify ownership before revoking
    success = await revoke_mcp_api_key(key_id)
    if not success:
        raise HTTPException(status_code=404, detail="Key not found")
    return {"status": "ok"}


@router.post("/validate")
async def validate_key(api_key: str):
    """Validate an MCP API key (for testing)."""
    key_info = await validate_mcp_api_key(api_key)
    if not key_info:
        raise HTTPException(status_code=401, detail="Invalid or expired API key")
    return {"valid": True, "key": {"id": key_info["id"], "name": key_info["name"], "scope": key_info["scope"]}}
