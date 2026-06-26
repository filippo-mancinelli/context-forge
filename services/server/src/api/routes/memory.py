"""REST API routes for memory management."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..deps import ActiveOrg, get_active_org

router = APIRouter(prefix="/memory", tags=["memory"])


def _get_memory():
    from ...mcp.memory import _get_memory as _m
    return _m()


class MemoryAddRequest(BaseModel):
    content: str
    metadata: Optional[dict[str, Any] | str] = None
    infer: bool = True


class MemorySearchRequest(BaseModel):
    query: str
    limit: int = 20


@router.post("")
async def add_memory(req: MemoryAddRequest, org: ActiveOrg = Depends(get_active_org)):
    """Add a memory in the active organization's namespace.

    Set infer=false to store the text directly without LLM extraction."""
    try:
        mem = _get_memory()
        from ...mcp.memory import _normalize_metadata

        result = mem.add(
            req.content,
            user_id=org.namespace,
            metadata=_normalize_metadata(req.metadata),
            infer=req.infer,
        )
        return {"status": "ok", "memory": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("")
async def list_memories(limit: int = 50, org: ActiveOrg = Depends(get_active_org)):
    """List recent memories in the active organization's namespace."""
    try:
        mem = _get_memory()
        results = mem.get_all(filters={"user_id": org.namespace})
        memories = results.get("results", results) if isinstance(results, dict) else results
        return {"memories": memories[:limit], "count": len(memories[:limit])}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/search")
async def search_memories(req: MemorySearchRequest, org: ActiveOrg = Depends(get_active_org)):
    """Search memories by semantic similarity within the active organization."""
    try:
        mem = _get_memory()
        results = mem.search(req.query, filters={"user_id": org.namespace}, limit=req.limit)
        memories = results.get("results", results) if isinstance(results, dict) else results
        return {"memories": memories, "count": len(memories)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{memory_id}")
async def delete_memory(memory_id: str):
    """Delete a memory by ID."""
    try:
        _get_memory().delete(memory_id)
        return {"status": "ok", "deleted": memory_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
