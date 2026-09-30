"""MCP tools for external database connections (schema context + read-only queries)."""
from __future__ import annotations

import logging
from typing import Optional

from ..catalog import machines, selections
from ..datasources import project_scopes, service
from .server import mcp
from .permissions import PermissionDenied, requires_permission
from .project_access import selection_rights
from .source_links import ui_link

logger = logging.getLogger(__name__)


def _slim_connections(connections: list[dict]) -> list[dict]:
    """Collegamenti del progetto come li vede un agente: l'alias è il nome da
    passare ai tool, il perimetro dice che cosa quell'alias può leggere."""
    return [
        {
            "alias": c.get("alias") or c["name"],
            "name": c["name"],
            "engine": c["engine"],
            "host": c.get("host"),
            "database": c.get("scope_database") or c.get("database_name"),
            "scope": c.get("scope_label"),
            "inferred": c.get("scope_inferred"),
            "description": c.get("description"),
            "status": c.get("status"),
            "annotation_count": c.get("annotation_count", 0),
        }
        for c in connections
    ]


async def _resolve_connection_ref(
    org_id: int,
    project_id: int,
    connection: Optional[str],
    hint: Optional[str] = None,
) -> tuple[Optional[str], Optional[dict]]:
    """Return (connection_name, list_payload).

    When list_payload is set the caller should return it instead of querying schema.
    """
    from ..datasources import service
    from ..datasources.service import ConnectionAmbiguousError, ConnectionNotFoundError

    if not connection and not hint:
        connections = await service.list_connections(org_id, project_id)
        return None, {"status": "ok", "connections": _slim_connections(connections), "count": len(connections)}

    if connection and service.is_list_sentinel(connection):
        connections = await service.list_connections(org_id, project_id)
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
            record = await service.get_scope(org_id, project_id, connection)
            # L'alias identifica un solo perimetro anche quando il progetto
            # collega la stessa connessione più volte; il nome no.
            return record["alias"], None
        except ConnectionNotFoundError:
            resolve_hint = connection

    try:
        record = await service.resolve_connection(org_id, project_id, resolve_hint)
    except ConnectionAmbiguousError as e:
        connections = await service.list_connections(org_id, project_id)
        return None, {
            "status": "error",
            "error": str(e),
            "connections": _slim_connections(connections),
        }
    except ConnectionNotFoundError as e:
        connections = await service.list_connections(org_id, project_id)
        payload: dict = {"status": "error", "error": str(e)}
        if connections:
            payload["connections"] = _slim_connections(connections)
        return None, payload

    return record["alias"], None


@mcp.tool()
@requires_permission("context-read")
async def db_list() -> dict:
    """List the database scopes available to the current project.

    Each entry is a scope: a connection of the organization catalog seen
    through one database and schema. Pass its alias as `connection` to
    db_schema, db_describe, db_query and db_execute. `scope` is what the alias
    can read; `inferred` true means the scope was deduced from the connection
    and nobody confirmed it yet. To read another database or schema of the same
    server, add a scope with catalog_list and resource_select.

    Returns:
        dict with a list of connections (alias, name, engine, host, database,
        scope, inferred, description, status, annotation_count)
    """
    from ..datasources import service
    from .context import resolve_org_id, require_project_id

    org_id = await resolve_org_id()
    project_id = await require_project_id()
    try:
        connections = await service.list_connections(org_id, project_id)
    except Exception as e:  # noqa: BLE001
        logger.error("db_list failed: %s", e)
        return {"status": "error", "error": str(e)}
    slim = _slim_connections(connections)
    return {"status": "ok", "connections": slim, "count": len(slim)}


@mcp.tool()
@requires_permission("context-read")
async def db_schema(
    connection: Optional[str] = None,
    schema: Optional[str] = None,
    hint: Optional[str] = None,
) -> dict:
    """Get the schema overview of an external database of the current project:
    tables, views, row estimates.

    Shallow context: every table with its column count, estimated row count,
    database comment, and human-curated description when available. Use
    db_describe on a specific table for columns, keys, and indexes.

    Args:
        connection: Alias of the scope (from db_list). Omit to list all
            scopes, or pass hint instead when inferring from conversation.
        schema: Optional schema. A confirmed scope accepts only its own schema;
            any other schema is refused with the list of the project's scopes.
        hint: Project/repo/topic hint when the exact scope alias is unknown
            (e.g. "context-forge" after discussing that project)

    Returns:
        dict with dialect, schemas, tables (name, description, comment,
        column_count, estimated_rows) and views, or a connections list when
        no connection is specified
    """
    from ..datasources import service
    from ..datasources.scopes import ScopeViolationError
    from .context import resolve_org_id, require_project_id

    org_id = await resolve_org_id()
    project_id = await require_project_id()
    try:
        ref, list_payload = await _resolve_connection_ref(org_id, project_id, connection, hint)
        if list_payload is not None:
            return list_payload
        overview = await service.schema_overview(org_id, project_id, ref, schema=schema)
    except ScopeViolationError as e:
        # Un perimetro fuori portata è un rifiuto normale e ben formato, non
        # un errore imprevisto: l'agente riceve il messaggio, il log resta muto.
        return {"status": "error", "error": str(e)}
    except Exception as e:  # noqa: BLE001
        logger.error("db_schema failed: %s", e)
        return {"status": "error", "error": str(e)}
    return {"status": "ok", **overview}


@mcp.tool()
@requires_permission("context-read")
async def db_describe(
    table: str,
    connection: Optional[str] = None,
    schema: Optional[str] = None,
    sample_rows: int = 0,
    hint: Optional[str] = None,
) -> dict:
    """Describe a table of an external database of the current project in depth.

    Deep context: columns (type, nullable, default, comment, curated
    description), primary key, foreign keys, indexes, unique constraints, and
    estimated row count. Optionally includes a few sample rows.

    Args:
        connection: Alias of the scope (from db_list), or omit and use hint
        table: Table name (from db_schema)
        schema: Optional schema. A confirmed scope accepts only its own schema;
            any other schema is refused with the list of the project's scopes.
        sample_rows: Include up to N sample rows (0-10, default 0). Sample data
            may contain real user data — request it only when needed.
        hint: Project/repo hint when the scope alias is inferred from context

    Returns:
        dict with columns, primary_key, foreign_keys, indexes,
        unique_constraints, estimated_rows, and optional sample_rows
    """
    from ..datasources import service
    from ..datasources.scopes import ScopeViolationError
    from .context import resolve_org_id, require_project_id

    org_id = await resolve_org_id()
    project_id = await require_project_id()
    try:
        ref, list_payload = await _resolve_connection_ref(org_id, project_id, connection, hint)
        if list_payload is not None:
            return list_payload
        detail = await service.describe_table(
            org_id, project_id, ref, table, schema=schema, sample_rows=sample_rows
        )
    except ScopeViolationError as e:
        # Un perimetro fuori portata è un rifiuto normale e ben formato, non
        # un errore imprevisto: l'agente riceve il messaggio, il log resta muto.
        return {"status": "error", "error": str(e)}
    except Exception as e:  # noqa: BLE001
        logger.error("db_describe failed: %s", e)
        return {"status": "error", "error": str(e)}
    return {"status": "ok", **detail}


@mcp.tool()
@requires_permission("db-query")
async def db_query(
    sql: str,
    connection: Optional[str] = None,
    max_rows: int = 100,
    hint: Optional[str] = None,
) -> dict:
    """Run a read-only SQL query against an external database connection of
    the current project.

    Only a single SELECT / WITH...SELECT / SHOW / DESCRIBE / EXPLAIN statement
    is allowed; mutating statements are rejected. A LIMIT is enforced
    server-side and queries time out after a few seconds. Every query is
    written to an audit log. Use db_schema/db_describe first so the SQL matches
    the real schema.

    Args:
        connection: Alias of the scope (from db_list), or omit and use hint
        sql: The read-only SQL statement to execute
        max_rows: Maximum rows to return (default 100, hard cap 500)
        hint: Project/repo hint when the scope alias is inferred from context

    Returns:
        dict with the executed sql, columns, rows, row_count, truncated flag,
        and duration_ms
    """
    from ..datasources import service
    from ..datasources.scopes import ScopeViolationError
    from ..datasources.validator import QueryValidationError
    from .context import resolve_org_id, require_project_id

    org_id = await resolve_org_id()
    project_id = await require_project_id()
    try:
        ref, list_payload = await _resolve_connection_ref(org_id, project_id, connection, hint)
        if list_payload is not None:
            return list_payload
        result = await service.run_query(org_id, project_id, ref, sql, max_rows=max_rows, source="mcp")
    except ScopeViolationError as e:
        # Un perimetro fuori portata è un rifiuto normale e ben formato, non
        # un errore imprevisto: l'agente riceve il messaggio, il log resta muto.
        return {"status": "error", "error": str(e)}
    except QueryValidationError as e:
        return {"status": "error", "error": f"Query rejected: {e}"}
    except Exception as e:  # noqa: BLE001
        logger.error("db_query failed: %s", e)
        return {"status": "error", "error": str(e)}
    return {"status": "ok", **result}


@mcp.tool()
@requires_permission("db-write", alternatives=("context-write",))
async def db_execute(connection: str, sql: str, reason: str = "") -> dict:
    """Execute a single INSERT, UPDATE or DELETE on a project datasource.

    Guarded: one DML statement only, UPDATE/DELETE must have a WHERE clause,
    DDL is rejected. Runs in its own transaction and returns the affected
    row count (no result set).

    Without the db-write permission the statement is not executed: it is stored
    as a write request with an EXPLAIN preview for an organization admin to
    approve. Poll the outcome with write_request_status.

    Args:
        connection: alias or id of the scope (from db_list).
        sql: the DML statement to execute.
        reason: why the change is needed; shown to the approver.
    """
    from ..datasources.scopes import ScopeViolationError
    from ..datasources.validator import QueryValidationError
    from .context import resolve_org_id, require_project_id
    from .permissions import has_permission

    org_id = await resolve_org_id()
    project_id = await require_project_id()
    if not has_permission("db-write"):
        return await _propose_db_execute(org_id, project_id, connection, sql, reason)
    try:
        # Il riferimento passa dalla stessa risoluzione delle letture: una
        # scrittura non deve finire su un perimetro diverso da quello nominato.
        ref, list_payload = await _resolve_connection_ref(org_id, project_id, connection)
        if list_payload is not None:
            return list_payload
        return await service.run_write(org_id, project_id, ref, sql, source="mcp")
    except ScopeViolationError as e:
        # Un perimetro fuori portata è un rifiuto normale e ben formato, non
        # un errore imprevisto: l'agente riceve il messaggio, il log resta muto.
        return {"status": "error", "error": str(e)}
    except QueryValidationError as e:
        return {"status": "error", "error": f"Query rejected: {e}"}
    except Exception as e:  # noqa: BLE001
        logger.error("db_execute failed: %s", e)
        return {"status": "error", "error": str(e)}


async def _propose_db_execute(
    org_id: int, project_id: int, connection: str, sql: str, reason: str
) -> dict:
    """Store the statement as a pending write request instead of running it.

    The scope is resolved first, as on the direct path, and the statement is
    validated against its schema; the request targets the scope alias, so the
    approved write runs on the scope it was proposed for.
    """
    from .. import write_previews, write_requests
    from ..datasources.validator import (
        QueryValidationError,
        ScopeReferenceError,
        validate_write_query,
    )
    from .approvals import current_requester, pending_response
    from .audit import scrub_text

    try:
        alias, list_payload = await _resolve_connection_ref(org_id, project_id, connection)
        if list_payload is not None:
            return list_payload
        record = await service.get_scope(org_id, project_id, alias)
    except Exception as e:  # noqa: BLE001
        logger.error("db_execute proposal failed: %s", e)
        return {"status": "error", "error": str(e)}
    try:
        validated = validate_write_query(sql, allowed_schema=service._effective_scope(record)[1])
    except ScopeReferenceError as e:
        refused = await service._scope_violation(org_id, project_id, record, str(e))
        return {"status": "error", "error": str(refused)}
    except QueryValidationError as e:
        return {"status": "error", "error": f"Query rejected: {e}"}
    try:
        preview = await write_previews.sql_preview(org_id, project_id, record["alias"], validated)
    except Exception as e:  # noqa: BLE001
        logger.error("db_execute proposal failed: %s", e)
        return {"status": "error", "error": str(e)}
    if preview.get("explain_error"):
        preview["explain_error"] = scrub_text(preview["explain_error"])

    kind, requester_id, label = current_requester()
    created = await write_requests.create(
        org_id=org_id,
        project_id=project_id,
        kind="db_execute",
        # The alias is the project's unique handle for the scope.
        target=record["alias"],
        payload={"sql": validated},
        preview=preview,
        reason=reason,
        requested_by_kind=kind,
        requested_by_id=requester_id,
        requested_by=label,
    )
    return pending_response(created["id"], preview)


@mcp.tool()
@requires_permission("sources-write")
async def db_add(
    name: str,
    engine: str,
    host: Optional[str] = None,
    port: Optional[int] = None,
    database_name: Optional[str] = None,
    schema: Optional[str] = None,
    username: Optional[str] = None,
    description: Optional[str] = None,
    ssh_machine: Optional[str] = None,
) -> dict:
    """Register a database server in the organization catalog and link one of its scopes to the active project.

    A new connection has no password ('pending_secret'): a person adds it from
    the Catalog page before it can be queried. When a connection to the same
    server already exists through the same route (machine, host, port, username
    and engine), it is reused and only the scope is linked, so a second database
    or schema of the same server does not create a second catalog entry. This
    tool takes no credential.

    Args:
        name: connection name, unique in the organization (used when a new
            connection is created).
        engine: database engine: postgresql, mysql, mariadb or sqlite.
        host: database host as reachable from the server, or from the machine.
        port: database port.
        database_name: database of the scope; for a new connection also its
            default database (the file path for sqlite).
        schema: PostgreSQL only: schema of the scope (default public).
        username: user the connection authenticates as.
        description: optional free-text description.
        ssh_machine: name of a catalog machine to tunnel through.

    Returns:
        dict with the connection, whether it was created, and the linked
        scope (alias, scope, inferred).
    """
    from .context import get_current_user_id, require_project_id, resolve_org_id

    org_id = await resolve_org_id()
    project_id = await require_project_id()
    # Registrare equivale a selezionare: valgono gli stessi diritti di resource_select.
    can_select, can_select_restricted = await selection_rights(org_id, project_id)
    if not can_select:
        raise PermissionDenied("Adding a database to this project requires the member role on this project")
    try:
        machine_id = None
        if ssh_machine:
            machine = await machines.get_machine_by_name(org_id, ssh_machine)
            if machine is None:
                return {"status": "error", "error": f"Machine '{ssh_machine}' is not in the catalog"}
            machine_id = machine["id"]
        connection = await service.find_connection(
            org_id, machine_id, host, port, username, engine
        )
        created = connection is None
        if created:
            connection = await service.create_connection(org_id, {
                "name": name,
                "engine": engine,
                "host": host,
                "port": port,
                "database_name": database_name,
                "username": username,
                "description": description,
                "options": {},
                "ssh_machine_id": machine_id,
            })
            await service.mark_pending_secret(org_id, connection["id"])
            connection = {**connection, "status": "pending_secret"}
        # Su SQLite il file è la connessione: il perimetro resta vuoto.
        on_file = (connection.get("engine") or engine) == "sqlite"
        scope_database = None if on_file else (database_name or connection.get("database_name"))
        scope_schema = None if on_file else schema
        scope = await service.find_project_scope(
            org_id, project_id, connection["id"], scope_database, scope_schema
        )
        if scope is None:
            await project_scopes.create_scope(
                org_id, project_id, connection["id"], scope_database, scope_schema, None,
                get_current_user_id(), can_select_restricted,
            )
            scope = await service.find_project_scope(
                org_id, project_id, connection["id"], scope_database, scope_schema
            )
    except selections.RestrictedResourceError as exc:
        raise PermissionDenied(f"'{exc}' is restricted: only an organization admin can add it to a project")
    except ValueError as exc:
        return {"status": "error", "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — vincoli di unicità, perimetro in conflitto, driver
        return {"status": "error", "error": str(exc)}

    result = {"status": "ok", "created": created, "connection": connection}
    if scope is not None:
        result["scope"] = {
            "alias": scope["alias"], "scope": scope["scope_label"], "inferred": scope["scope_inferred"],
        }
    if created:
        result["next_step"] = (
            f"Connection '{name}' has no password yet. Add it from the Catalog page "
            "in the web UI; queries fail until then."
        )
        result["ui_url"] = ui_link("/catalog")
    return result
