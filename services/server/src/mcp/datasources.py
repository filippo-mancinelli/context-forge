"""MCP tools for external database connections (schema context + read-only queries)."""
from __future__ import annotations

import logging
from typing import Optional

from .server import mcp

logger = logging.getLogger(__name__)


def _slim_connections(connections: list[dict]) -> list[dict]:
    return [
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


async def _resolve_connection_ref(
    org_id: int,
    connection: Optional[str],
    hint: Optional[str] = None,
) -> tuple[Optional[str], Optional[dict]]:
    """Return (connection_name, list_payload).

    When list_payload is set the caller should return it instead of querying schema.
    """
    from ..datasources import service
    from ..datasources.service import ConnectionAmbiguousError, ConnectionNotFoundError

    if not connection and not hint:
        connections = await service.list_connections(org_id)
        return None, {"status": "ok", "connections": _slim_connections(connections), "count": len(connections)}

    if connection and service.is_list_sentinel(connection):
        connections = await service.list_connections(org_id)
        return None, {
            "status": "ok",
            "connections": _slim_connections(connections),
            "count": len(connections),
            "note": "Use db_list() or omit connection to list connections. "
            "Pass a connection name from this list, or a hint matching the project.",
        }

    resolve_hint = hint or connection
    if connection and not service.is_list_sentinel(connection) and not hint:
        try:
            record = await service.get_connection(org_id, connection)
            return record["name"], None
        except ConnectionNotFoundError:
            resolve_hint = connection

    try:
        record = await service.resolve_connection(org_id, resolve_hint)
    except ConnectionAmbiguousError as e:
        connections = await service.list_connections(org_id)
        return None, {
            "status": "error",
            "error": str(e),
            "connections": _slim_connections(connections),
        }
    except ConnectionNotFoundError as e:
        connections = await service.list_connections(org_id)
        payload: dict = {"status": "error", "error": str(e)}
        if connections:
            payload["connections"] = _slim_connections(connections)
        return None, payload

    return record["name"], None


@mcp.tool()
async def db_list() -> dict:
    """List the external database connections available to this organization.

    Returns connection names to use with db_schema, db_describe, and db_query,
    plus engine type, target database, and last known reachability status.

    Always call this first when you need a database but don't know the exact
    connection name. Match connection names to the project or repo discussed
    in the conversation (e.g. repo "context-forge" → connection "context-forge").

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
    slim = _slim_connections(connections)
    return {"status": "ok", "connections": slim, "count": len(slim)}


@mcp.tool()
async def db_schema(
    connection: Optional[str] = None,
    schema: Optional[str] = None,
    hint: Optional[str] = None,
) -> dict:
    """Get the schema overview of an external database: tables, views, row estimates.

    Shallow context: every table with its column count, estimated row count,
    database comment, and human-curated description when available. Use
    db_describe on a specific table for columns, keys, and indexes.

    Args:
        connection: Exact connection name (from db_list). Omit to list all
            connections, or pass hint instead when inferring from conversation.
        schema: Optional schema name (defaults to the database's default schema)
        hint: Project/repo/topic hint when the exact connection name is unknown
            (e.g. "context-forge" after discussing that project)

    Returns:
        dict with dialect, schemas, tables (name, description, comment,
        column_count, estimated_rows) and views, or a connections list when
        no connection is specified
    """
    from ..datasources import service
    from .context import resolve_org_id

    org_id = await resolve_org_id()
    try:
        ref, list_payload = await _resolve_connection_ref(org_id, connection, hint)
        if list_payload is not None:
            return list_payload
        overview = await service.schema_overview(org_id, ref, schema=schema)
    except Exception as e:  # noqa: BLE001
        logger.error("db_schema failed: %s", e)
        return {"status": "error", "error": str(e)}
    return {"status": "ok", **overview}


@mcp.tool()
async def db_describe(
    table: str,
    connection: Optional[str] = None,
    schema: Optional[str] = None,
    sample_rows: int = 0,
    hint: Optional[str] = None,
) -> dict:
    """Describe a table of an external database in depth.

    Deep context: columns (type, nullable, default, comment, curated
    description), primary key, foreign keys, indexes, unique constraints, and
    estimated row count. Optionally includes a few sample rows.

    Args:
        connection: Connection name (from db_list), or omit and use hint
        table: Table name (from db_schema)
        schema: Optional schema name (defaults to the database's default schema)
        sample_rows: Include up to N sample rows (0-10, default 0). Sample data
            may contain real user data — request it only when needed.
        hint: Project/repo hint when connection name is inferred from context

    Returns:
        dict with columns, primary_key, foreign_keys, indexes,
        unique_constraints, estimated_rows, and optional sample_rows
    """
    from ..datasources import service
    from .context import resolve_org_id

    org_id = await resolve_org_id()
    try:
        ref, list_payload = await _resolve_connection_ref(org_id, connection, hint)
        if list_payload is not None:
            return list_payload
        detail = await service.describe_table(
            org_id, ref, table, schema=schema, sample_rows=sample_rows
        )
    except Exception as e:  # noqa: BLE001
        logger.error("db_describe failed: %s", e)
        return {"status": "error", "error": str(e)}
    return {"status": "ok", **detail}


@mcp.tool()
async def db_query(
    sql: str,
    connection: Optional[str] = None,
    max_rows: int = 100,
    hint: Optional[str] = None,
) -> dict:
    """Run a read-only SQL query against an external database connection.

    Only a single SELECT / WITH...SELECT / SHOW / DESCRIBE / EXPLAIN statement
    is allowed; mutating statements are rejected. A LIMIT is enforced
    server-side and queries time out after a few seconds. Every query is
    written to an audit log. Use db_schema/db_describe first so the SQL matches
    the real schema.

    Args:
        connection: Connection name (from db_list), or omit and use hint
        sql: The read-only SQL statement to execute
        max_rows: Maximum rows to return (default 100, hard cap 500)
        hint: Project/repo hint when connection name is inferred from context

    Returns:
        dict with the executed sql, columns, rows, row_count, truncated flag,
        and duration_ms
    """
    from ..datasources import service
    from ..datasources.validator import QueryValidationError
    from .context import resolve_org_id

    org_id = await resolve_org_id()
    try:
        ref, list_payload = await _resolve_connection_ref(org_id, connection, hint)
        if list_payload is not None:
            return list_payload
        result = await service.run_query(org_id, ref, sql, max_rows=max_rows, source="mcp")
    except QueryValidationError as e:
        return {"status": "error", "error": f"Query rejected: {e}"}
    except Exception as e:  # noqa: BLE001
        logger.error("db_query failed: %s", e)
        return {"status": "error", "error": str(e)}
    return {"status": "ok", **result}
