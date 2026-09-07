"""REST API per le sorgenti filesystem SSH (scoped per progetto)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...ssh_sources import service
from ..deps import ActiveProject, get_active_project, require_project_role

router = APIRouter(prefix="/ssh-sources", tags=["ssh-sources"])


class WriteFileRequest(BaseModel):
    path: str
    content: str


class SSHSourceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    host: str = Field(min_length=1)
    port: Optional[int] = 22
    username: str = Field(min_length=1)
    auth_method: str = "password"  # 'key' | 'password'
    # Write-only; empty on update means "keep the stored secret".
    password: Optional[str] = None
    private_key: Optional[str] = None
    root_path: str = Field(min_length=1)
    include_globs: Optional[str] = None
    exclude_globs: Optional[str] = None
    description: Optional[str] = None


@router.get("")
async def list_sources(org: ActiveProject = Depends(get_active_project)):
    return {"sources": await service.list_sources(org.org_id, org.project_id)}


@router.post("")
async def create_source(
    req: SSHSourceRequest, org: ActiveProject = Depends(require_project_role("member"))
):
    try:
        source = await service.create_source(org.org_id, org.project_id, req.model_dump())
    except Exception as e:  # noqa: BLE001
        if "ssh_sources_project_name_key" in str(e):
            raise HTTPException(status_code=400, detail=f"Source '{req.name}' already exists")
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "ok", "source": source}


@router.put("/{source_id}")
async def update_source(
    source_id: int,
    req: SSHSourceRequest,
    org: ActiveProject = Depends(require_project_role("member")),
):
    source = await service.update_source(org.org_id, org.project_id, source_id, req.model_dump())
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return {"status": "ok", "source": source}


@router.delete("/{source_id}")
async def delete_source(
    source_id: int, org: ActiveProject = Depends(require_project_role("member"))
):
    if not await service.delete_source(org.org_id, org.project_id, source_id):
        raise HTTPException(status_code=404, detail="Source not found")
    return {"status": "ok"}


@router.post("/{source_id}/test")
async def test_source(
    source_id: int, org: ActiveProject = Depends(require_project_role("member"))
):
    try:
        return await service.test_source(org.org_id, org.project_id, source_id)
    except service.SSHSourceNotFoundError:
        raise HTTPException(status_code=404, detail="Source not found")


@router.get("/{source_id}/files")
async def list_files(
    source_id: int,
    subpath: str = "",
    recursive: bool = False,
    org: ActiveProject = Depends(get_active_project),
):
    """Anteprima dei file leggibili (usa gli stessi confini/glob dei tool MCP)."""
    import asyncio

    from ...ssh_sources import client

    record = await service.get_source(org.org_id, org.project_id, source_id, include_secret=True)
    if record is None:
        raise HTTPException(status_code=404, detail="Source not found")
    try:
        files = await asyncio.to_thread(
            client.list_files, service.decrypted_conn(record), subpath, recursive
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))
    return {"files": files}


@router.put("/{source_id}/files")
async def write_source_file(
    source_id: int,
    req: WriteFileRequest,
    project: ActiveProject = Depends(require_project_role("owner")),
):
    """Crea o sovrascrive un file sulla sorgente SSH (solo owner)."""
    import asyncio

    from ...ssh_sources import client

    try:
        record = await service.resolve_source(
            project.org_id, project.project_id, source_id, include_secret=True
        )
    except service.SSHSourceNotFoundError:
        raise HTTPException(status_code=404, detail="Source not found")
    try:
        result = await asyncio.to_thread(
            client.write_file, service.decrypted_conn(record), req.path, req.content
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e))
    return {"status": "ok", **result}
