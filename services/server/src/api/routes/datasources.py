"""REST API routes for external database connections (data sources)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...datasources import service
from ...datasources.engines import SUPPORTED_ENGINES
from ...datasources.service import ConnectionNotFoundError
from ...datasources.validator import QueryValidationError
from ..deps import ActiveProject, get_active_project, require_project_role

router = APIRouter(prefix="/datasources", tags=["datasources"])


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


class ExecuteRequest(BaseModel):
    sql: str


@router.get("")
async def list_connections(org: ActiveProject = Depends(get_active_project)):
    connections = await service.list_connections(org.org_id, org.project_id)
    return {"connections": connections, "engines": list(SUPPORTED_ENGINES)}


@router.get("/{connection_id}/schema")
async def get_schema(
    connection_id: int,
    schema: Optional[str] = None,
    org: ActiveProject = Depends(get_active_project),
):
    try:
        return await service.schema_overview(org.org_id, org.project_id, connection_id, schema=schema)
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
    org: ActiveProject = Depends(get_active_project),
):
    try:
        return await service.describe_table(
            org.org_id, org.project_id, connection_id, table_name, schema=schema, sample_rows=sample_rows
        )
    except ConnectionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Table introspection failed: {e}")


@router.get("/{connection_id}/annotations")
async def list_annotations(connection_id: int, org: ActiveProject = Depends(get_active_project)):
    # Membership check via connection lookup.
    try:
        await service.get_connection(org.org_id, org.project_id, connection_id)
    except ConnectionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    annotations = await service.list_annotations(connection_id)
    return {"annotations": annotations, "count": len(annotations)}


@router.put("/{connection_id}/annotations")
async def upsert_annotations(
    connection_id: int,
    req: AnnotationsRequest,
    org: ActiveProject = Depends(require_project_role("member")),
):
    """Upsert (or delete, when description is empty) data-dictionary entries.

    Accepts a list so curated metadata can be imported in bulk (e.g. migrating
    an existing hand-written data dictionary).
    """
    try:
        await service.get_connection(org.org_id, org.project_id, connection_id)
    except ConnectionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    written = await service.upsert_annotations(
        connection_id, [a.model_dump() for a in req.annotations]
    )
    return {"status": "ok", "written": written}


@router.post("/{connection_id}/query")
async def run_query(
    connection_id: int, req: QueryRequest, org: ActiveProject = Depends(require_project_role("member"))
):
    try:
        return await service.run_query(
            org.org_id, org.project_id, connection_id, req.sql, max_rows=req.max_rows, source="ui"
        )
    except ConnectionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except QueryValidationError as e:
        raise HTTPException(status_code=400, detail=f"Query rejected: {e}")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/{connection_id}/execute")
async def execute_write(
    connection_id: int,
    req: ExecuteRequest,
    project: ActiveProject = Depends(require_project_role("owner")),
):
    """Execute a guarded DML statement on the datasource (owner only)."""
    try:
        return await service.run_write(
            project.org_id, project.project_id, connection_id, req.sql, source="ui"
        )
    except ConnectionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except QueryValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/{connection_id}/log")
async def get_query_log(
    connection_id: int, limit: int = 50, org: ActiveProject = Depends(get_active_project)
):
    try:
        await service.get_connection(org.org_id, org.project_id, connection_id)
    except ConnectionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    log = await service.query_log(org.org_id, org.project_id, connection_id, limit=min(limit, 200))
    return {"log": log, "count": len(log)}
