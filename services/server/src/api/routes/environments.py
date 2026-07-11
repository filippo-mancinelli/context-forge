"""REST API routes for environments (deployment targets: staging, production, ...)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...db import get_pool
from ..deps import ActiveOrg, get_active_org, require_role

router = APIRouter(prefix="/environments", tags=["environments"])

KINDS = ("production", "staging", "development", "other")


class EnvironmentOut(BaseModel):
    id: int
    name: str
    kind: str
    url: Optional[str] = None
    domains: list[str] = []
    db_connection_id: Optional[int] = None
    db_connection_name: Optional[str] = None
    database_notes: Optional[str] = None
    repo: Optional[str] = None
    branch: Optional[str] = None
    config_notes: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


def _row_to_out(row) -> EnvironmentOut:
    d = dict(row)
    d["domains"] = list(d.get("domains") or [])
    for key in ("created_at", "updated_at"):
        if d.get(key):
            d[key] = d[key].isoformat()
    return EnvironmentOut(**d)


_ENV_SELECT_SQL = """
SELECT e.id, e.name, e.kind, e.url, e.domains, e.db_connection_id, e.database_notes,
       e.repo, e.branch, e.config_notes, e.notes, e.created_at, e.updated_at,
       c.name AS db_connection_name
FROM environments e
LEFT JOIN db_connections c ON c.id = e.db_connection_id
"""


class EnvironmentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: str = "staging"
    url: Optional[str] = None
    domains: list[str] = Field(default_factory=list)
    db_connection_id: Optional[int] = None
    database_notes: Optional[str] = None
    repo: Optional[str] = None
    branch: Optional[str] = None
    config_notes: Optional[str] = None
    notes: Optional[str] = None


async def _validate_db_connection(org_id: int, db_connection_id: Optional[int]) -> None:
    if db_connection_id is None:
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM db_connections WHERE id=$1 AND org_id=$2", db_connection_id, org_id
        )
    if not exists:
        raise HTTPException(status_code=400, detail="Database connection not found")


@router.get("", response_model=list[EnvironmentOut])
async def list_environments(org: ActiveOrg = Depends(get_active_org)):
    """List all environments for the active organization."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"{_ENV_SELECT_SQL} WHERE e.org_id = $1 ORDER BY e.kind, e.name", org.org_id
        )
    return [_row_to_out(r) for r in rows]


@router.post("", status_code=201, response_model=EnvironmentOut)
async def create_environment(req: EnvironmentRequest, org: ActiveOrg = Depends(require_role("member"))):
    """Create an environment record."""
    if req.kind not in KINDS:
        raise HTTPException(status_code=400, detail=f"kind must be one of {', '.join(KINDS)}")
    await _validate_db_connection(org.org_id, req.db_connection_id)

    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            row_id = await conn.fetchval(
                """
                INSERT INTO environments
                    (org_id, name, kind, url, domains, db_connection_id, database_notes,
                     repo, branch, config_notes, notes)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                RETURNING id
                """,
                org.org_id,
                req.name,
                req.kind,
                req.url,
                req.domains,
                req.db_connection_id,
                req.database_notes,
                req.repo,
                req.branch,
                req.config_notes,
                req.notes,
            )
    except Exception as e:  # noqa: BLE001
        if "environments_org_id_name_key" in str(e):
            raise HTTPException(status_code=400, detail=f"Environment '{req.name}' already exists")
        raise HTTPException(status_code=500, detail=str(e))

    return await _get_one(org.org_id, row_id)


@router.put("/{env_id}", response_model=EnvironmentOut)
async def update_environment(
    env_id: int, req: EnvironmentRequest, org: ActiveOrg = Depends(require_role("member"))
):
    """Update an environment record."""
    if req.kind not in KINDS:
        raise HTTPException(status_code=400, detail=f"kind must be one of {', '.join(KINDS)}")
    await _validate_db_connection(org.org_id, req.db_connection_id)

    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            updated = await conn.fetchval(
                """
                UPDATE environments
                SET name=$3, kind=$4, url=$5, domains=$6, db_connection_id=$7,
                    database_notes=$8, repo=$9, branch=$10, config_notes=$11, notes=$12,
                    updated_at=NOW()
                WHERE id=$1 AND org_id=$2
                RETURNING id
                """,
                env_id,
                org.org_id,
                req.name,
                req.kind,
                req.url,
                req.domains,
                req.db_connection_id,
                req.database_notes,
                req.repo,
                req.branch,
                req.config_notes,
                req.notes,
            )
    except Exception as e:  # noqa: BLE001
        if "environments_org_id_name_key" in str(e):
            raise HTTPException(status_code=400, detail=f"Environment '{req.name}' already exists")
        raise HTTPException(status_code=500, detail=str(e))

    if updated is None:
        raise HTTPException(status_code=404, detail="Environment not found")
    return await _get_one(org.org_id, env_id)


@router.delete("/{env_id}")
async def delete_environment(env_id: int, org: ActiveOrg = Depends(require_role("member"))):
    """Delete an environment record."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        deleted = await conn.fetchval(
            "DELETE FROM environments WHERE id=$1 AND org_id=$2 RETURNING id", env_id, org.org_id
        )
    if deleted is None:
        raise HTTPException(status_code=404, detail="Environment not found")
    return {"status": "ok", "deleted": env_id}


async def _get_one(org_id: int, env_id: int) -> EnvironmentOut:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(f"{_ENV_SELECT_SQL} WHERE e.id=$1 AND e.org_id=$2", env_id, org_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Environment not found")
    return _row_to_out(row)
