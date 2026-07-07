"""MCP tools for external database connections (schema context + read-only queries)."""
from __future__ import annotations

import logging
from typing import Optional

from .server import mcp

logger = logging.getLogger(__name__)


@mcp.tool()
async def db_list() -> dict:
    """List the external database connections available to this organization.

    Returns connection names to use with db_schema, db_describe, and db_query,
    plus engine type, target database, and last known reachability status.

    Returns:
        dict with a list of connections (name, engine, database, status,
        description, annotation_count)
    """
    from ..datasources import service
    from .context import resolve_org_id

    org_id = await resolve_org_id()
    try:
        connections = await service.list_connections(org_id)
    except Exception as e:  # noqa: BLE001
        logger.error("db_list failed: %s", e)
        return {"status": "error", "error": str(e)}
    slim = [
        {
            "name": c["name"],
            "engine": c["engine"],
            "host": c.get("host"),
            "database": c.get("database_name"),
            "description": c.get("description"),
            "status": c.get("status"),
            "annotation_count": c.get("annotation_count", 0),
        }
        for c in connections
    ]
    return {"status": "ok", "connections": slim, "count": len(slim)}


@mcp.tool()
async def db_schema(connection: str, schema: Optional[str] = None) -> dict:
    """Get the schema overview of an external database: tables, views, row estimates.

    Shallow context: every table with its column count, estimated row count,
    database comment, and human-curated description when available. Use
    db_describe on a specific table for columns, keys, and indexes.

    Args:
        connection: Connection name (from db_list)
        schema: Optional schema name (defaults to the database's default schema)

    Returns:
        dict with dialect, schemas, tables (name, description, comment,
        column_count, estimated_rows) and views
    """
    from ..datasources import service
    from .context import resolve_org_id

    org_id = await resolve_org_id()
    try:
        overview = await service.schema_overview(org_id, connection, schema=schema)
    except Exception as e:  # noqa: BLE001
        logger.error("db_schema failed: %s", e)
        return {"status": "error", "error": str(e)}
    return {"status": "ok", **overview}


@mcp.tool()
async def db_describe(
    connection: str,
    table: str,
    schema: Optional[str] = None,
    sample_rows: int = 0,
) -> dict:
    """Describe a table of an external database in depth.

    Deep context: columns (type, nullable, default, comment, curated
    description), primary key, foreign keys, indexes, unique constraints, and
    estimated row count. Optionally includes a few sample rows.

    Args:
        connection: Connection name (from db_list)
        table: Table name (from db_schema)
        schema: Optional schema name (defaults to the database's default schema)
        sample_rows: Include up to N sample rows (0-10, default 0). Sample data
            may contain real user data — request it only when needed.

    Returns:
        dict with columns, primary_key, foreign_keys, indexes,
        unique_constraints, estimated_rows, and optional sample_rows
    """
    from ..datasources import service
    from .context import resolve_org_id

    org_id = await resolve_org_id()
    try:
        detail = await service.describe_table(
            org_id, connection, table, schema=schema, sample_rows=sample_rows
        )
    except Exception as e:  # noqa: BLE001
        logger.error("db_describe failed: %s", e)
        return {"status": "error", "error": str(e)}
    return {"status": "ok", **detail}


@mcp.tool()
async def db_query(connection: str, sql: str, max_rows: int = 100) -> dict:
    """Run a read-only SQL query against an external database connection.

    Only a single SELECT / WITH...SELECT / SHOW / DESCRIBE / EXPLAIN statement
    is allowed; mutating statements are rejected. A LIMIT is enforced
    server-side and queries time out after a few seconds. Every query is
    written to an audit log. Use db_schema/db_describe first so the SQL matches
    the real schema.

    Args:
        connection: Connection name (from db_list)
        sql: The read-only SQL statement to execute
        max_rows: Maximum rows to return (default 100, hard cap 500)

    Returns:
        dict with the executed sql, columns, rows, row_count, truncated flag,
        and duration_ms
    """
    from ..datasources import service
    from ..datasources.validator import QueryValidationError
    from .context import resolve_org_id

    org_id = await resolve_org_id()
    try:
        result = await service.run_query(org_id, connection, sql, max_rows=max_rows, source="mcp")
    except QueryValidationError as e:
        return {"status": "error", "error": f"Query rejected: {e}"}
    except Exception as e:  # noqa: BLE001
        logger.error("db_query failed: %s", e)
        return {"status": "error", "error": str(e)}
    return {"status": "ok", **result}
