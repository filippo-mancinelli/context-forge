"""REST API routes for memory management."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..deps import ActiveProject, get_active_project

router = APIRouter(prefix="/memory", tags=["memory"])


async def _get_memory(org_id: int):
    from ...mcp.memory import _get_memory as _m
    return await _m(org_id)


class MemoryAddRequest(BaseModel):
    content: str
    metadata: Optional[dict[str, Any] | str] = None
    infer: bool = True


class MemorySearchRequest(BaseModel):
    query: str
    limit: int = 20


@router.post("")
async def add_memory(
    req: MemoryAddRequest, project: ActiveProject = Depends(get_active_project)
):
    """Add a memory in the active project's namespace.

    Set infer=false to store the text directly without LLM extraction."""
    try:
        mem = await _get_memory(project.org_id)
        from ...mcp.memory import _normalize_metadata

        result = mem.add(
            req.content,
            user_id=project.namespace,
            metadata=_normalize_metadata(req.metadata),
            infer=req.infer,
        )
        return {"status": "ok", "memory": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("")
async def list_memories(
    limit: int = 50, project: ActiveProject = Depends(get_active_project)
):
    """List recent memories in the active project's namespace."""
    try:
        mem = await _get_memory(project.org_id)
        results = mem.get_all(user_id=project.namespace)
        memories = results.get("results", results) if isinstance(results, dict) else results
        return {"memories": memories[:limit], "count": len(memories[:limit])}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/search")
async def search_memories(
    req: MemorySearchRequest, project: ActiveProject = Depends(get_active_project)
):
    """Search memories by semantic similarity within the active project."""
    try:
        mem = await _get_memory(project.org_id)
        results = mem.search(req.query, user_id=project.namespace, limit=req.limit)
        memories = results.get("results", results) if isinstance(results, dict) else results
        return {"memories": memories, "count": len(memories)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{memory_id}")
async def delete_memory(
    memory_id: str, project: ActiveProject = Depends(get_active_project)
):
    """Delete a memory by ID, if it belongs to the active project's namespace.

    mem0's delete-by-id does not filter by user_id, so the memory is looked up
    first and the delete is refused (404) unless it belongs to the caller's
    namespace.
    """
    try:
        mem = await _get_memory(project.org_id)
        existing = mem.get(memory_id)
        if not existing or existing.get("user_id") != project.namespace:
            raise HTTPException(status_code=404, detail=f"Memory '{memory_id}' not found")
        mem.delete(memory_id)
        return {"status": "ok", "deleted": memory_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
