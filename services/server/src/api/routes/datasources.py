"""REST API routes for external database connections (data sources)."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...datasources import service
from ...datasources.engines import SUPPORTED_ENGINES
from ...datasources.service import ConnectionNotFoundError
from ...datasources.validator import QueryValidationError
from ..deps import ActiveOrg, get_active_org, require_role

router = APIRouter(prefix="/datasources", tags=["datasources"])


class ConnectionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    engine: str
    host: Optional[str] = None
    port: Optional[int] = None
    database_name: Optional[str] = None
    username: Optional[str] = None
    # Write-only; empty on update means "keep the stored password".
    password: Optional[str] = None
    options: dict[str, Any] = Field(default_factory=dict)
    description: Optional[str] = None


class AnnotationItem(BaseModel):
    schema_name: str = ""
    table_name: str
    column_name: str = ""
    description: str  # empty deletes the annotation


class AnnotationsRequest(BaseModel):
    annotations: list[AnnotationItem]


class QueryRequest(BaseModel):
    sql: str
    max_rows: int = 100


@router.get("")
async def list_connections(org: ActiveOrg = Depends(get_active_org)):
    connections = await service.list_connections(org.org_id)
    return {"connections": connections, "engines": list(SUPPORTED_ENGINES)}


@router.post("")
async def create_connection(req: ConnectionRequest, org: ActiveOrg = Depends(require_role("member"))):
    try:
        connection = await service.create_connection(org.org_id, req.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        if "db_connections_org_id_name_key" in str(e):
            raise HTTPException(status_code=400, detail=f"Connection '{req.name}' already exists")
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "ok", "connection": connection}


@router.put("/{connection_id}")
async def update_connection(
    connection_id: int, req: ConnectionRequest, org: ActiveOrg = Depends(require_role("member"))
):
    try:
        connection = await service.update_connection(org.org_id, connection_id, req.model_dump())
    except ConnectionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok", "connection": connection}


@router.delete("/{connection_id}")
async def delete_connection(connection_id: int, org: ActiveOrg = Depends(require_role("member"))):
    try:
        await service.delete_connection(org.org_id, connection_id)
    except ConnectionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"status": "ok"}


@router.post("/{connection_id}/test")
async def test_connection(connection_id: int, org: ActiveOrg = Depends(require_role("member"))):
    try:
        result = await service.test_connection(org.org_id, connection_id)
    except ConnectionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return result


@router.get("/{connection_id}/schema")
async def get_schema(
    connection_id: int,
    schema: Optional[str] = None,
    org: ActiveOrg = Depends(get_active_org),
):
    try:
        return await service.schema_overview(org.org_id, connection_id, schema=schema)
    except ConnectionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Schema introspection failed: {e}")


@router.get("/{connection_id}/tables/{table_name}")
async def describe_table(
    connection_id: int,
    table_name: str,
    schema: Optional[str] = None,
    sample_rows: int = 0,
    org: ActiveOrg = Depends(get_active_org),
):
    try:
        return await service.describe_table(
            org.org_id, connection_id, table_name, schema=schema, sample_rows=sample_rows
        )
    except ConnectionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Table introspection failed: {e}")


@router.get("/{connection_id}/annotations")
async def list_annotations(connection_id: int, org: ActiveOrg = Depends(get_active_org)):
    # Membership check via connection lookup.
    try:
        await service.get_connection(org.org_id, connection_id)
    except ConnectionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    annotations = await service.list_annotations(connection_id)
    return {"annotations": annotations, "count": len(annotations)}


@router.put("/{connection_id}/annotations")
async def upsert_annotations(
    connection_id: int, req: AnnotationsRequest, org: ActiveOrg = Depends(require_role("member"))
):
    """Upsert (or delete, when description is empty) data-dictionary entries.

    Accepts a list so curated metadata can be imported in bulk (e.g. migrating
    an existing hand-written data dictionary).
    """
    try:
        await service.get_connection(org.org_id, connection_id)
    except ConnectionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    written = await service.upsert_annotations(
        connection_id, [a.model_dump() for a in req.annotations]
    )
    return {"status": "ok", "written": written}


@router.post("/{connection_id}/query")
async def run_query(
    connection_id: int, req: QueryRequest, org: ActiveOrg = Depends(require_role("member"))
):
    try:
        return await service.run_query(
            org.org_id, connection_id, req.sql, max_rows=req.max_rows, source="ui"
        )
    except ConnectionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except QueryValidationError as e:
        raise HTTPException(status_code=400, detail=f"Query rejected: {e}")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/{connection_id}/log")
async def get_query_log(
    connection_id: int, limit: int = 50, org: ActiveOrg = Depends(get_active_org)
):
    try:
        await service.get_connection(org.org_id, connection_id)
    except ConnectionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    log = await service.query_log(org.org_id, connection_id, limit=min(limit, 200))
    return {"log": log, "count": len(log)}
