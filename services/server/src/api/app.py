"""FastAPI application for the Web UI backend."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes import repos as repos_routes
from .routes import memory as memory_routes
from .routes import jobs as jobs_routes

api = FastAPI(
    title="context-forge API",
    description="REST API for the context-forge Web UI",
    version="0.1.0",
)

api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api.include_router(repos_routes.router, prefix="/api")
api.include_router(memory_routes.router, prefix="/api")
api.include_router(jobs_routes.router, prefix="/api")


@api.get("/api/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "context-forge-api"}


@api.get("/api/tools")
async def list_tools():
    """List all registered MCP tools with their descriptions."""
    from ..mcp.server import mcp
    tools = []
    try:
        # FastMCP v2 internal API
        raw = mcp._tool_manager.list_tools()
        for tool in raw:
            tools.append({
                "name": getattr(tool, "name", str(tool)),
                "description": getattr(tool, "description", "") or "",
            })
    except Exception:
        # Fallback: return the known tool names from our modules
        tools = [
            {"name": "memory_add", "description": "Save a memory, fact, or note persistently across sessions."},
            {"name": "memory_search", "description": "Search memories semantically."},
            {"name": "memory_list", "description": "List recent memories."},
            {"name": "memory_delete", "description": "Delete a specific memory by its ID."},
            {"name": "repo_list", "description": "List all configured repositories and their indexing status."},
            {"name": "repo_search", "description": "Search across indexed repositories using semantic similarity."},
            {"name": "repo_get_file", "description": "Read the full content of a file from an indexed repository."},
            {"name": "repo_index", "description": "Trigger re-indexing of one or all repositories."},
            {"name": "repo_relationships", "description": "Discover semantic relationships between repositories."},
            {"name": "job_submit", "description": "Submit a long-running HTTP request as an async background job."},
            {"name": "job_status", "description": "Check the status of a submitted async job."},
            {"name": "job_result", "description": "Retrieve the result of a completed async job."},
        ]
    return {"tools": tools, "count": len(tools)}
