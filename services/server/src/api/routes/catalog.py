"""REST API del catalogo dell'organizzazione: macchine, cartelle SSH, database.

Leggere il catalogo richiede l'appartenenza all'organizzazione. Scriverlo,
testarne le voci o cancellarle richiede il ruolo admin, perché le risorse
portano credenziali. I segreti non sono mai restituiti.
"""
from __future__ import annotations

from typing import Any, Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ... import projects
from ...catalog import machines, selections
from ...datasources import service as db_service
from ...datasources.engines import SUPPORTED_ENGINES
from ...ssh_sources import service as ssh_service
from ...tenancy import role_at_least
from ..deps import ActiveOrg, get_active_org, get_current_user_id, require_role


async def catalog_reader(
    org: ActiveOrg = Depends(get_active_org),
    user_id: int = Depends(get_current_user_id),
) -> ActiveOrg:
    """Il catalogo lo consulta chi lavora su almeno un progetto dell'organizzazione."""
    if role_at_least(org.role, "admin"):
        return org
    if not await projects.list_accessible_projects(org.org_id, user_id):
        raise HTTPException(
            status_code=403, detail="Requires access to at least one project of this organization"
        )
    return org


# Ogni route del catalogo passa da catalog_reader; le scritture chiedono in più il ruolo admin.
router = APIRouter(prefix="/catalog", tags=["catalog"], dependencies=[Depends(catalog_reader)])


class MachineRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    host: str = Field(min_length=1)
    port: int = 22
    username: str = Field(min_length=1)
    auth_method: str = "password"  # 'key' | 'password'
    # Write-only: vuoto in modifica significa "mantieni il segreto memorizzato".
    password: Optional[str] = None
    private_key: Optional[str] = None
    description: Optional[str] = None


class FolderRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    machine_id: int
    root_path: str = Field(min_length=1)
    include_globs: Optional[str] = None
    exclude_globs: Optional[str] = None
    description: Optional[str] = None
    restricted: bool = False


class DatabaseRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    engine: str
    host: Optional[str] = None
    port: Optional[int] = None
    database_name: Optional[str] = None
    username: Optional[str] = None
    # Write-only: vuoto in modifica significa "mantieni la password memorizzata".
    password: Optional[str] = None
    options: dict[str, Any] = Field(default_factory=dict)
    description: Optional[str] = None
    # Macchina del catalogo attraverso cui passa il tunnel; None = diretta.
    ssh_machine_id: Optional[int] = None
    restricted: bool = False


async def _guard_delete(org_id: int, kind: str, resource_id: int, confirm: bool) -> None:
    using = await selections.projects_using(org_id, kind, resource_id)
    if using and not confirm:
        raise HTTPException(
            status_code=409,
            detail={"message": "The resource is selected by projects", "projects": using},
        )


# ── Macchine ──────────────────────────────────────────────────────────────────


@router.get("/machines")
async def list_machines(org: ActiveOrg = Depends(get_active_org)):
    return {"machines": await machines.list_machines(org.org_id)}


@router.post("/machines")
async def create_machine(req: MachineRequest, org: ActiveOrg = Depends(require_role("admin"))):
    try:
        machine = await machines.create_machine(org.org_id, req.model_dump())
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=400, detail="A machine with this name or address already exists")
    return {"status": "ok", "machine": machine}


@router.put("/machines/{machine_id}")
async def update_machine(
    machine_id: int, req: MachineRequest, org: ActiveOrg = Depends(require_role("admin"))
):
    try:
        machine = await machines.update_machine(org.org_id, machine_id, req.model_dump())
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=400, detail="A machine with this name or address already exists")
    if machine is None:
        raise HTTPException(status_code=404, detail="Machine not found")
    return {"status": "ok", "machine": machine}


@router.delete("/machines/{machine_id}")
async def delete_machine(machine_id: int, org: ActiveOrg = Depends(require_role("admin"))):
    try:
        deleted = await machines.delete_machine(org.org_id, machine_id)
    except machines.MachineInUseError as e:
        raise HTTPException(
            status_code=409,
            detail={"message": str(e), "folders": e.folders, "databases": e.databases},
        )
    if not deleted:
        raise HTTPException(status_code=404, detail="Machine not found")
    return {"status": "ok"}


@router.post("/machines/{machine_id}/test")
async def test_machine(machine_id: int, org: ActiveOrg = Depends(require_role("admin"))):
    try:
        return await machines.test_machine(org.org_id, machine_id)
    except machines.MachineNotFoundError:
        raise HTTPException(status_code=404, detail="Machine not found")


# ── Cartelle SSH ──────────────────────────────────────────────────────────────


@router.get("/folders")
async def list_folders(org: ActiveOrg = Depends(get_active_org)):
    return {"sources": await ssh_service.list_catalog(org.org_id)}


@router.post("/folders")
async def create_folder(req: FolderRequest, org: ActiveOrg = Depends(require_role("admin"))):
    try:
        source = await ssh_service.create_source(org.org_id, req.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=400, detail=f"Folder '{req.name}' already exists")
    return {"status": "ok", "source": source}


@router.put("/folders/{source_id}")
async def update_folder(
    source_id: int, req: FolderRequest, org: ActiveOrg = Depends(require_role("admin"))
):
    try:
        source = await ssh_service.update_source(org.org_id, source_id, req.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=400, detail=f"Folder '{req.name}' already exists")
    if source is None:
        raise HTTPException(status_code=404, detail="Folder not found")
    return {"status": "ok", "source": source}


@router.delete("/folders/{source_id}")
async def delete_folder(
    source_id: int, confirm: bool = False, org: ActiveOrg = Depends(require_role("admin"))
):
    await _guard_delete(org.org_id, "folders", source_id, confirm)
    if not await ssh_service.delete_source(org.org_id, source_id):
        raise HTTPException(status_code=404, detail="Folder not found")
    return {"status": "ok"}


@router.post("/folders/{source_id}/test")
async def test_folder(source_id: int, org: ActiveOrg = Depends(require_role("admin"))):
    try:
        return await ssh_service.test_source(org.org_id, source_id)
    except ssh_service.SSHSourceNotFoundError:
        raise HTTPException(status_code=404, detail="Folder not found")


# I nomi dei progetti che usano una risorsa servono alla conferma di
# cancellazione: li vede solo chi può cancellarla.
@router.get("/folders/{source_id}/projects")
async def folder_projects(source_id: int, org: ActiveOrg = Depends(require_role("admin"))):
    return {"projects": await selections.projects_using(org.org_id, "folders", source_id)}


# ── Database ──────────────────────────────────────────────────────────────────


@router.get("/databases")
async def list_databases(org: ActiveOrg = Depends(get_active_org)):
    return {
        "connections": await db_service.list_catalog_connections(org.org_id),
        "engines": list(SUPPORTED_ENGINES),
    }


@router.post("/databases")
async def create_database(req: DatabaseRequest, org: ActiveOrg = Depends(require_role("admin"))):
    try:
        connection = await db_service.create_connection(org.org_id, req.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=400, detail=f"Connection '{req.name}' already exists")
    return {"status": "ok", "connection": connection}


@router.put("/databases/{connection_id}")
async def update_database(
    connection_id: int, req: DatabaseRequest, org: ActiveOrg = Depends(require_role("admin"))
):
    try:
        connection = await db_service.update_connection(org.org_id, connection_id, req.model_dump())
    except db_service.ConnectionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=400, detail=f"Connection '{req.name}' already exists")
    return {"status": "ok", "connection": connection}


@router.delete("/databases/{connection_id}")
async def delete_database(
    connection_id: int, confirm: bool = False, org: ActiveOrg = Depends(require_role("admin"))
):
    await _guard_delete(org.org_id, "databases", connection_id, confirm)
    try:
        await db_service.delete_connection(org.org_id, connection_id)
    except db_service.ConnectionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"status": "ok"}


@router.post("/databases/{connection_id}/test")
async def test_database(connection_id: int, org: ActiveOrg = Depends(require_role("admin"))):
    try:
        return await db_service.test_connection(org.org_id, connection_id)
    except db_service.ConnectionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/databases/{connection_id}/projects")
async def database_projects(connection_id: int, org: ActiveOrg = Depends(require_role("admin"))):
    return {"projects": await selections.projects_using(org.org_id, "databases", connection_id)}
