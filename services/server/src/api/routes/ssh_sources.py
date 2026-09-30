"""REST API delle cartelle SSH selezionate dal progetto attivo (sola lettura, più scrittura file dell'owner)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...ssh_sources import service
from ..deps import ActiveProject, get_active_project, require_project_role

router = APIRouter(prefix="/ssh-sources", tags=["ssh-sources"])


class WriteFileRequest(BaseModel):
    path: str
    content: str


@router.get("")
async def list_sources(org: ActiveProject = Depends(get_active_project)):
    return {"sources": await service.list_sources(org.org_id, org.project_id)}


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
