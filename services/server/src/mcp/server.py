"""FastMCP server instance — shared across tool modules."""
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

mcp = FastMCP(
    name="context-forge",
    instructions=(
        "context-forge provides persistent memory and semantic search across "
        "your codebase. Use memory_* tools to store and retrieve knowledge across "
        "sessions. Use repo_* tools to search code and navigate repositories. "
        "Use job_* tools to run long HTTP calls (e.g. slow AI agents) without timeouts."
    ),
)

# Register OAuth endpoints
@mcp.http_app.get("/oauth/authorize")
async def oauth_authorize_handler(request: Request):
    """OAuth authorization endpoint."""
    from .oauth import oauth_authorize
    return await oauth_authorize(request)


@mcp.http_app.post("/oauth/token")
async def oauth_token_handler(request: Request):
    """OAuth token endpoint."""
    from .oauth import oauth_token
    return await oauth_token(request)


@mcp.http_app.get("/oauth/callback")
async def oauth_callback_handler(request: Request):
    """OAuth callback endpoint."""
    from .oauth import oauth_callback
    return await oauth_callback(request)


@mcp.http_app.post("/oauth/validate")
async def oauth_validate_handler(request: Request):
    """Validate OAuth token."""
    from .oauth import oauth_validate
    return await oauth_validate(request)


@mcp.http_app.post("/oauth/setup-client")
async def oauth_setup_client_handler(request: Request):
    """Setup default OAuth client."""
    from .oauth import oauth_setup_client
    return await oauth_setup_client()
