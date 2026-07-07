"""FastMCP server instance — shared across tool modules."""
from fastmcp import FastMCP

mcp = FastMCP(
    name="context-forge",
    instructions=(
        "context-forge provides persistent memory and semantic search across "
        "your codebase. Use memory_* tools to store and retrieve knowledge across "
        "sessions. Use repo_* tools to search code and navigate repositories. "
        "Use db_* tools to explore external database schemas (with curated "
        "descriptions) and run read-only SQL queries. "
        "Use job_* tools to run long HTTP calls (e.g. slow AI agents) without timeouts."
    ),
)
