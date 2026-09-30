"""REST API delle risorse del catalogo selezionate da un progetto.

Vedere le selezioni richiede l'accesso al progetto; aggiungerle o toglierle il
ruolo member sul progetto. Una risorsa riservata la aggiunge solo un admin
dell'organizzazione.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ... import projects
from ...catalog import repos as repo_catalog
from ...catalog import selections
from ...datasources import service as db_service
from ...ssh_sources import service as ssh_service
from ...tenancy import role_at_least
from ..deps import ActiveOrg, get_active_org, get_current_user_id

router = APIRouter(prefix="/projects", tags=["project-resources"])


class SelectionRequest(BaseModel):
    kind: str
    resource_id: int


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
