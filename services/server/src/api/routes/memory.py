"""REST API routes for memory management."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/memory", tags=["memory"])


def _get_memory():
    from ...mcp.memory import _get_memory as _m
    return _m()


class MemorySearchRequest(BaseModel):
    query: str
    limit: int = 20
    user_id: Optional[str] = None


@router.get("")
async def list_memories(limit: int = 50, user_id: Optional[str] = None):
    """List recent memories."""
    from ...config import get_forge_config
    uid = user_id or get_forge_config().memory.user_id
    try:
        mem = _get_memory()
        results = mem.get_all(user_id=uid)
        memories = results.get("results", results) if isinstance(results, dict) else results
        return {"memories": memories[:limit], "count": len(memories[:limit])}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/search")
async def search_memories(req: MemorySearchRequest):
    """Search memories by semantic similarity."""
    from ...config import get_forge_config
    uid = req.user_id or get_forge_config().memory.user_id
    try:
        mem = _get_memory()
        results = mem.search(req.query, user_id=uid, limit=req.limit)
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
