"""FastAPI application for the Web UI backend."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from .routes import repos as repos_routes
from .routes import memory as memory_routes
from .routes import jobs as jobs_routes
from .routes import setup as setup_routes
from .routes import auth as auth_routes
from .routes import settings as settings_routes

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
api.include_router(setup_routes.router, prefix="/api")
api.include_router(auth_routes.router, prefix="/api")
api.include_router(settings_routes.router, prefix="/api")


@api.middleware("http")
async def auth_guard(request, call_next):
    path = request.url.path
    if not path.startswith("/api"):
        return await call_next(request)

    open_paths = ("/api/health", "/api/setup", "/api/auth")
    if path.startswith(open_paths):
        return await call_next(request)

    from .security import is_configured, is_legacy_mode_available, require_valid_token_or_raise
    if not await is_configured():
        if await is_legacy_mode_available():
            return await call_next(request)
        return JSONResponse(status_code=423, content={"detail": "Setup required"})

    try:
        await require_valid_token_or_raise(request.headers.get("Authorization"))
    except Exception as e:
        code = getattr(e, "status_code", 401)
        detail = getattr(e, "detail", str(e))
        return JSONResponse(status_code=code, content={"detail": detail})

    return await call_next(request)


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
