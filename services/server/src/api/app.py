"""FastAPI application for the Web UI backend."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from ..config import get_settings
from .routes import repos as repos_routes
from .routes import memory as memory_routes
from .routes import knowledge as knowledge_routes
from .routes import chat as chat_routes
from .routes import chat_sessions as chat_sessions_routes
from .routes import jobs as jobs_routes
from .routes import setup as setup_routes
from .routes import auth as auth_routes
from .routes import settings as settings_routes
from .routes import github as github_routes
from .routes import gitlab as gitlab_routes
from .routes import mcp_keys as mcp_keys_routes
from .routes import oauth as oauth_routes
from .routes import organizations as organizations_routes
from .routes import invitations as invitations_routes
from .routes import webhooks as webhooks_routes
from .routes import datasources as datasources_routes
from .routes import contracts as contracts_routes
from .routes import ci as ci_routes

api = FastAPI(
    title="context-forge API",
    description="REST API for the context-forge Web UI",
    version="0.1.0",
)

_cors_base = ["http://localhost:3000", "http://127.0.0.1:3000"]
_cors_extra = [o.strip() for o in get_settings().cors_origins.split(",") if o.strip()]

# If any entry is "*", allow all origins (useful for self-hosted / reverse-proxy deployments).
# Wildcard entries like "*.example.com" are treated as regex patterns.
_allow_all = "*" in _cors_extra
_regex_origins = [o for o in _cors_extra if o.startswith("*.")]
_plain_origins = [o for o in _cors_extra if not o.startswith("*.") and o != "*"]

if _allow_all:
    api.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,  # credentials require explicit origin, not wildcard
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    import re as _re
    _origin_regex = (
        "|".join(
            _re.escape(o).replace(r"\*", r"[^.]+") for o in _regex_origins
        )
        if _regex_origins
        else None
    )
    api.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_base + _plain_origins,
        allow_origin_regex=_origin_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

api.include_router(repos_routes.router, prefix="/api")
api.include_router(memory_routes.router, prefix="/api")
api.include_router(knowledge_routes.router, prefix="/api")
api.include_router(chat_sessions_routes.router, prefix="/api")
api.include_router(chat_sessions_routes.public_router, prefix="/api")
api.include_router(chat_routes.router, prefix="/api")
api.include_router(jobs_routes.router, prefix="/api")
api.include_router(setup_routes.router, prefix="/api")
api.include_router(auth_routes.router, prefix="/api")
api.include_router(settings_routes.router, prefix="/api")
api.include_router(github_routes.router, prefix="/api")
api.include_router(gitlab_routes.router, prefix="/api")
api.include_router(mcp_keys_routes.router, prefix="/api")
api.include_router(oauth_routes.router, prefix="/api")
api.include_router(organizations_routes.router, prefix="/api")
api.include_router(invitations_routes.router, prefix="/api")
api.include_router(webhooks_routes.router, prefix="/api")
api.include_router(datasources_routes.router, prefix="/api")
api.include_router(contracts_routes.router, prefix="/api")
api.include_router(ci_routes.router, prefix="/api")


@api.middleware("http")
async def auth_guard(request, call_next):
    path = request.url.path
    if request.method == "OPTIONS":
        return await call_next(request)

    if not path.startswith("/api"):
        return await call_next(request)

    open_paths = (
        "/api/health",
        "/api/setup",
        "/api/auth",
        "/api/mcp/keys/validate",
        "/api/oauth",
        "/api/invitations",  # public invite preview/accept
        "/api/webhooks",     # external git hooks; authenticated by shared secret
        "/api/chat/shared",  # public chat snapshots; authorized by share token
    )
    if path.startswith(open_paths):
        return await call_next(request)

    from .security import is_configured, require_valid_token_or_raise
    if not await is_configured():
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
            {"name": "kb_search", "description": "Search uploaded knowledge-base documents by semantic similarity."},
            {"name": "kb_list", "description": "List documents in the knowledge base and their processing status."},
            {"name": "kb_get_document", "description": "Retrieve the full extracted text of a knowledge-base document."},
            {"name": "repo_list", "description": "List all configured repositories and their indexing status."},
            {"name": "repo_search", "description": "Search across indexed repositories using semantic similarity."},
            {"name": "repo_get_file", "description": "Read the full content of a file from an indexed repository."},
            {"name": "repo_index", "description": "Trigger re-indexing of one or all repositories."},
            {"name": "repo_relationships", "description": "Discover semantic relationships between repositories."},
            {"name": "db_list", "description": "List external database connections available to the organization."},
            {"name": "db_schema", "description": "Get the schema overview of an external database (tables, views, row estimates)."},
            {"name": "db_describe", "description": "Describe a table in depth: columns, keys, indexes, curated descriptions."},
            {"name": "db_query", "description": "Run a validated read-only SQL query against an external database."},
            {"name": "api_list", "description": "List ingested API contracts (OpenAPI specs / GraphQL schemas)."},
            {"name": "api_endpoints", "description": "List or search API operations across ingested contracts."},
            {"name": "api_get_endpoint", "description": "Get one API operation's parameters, request body, and responses."},
            {"name": "ci_runs", "description": "List recent CI runs (GitHub Actions / GitLab CI) for a repository."},
            {"name": "ci_failure", "description": "Get why a CI run failed: failed jobs/steps and error log tails."},
            {"name": "job_submit", "description": "Submit a long-running HTTP request as an async background job."},
            {"name": "job_status", "description": "Check the status of a submitted async job."},
            {"name": "job_result", "description": "Retrieve the result of a completed async job."},
        ]
    return {"tools": tools, "count": len(tools)}
