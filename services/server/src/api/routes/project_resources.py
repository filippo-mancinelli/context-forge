"""REST API delle risorse del catalogo selezionate da un progetto.

Vedere le selezioni richiede l'accesso al progetto; aggiungerle o toglierle il
ruolo member sul progetto. Una risorsa riservata la aggiunge solo un admin
dell'organizzazione.
I database si collegano con un perimetro (database, schema) e un alias, anche più volte la stessa connessione.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ... import projects
from ...catalog import repos as repo_catalog
from ...catalog import selections
from ...datasources import project_scopes, scopes
from ...datasources import service as db_service
from ...ssh_sources import service as ssh_service
from ...tenancy import role_at_least
from ..deps import ActiveOrg, get_active_org, get_current_user_id

router = APIRouter(prefix="/projects", tags=["project-resources"])


class SelectionRequest(BaseModel):
    kind: str
    resource_id: int


class ScopeRequest(BaseModel):
    """Perimetro e alias di un collegamento a un database."""

    model_config = ConfigDict(populate_by_name=True)

    database: Optional[str] = None
    # "schema" è il nome del campo nel JSON; in Python oscurerebbe BaseModel.schema.
    scope_schema: Optional[str] = Field(default=None, alias="schema")
    alias: Optional[str] = None


class ScopeCreateRequest(ScopeRequest):
    connection_id: int


# Errori del collegamento con perimetro che diventano risposte REST. Sono solo
# questi: un guasto di altra natura resta un errore del server.
_SCOPE_ERRORS = (
    project_scopes.ScopeNotFoundError,
    project_scopes.ScopeConflictError,
    selections.ResourceNotFoundError,
    selections.RestrictedResourceError,
    scopes.ScopeShapeError,
    project_scopes.AliasError,
)


def _scope_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, project_scopes.ScopeNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, selections.ResourceNotFoundError):
        return HTTPException(status_code=404, detail="Database connection not found in this organization")
    if isinstance(exc, selections.RestrictedResourceError):
        return HTTPException(
            status_code=403, detail="Restricted resource: only organization admins can add it to a project"
        )
    if isinstance(exc, project_scopes.ScopeConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    # Perimetro di forma sbagliata o alias non valido: richiesta da correggere.
    return HTTPException(status_code=400, detail=str(exc))


async def _project_role(project_id: int, org: ActiveOrg, user_id: int) -> str:
    project = await projects.get_project(project_id)
    if project is None or project["org_id"] != org.org_id:
        raise HTTPException(status_code=404, detail="Project not found")
    role = await projects.resolve_project_access(org.org_id, user_id, project_id)
    if role is None:
        raise HTTPException(status_code=403, detail="Not a member of this project")
    return role


@router.get("/{project_id}/resources")
async def list_project_resources(
    project_id: int,
    org: ActiveOrg = Depends(get_active_org),
    user_id: int = Depends(get_current_user_id),
):
    await _project_role(project_id, org, user_id)
    return {
        "folders": await ssh_service.list_sources(org.org_id, project_id),
        "databases": await db_service.list_connections(org.org_id, project_id),
        "repos": await repo_catalog.list_project_repos(org.org_id, project_id),
    }


@router.post("/{project_id}/resources")
async def select_project_resource(
    project_id: int,
    req: SelectionRequest,
    org: ActiveOrg = Depends(get_active_org),
    user_id: int = Depends(get_current_user_id),
):
    role = await _project_role(project_id, org, user_id)
    if not role_at_least(role, "member"):
        raise HTTPException(status_code=403, detail="Requires the member role on this project")
    try:
        result = await selections.select_resource(
            org.org_id, project_id, req.kind, req.resource_id, user_id, role_at_least(org.role, "admin")
        )
    except selections.UnknownKindError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except selections.ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="Resource not found in this organization")
    except selections.RestrictedResourceError:
        raise HTTPException(
            status_code=403, detail="Restricted resource: only organization admins can add it to a project"
        )
    return {"status": "ok", **result}


@router.delete("/{project_id}/resources/{kind}/{resource_id}")
async def deselect_project_resource(
    project_id: int,
    kind: str,
    resource_id: int,
    org: ActiveOrg = Depends(get_active_org),
    user_id: int = Depends(get_current_user_id),
):
    role = await _project_role(project_id, org, user_id)
    if not role_at_least(role, "member"):
        raise HTTPException(status_code=403, detail="Requires the member role on this project")
    try:
        removed = await selections.deselect_resource(org.org_id, project_id, kind, resource_id)
    except selections.UnknownKindError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not removed:
        raise HTTPException(status_code=404, detail="The resource is not selected in this project")
    return {"status": "ok"}


async def _require_member(project_id: int, org: ActiveOrg, user_id: int) -> None:
    role = await _project_role(project_id, org, user_id)
    if not role_at_least(role, "member"):
        raise HTTPException(status_code=403, detail="Requires the member role on this project")


@router.post("/{project_id}/databases")
async def add_project_database(
    project_id: int,
    req: ScopeCreateRequest,
    org: ActiveOrg = Depends(get_active_org),
    user_id: int = Depends(get_current_user_id),
):
    """Collega una connessione del catalogo al progetto sul perimetro scelto."""
    await _require_member(project_id, org, user_id)
    try:
        scope = await project_scopes.create_scope(
            org.org_id, project_id, req.connection_id, req.database, req.scope_schema, req.alias,
            user_id, role_at_least(org.role, "admin"),
        )
    except _SCOPE_ERRORS as e:
        raise _scope_http_error(e) from e
    return {"status": "ok", "scope": scope}


@router.put("/{project_id}/databases/{scope_id}")
async def update_project_database(
    project_id: int,
    scope_id: int,
    req: ScopeRequest,
    org: ActiveOrg = Depends(get_active_org),
    user_id: int = Depends(get_current_user_id),
):
    """Cambia perimetro e alias di un collegamento; salvare vale come conferma."""
    await _require_member(project_id, org, user_id)
    try:
        scope = await project_scopes.update_scope(
            org.org_id, project_id, scope_id, req.database, req.scope_schema, req.alias,
            can_select_restricted=role_at_least(org.role, "admin"),
        )
    except _SCOPE_ERRORS as e:
        raise _scope_http_error(e) from e
    return {"status": "ok", "scope": scope}


@router.delete("/{project_id}/databases/{scope_id}")
async def remove_project_database(
    project_id: int,
    scope_id: int,
    org: ActiveOrg = Depends(get_active_org),
    user_id: int = Depends(get_current_user_id),
):
    await _require_member(project_id, org, user_id)
    try:
        await project_scopes.delete_scope(org.org_id, project_id, scope_id)
    except _SCOPE_ERRORS as e:
        raise _scope_http_error(e) from e
    return {"status": "ok"}
