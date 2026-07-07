"""context-forge server entry point.

Starts two servers concurrently:
  - FastMCP on port 4000 (/mcp) - MCP HTTP endpoint for AI agents
  - FastAPI on port 8000 (/api) - REST API for the Web UI
"""
from __future__ import annotations

import asyncio
import logging
import sys

import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def main() -> None:
    from .config import get_settings
    from .db import init_db, close_db
    from .runtime_state import ensure_runtime_state
    from .scheduler import start_scheduler, stop_scheduler, initial_index

    # Import tool modules so they register on the mcp instance
    from .mcp import memory, repos, jobs, knowledge, datasources, contracts, ci  # noqa: F401
    from .mcp.server import mcp
    from .mcp import oauth  # noqa: F401 - register OAuth handlers
    from .api.app import api
    from .mcp.auth import add_auth_middleware

    settings = get_settings()

    from .tenancy import ensure_tenant_storage

    # Initialize DB
    logger.info("Initializing database...")
    await init_db()
    await ensure_runtime_state()
    # Seed the default organization, attribute pre-existing data to it, and
    # migrate repo storage to tenant-aware composite identity.
    await ensure_tenant_storage()

    # Requeue any knowledge-base documents left mid-processing by a prior crash.
    from .kb.store import reset_stale_processing

    await reset_stale_processing()

    # Initial indexing (background)
    logger.info("Starting initial repo sync...")
    asyncio.create_task(initial_index())

    # Start scheduler
    await start_scheduler()

    # Configure both ASGI apps
    mcp_app = mcp.http_app(path="/mcp")
    # Add authentication middleware if enabled
    mcp_app = add_auth_middleware(mcp_app, auth_mode=settings.mcp_auth_mode)
    mcp_config = uvicorn.Config(
        mcp_app,
        host="0.0.0.0",
        port=settings.mcp_port,
        log_level=settings.log_level.lower(),
        access_log=False,
    )
    api_config = uvicorn.Config(
        api,
        host="0.0.0.0",
        port=settings.api_port,
        log_level=settings.log_level.lower(),
        access_log=False,
    )

    mcp_server = uvicorn.Server(mcp_config)
    api_server = uvicorn.Server(api_config)

    logger.info("Starting MCP server on port %d", settings.mcp_port)
    logger.info("Starting API server on port %d", settings.api_port)

    try:
        await asyncio.gather(
            mcp_server.serve(),
            api_server.serve(),
        )
    finally:
        await stop_scheduler()
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
