"""REST API routes for the data sources of the active project.

Ogni rotta di dettaglio riceve l'id del perimetro, cioè del collegamento del
progetto a una connessione. Per un rilascio accetta anche l'id di una
connessione che nel progetto ha un solo perimetro. Una richiesta che esce dal
perimetro confermato riceve 400 con un messaggio che nomina il perimetro.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...datasources import service
from ...datasources.engines import SUPPORTED_ENGINES
from ...datasources.scopes import ScopeViolationError
from ...datasources.service import ConnectionAmbiguousError, ConnectionNotFoundError
from ...datasources.validator import QueryValidationError
from ..deps import ActiveProject, get_active_project, require_project_role

router = APIRouter(prefix="/datasources", tags=["datasources"])


class AnnotationItem(BaseModel):
    # Vuoto = schema del perimetro per un perimetro confermato.
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


def _lookup_error(e: Exception) -> HTTPException:
    """Errori di risoluzione del perimetro tradotti in HTTP."""
    if isinstance(e, ConnectionAmbiguousError):
        return HTTPException(status_code=409, detail=str(e))
    return HTTPException(status_code=404, detail=str(e))


@router.get("")
async def list_connections(org: ActiveProject = Depends(get_active_project)):
    connections = await service.list_connections(org.org_id, org.project_id)
    return {"connections": connections, "engines": list(SUPPORTED_ENGINES)}


@router.get("/{source_id}/schema")
async def get_schema(
    source_id: int,
    schema: Optional[str] = None,
    org: ActiveProject = Depends(get_active_project),
):
    try:
        return await service.schema_overview(org.org_id, org.project_id, source_id, schema=schema)
    except (ConnectionNotFoundError, ConnectionAmbiguousError) as e:
        raise _lookup_error(e)
    except ScopeViolationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Schema introspection failed: {e}")


@router.get("/{source_id}/tables/{table_name}")
async def describe_table(
    source_id: int,
    table_name: str,
    schema: Optional[str] = None,
    sample_rows: int = 0,
    org: ActiveProject = Depends(get_active_project),
):
    try:
        return await service.describe_table(
            org.org_id, org.project_id, source_id, table_name, schema=schema, sample_rows=sample_rows
        )
    except (ConnectionNotFoundError, ConnectionAmbiguousError) as e:
        raise _lookup_error(e)
    except ScopeViolationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Table introspection failed: {e}")


@router.get("/{source_id}/annotations")
async def list_annotations(source_id: int, org: ActiveProject = Depends(get_active_project)):
    try:
        annotations = await service.scope_annotations(org.org_id, org.project_id, source_id)
    except (ConnectionNotFoundError, ConnectionAmbiguousError) as e:
        raise _lookup_error(e)
    return {"annotations": annotations, "count": len(annotations)}


@router.put("/{source_id}/annotations")
async def upsert_annotations(
    source_id: int,
    req: AnnotationsRequest,
    org: ActiveProject = Depends(require_project_role("member")),
):
    """Upsert (or delete, when description is empty) data-dictionary entries.

    Accepts a list so curated metadata can be imported in bulk (e.g. migrating
    an existing hand-written data dictionary).
    """
    try:
        written = await service.save_scope_annotations(
            org.org_id, org.project_id, source_id, [a.model_dump() for a in req.annotations]
        )
    except (ConnectionNotFoundError, ConnectionAmbiguousError) as e:
        raise _lookup_error(e)
    except ScopeViolationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok", "written": written}


@router.post("/{source_id}/query")
async def run_query(
    source_id: int, req: QueryRequest, org: ActiveProject = Depends(require_project_role("member"))
):
    try:
        return await service.run_query(
            org.org_id, org.project_id, source_id, req.sql, max_rows=req.max_rows, source="ui"
        )
    except (ConnectionNotFoundError, ConnectionAmbiguousError) as e:
        raise _lookup_error(e)
    except ScopeViolationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except QueryValidationError as e:
        raise HTTPException(status_code=400, detail=f"Query rejected: {e}")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/{source_id}/execute")
async def execute_write(
    source_id: int,
    req: ExecuteRequest,
    project: ActiveProject = Depends(require_project_role("owner")),
):
    """Execute a guarded DML statement on the datasource (owner only)."""
    try:
        return await service.run_write(
            project.org_id, project.project_id, source_id, req.sql, source="ui"
        )
    except (ConnectionNotFoundError, ConnectionAmbiguousError) as e:
        raise _lookup_error(e)
    except ScopeViolationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except QueryValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/{source_id}/log")
async def get_query_log(
    source_id: int, limit: int = 50, org: ActiveProject = Depends(get_active_project)
):
    try:
        record = await service.get_scope(org.org_id, org.project_id, source_id)
    except (ConnectionNotFoundError, ConnectionAmbiguousError) as e:
        raise _lookup_error(e)
    # Il log è della connessione nel progetto: la colonna schema_name dice su
    # quale perimetro è girata ciascuna query.
    log = await service.query_log(org.org_id, org.project_id, record["id"], limit=min(limit, 200))
    return {"log": log, "count": len(log)}
