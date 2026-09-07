"""MCP API key management routes (scoped to the active organization)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from ... import tenancy
from ...mcp.permissions import PERMISSIONS, permissions_from_scope
from ...tenancy import resolve_role_permissions
from ..deps import ActiveOrg, get_active_org, get_current_user_id, require_role
from ..security import (
    create_mcp_api_key,
    get_mcp_api_key,
    list_mcp_api_keys,
    revoke_mcp_api_key,
    update_mcp_api_key_rate_limit,
    validate_mcp_api_key,
)

router = APIRouter(prefix="/mcp/keys", tags=["mcp-keys"])


class CreateKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    # Nuovo modello: lista esplicita di permessi MCP (o ["*"]).
    permissions: list[str] | None = None
    # Alias legacy, deprecato: tradotto con la stessa mappa del backfill.
    scope: str | None = Field(default=None, pattern=r"^(read|write|admin)(,(read|write|admin))*$")
    expires_days: int | None = Field(default=None, ge=1, le=365)
    # Alias legacy: singolo progetto. Preferire project_ids.
    project_id: int | None = None
    # Key org-level: elenco dei progetti su cui la key è abilitata.
    project_ids: list[int] | None = None
    # Key valida su tutti i progetti dell'org (solo admin/owner).
    all_projects: bool = False
    # Chiamate al minuto consentite alla key. None = illimitata.
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=100000)


class CreateKeyResponse(BaseModel):
    key: str
    id: int
    name: str
    scope: str
    permissions: str
    expires_at: str | None
    rate_limit_per_minute: int | None = None


def _resolve_permissions_csv(req: CreateKeyRequest) -> str:
    """Deriva il CSV permessi dalla richiesta (permissions > scope legacy > default)."""
    if req.permissions is not None:
        unique = list(dict.fromkeys(req.permissions))
        if not unique:
            raise HTTPException(status_code=422, detail="permissions cannot be empty")
        for perm in unique:
            if perm != "*" and perm not in PERMISSIONS:
                raise HTTPException(status_code=422, detail=f"Unknown permission '{perm}'")
        if "*" in unique:
            if len(unique) > 1:
                raise HTTPException(
                    status_code=422, detail="'*' cannot be combined with other permissions"
                )
            return "*"
        return ",".join(sorted(unique))
    scope = req.scope or "read,write"
    perms = permissions_from_scope(scope)
    return "*" if "*" in perms else ",".join(sorted(perms))


def _enforce_creator_cap(permissions_csv: str, allowed: frozenset) -> None:
    """Reject granting a key more than the creator's own effective permissions (see I1).

    Only a creator whose own role resolves to '*' may grant '*' (or anything).
    """
    if "*" in allowed:
        return
    if permissions_csv == "*":
        raise HTTPException(
            status_code=403,
            detail="Cannot grant permissions beyond your own role",
        )
    requested = {p for p in permissions_csv.split(",") if p}
    if not requested.issubset(allowed):
        raise HTTPException(
            status_code=403,
            detail="Cannot grant permissions beyond your own role",
        )


@router.post("", response_model=CreateKeyResponse)
async def create_key(
    req: CreateKeyRequest,
    user_id: int = Depends(get_current_user_id),
    org: ActiveOrg = Depends(require_role("member")),
):
    """Create a new MCP API key for the active organization. Shown only once."""
    from ...projects import get_default_project_id, get_project, resolve_project_access
    from ...tenancy import role_at_least

    permissions_csv = _resolve_permissions_csv(req)
    allowed = await resolve_role_permissions(org.org_id, org.role)
    _enforce_creator_cap(permissions_csv, allowed)

    project_ids: list[int] | None
    if req.all_projects:
        # Key org-wide: solo chi amministra l'org può crearne una.
        if not role_at_least(org.role, "admin"):
            raise HTTPException(
                status_code=403,
                detail="Only org admins can create an organization-wide key",
            )
        project_ids = None
    else:
        requested = req.project_ids or ([] if req.project_id is None else [req.project_id])
        if not requested:
            requested = [await get_default_project_id(org.org_id)]
        validated: list[int] = []
        for pid in dict.fromkeys(requested):
            project = await get_project(pid)
            if project is None or project["org_id"] != org.org_id:
                raise HTTPException(status_code=404, detail="Project not found")
            # Il creatore non può concedere progetti oltre la propria abilitazione.
            if await resolve_project_access(org.org_id, user_id, pid) is None:
                raise HTTPException(
                    status_code=403,
                    detail="Cannot grant a project you are not enabled on",
                )
            validated.append(pid)
        project_ids = validated

    raw_key = await create_mcp_api_key(
        name=req.name,
        scope=req.scope or "read,write",
        created_by=user_id,
        expires_days=req.expires_days,
        org_id=org.org_id,
        permissions=permissions_csv,
        project_ids=project_ids,
        rate_limit_per_minute=req.rate_limit_per_minute,
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
        permissions=key_info["permissions"],
        expires_at=key_info["expires_at"].isoformat() if key_info["expires_at"] else None,
        rate_limit_per_minute=key_info.get("rate_limit_per_minute"),
    )


def _serialize_key(row: dict) -> dict:
    """Turn a raw key row into a JSON-safe dict (isoformat dates, project fields pass through)."""
    out = dict(row)
    for field in ("created_at", "last_used_at", "expires_at"):
        val = out.get(field)
        if val is not None and hasattr(val, "isoformat"):
            out[field] = val.isoformat()
    return out


@router.get("")
async def list_keys(org: ActiveOrg = Depends(get_active_org)):
    """List MCP API keys for the active organization."""
    keys = await list_mcp_api_keys(org_id=org.org_id)
    return {"keys": [_serialize_key(k) for k in keys]}


class UpdateKeyRequest(BaseModel):
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=100000)


@router.put("/{key_id}")
async def update_key(
    key_id: int,
    req: UpdateKeyRequest,
    user_id: int = Depends(get_current_user_id),
    org: ActiveOrg = Depends(get_active_org),
):
    """Replace a key's rate limit. Creator or org admin only.

    PUT semantics: the body replaces the whole setting, so a null or omitted
    `rate_limit_per_minute` means unlimited.
    """
    key = await get_mcp_api_key(key_id)
    if not key or key.get("org_id") != org.org_id:
        raise HTTPException(status_code=404, detail="Key not found")
    if key.get("created_by") != user_id and not tenancy.role_at_least(org.role, "admin"):
        raise HTTPException(
            status_code=403, detail="Only the key creator or an org admin can update this key"
        )
    if not await update_mcp_api_key_rate_limit(key_id, org.org_id, req.rate_limit_per_minute):
        raise HTTPException(status_code=404, detail="Key not found")
    return {"status": "ok", "rate_limit_per_minute": req.rate_limit_per_minute}


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
