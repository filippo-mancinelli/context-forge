"""Tool MCP per consultare il catalogo dell'organizzazione e sceglierne le risorse.

Il catalogo elenca cartelle SSH, database e repository censiti per l'organizzazione;
i tool del progetto vedono solo quelli selezionati. Selezionare richiede il ruolo
member sul progetto e, per una risorsa riservata, un admin dell'organizzazione.
Nessuna credenziale passa da qui.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from ..catalog import repos as repo_catalog
from ..catalog import selections
from ..datasources import project_scopes
from ..datasources import service as db_service
from ..datasources.scopes import ScopeShapeError, default_scope
from ..ssh_sources import service as ssh_service
from .context import get_current_user_id, require_project_id, resolve_org_id
from .permissions import PermissionDenied, requires_permission
from .project_access import selection_rights
from .server import mcp

CATALOG_KINDS = ("folders", "databases", "repos")


def _error(message: str) -> dict:
    return {"status": "error", "error": message}


def _folder_item(source: dict, selected: set[int]) -> dict:
    return {
        "name": source["name"],
        "machine": source["machine_name"],
        "root_path": source["root_path"],
        "description": source.get("description"),
        "restricted": source["restricted"],
        "selected": source["id"] in selected,
    }


def _database_item(connection: dict, selected: set[int], linked: dict[int, list[dict]]) -> dict:
    scopes = linked.get(connection["id"], [])
    return {
        "name": connection["name"],
        "engine": connection["engine"],
        "host": connection.get("host"),
        "database": connection.get("database_name"),
        "via_machine": connection.get("ssh_machine_name"),
        "description": connection.get("description"),
        "restricted": connection["restricted"],
        "selected": connection["id"] in selected or bool(scopes),
        # Perimetri con cui il progetto usa questa connessione.
        "scopes": [
            {"alias": s["alias"], "scope": s["scope_label"], "inferred": s["scope_inferred"]}
            for s in scopes
        ],
    }


def _scope_view(scope: dict) -> dict:
    return {
        "scope_id": scope["scope_id"],
        "alias": scope["alias"],
        "scope": scope["scope_label"],
        "inferred": scope["scope_inferred"],
    }


def _repo_item(repo: dict, selected: set[int]) -> dict:
    return {
        "name": repo["name"],
        "type": repo["type"],
        "url": repo.get("url") or repo.get("path"),
        "branch": repo.get("branch"),
        "status": repo.get("status"),
        "description": repo.get("description"),
        "restricted": repo["restricted"],
        "selected": repo["id"] in selected,
    }


@mcp.tool()
@requires_permission("context-read")
async def catalog_list(kind: Optional[str] = None) -> dict:
    """List the organization's catalog of SSH folders, databases and repositories.

    Each entry says whether the active project already selected it. Tools such
    as ssh_read_file, db_query or repo_search only reach selected resources: when
    the one you need is listed with selected=false, add it with resource_select.
    A database is a server connection: its `scopes` are the database/schema
    pairs the project already uses, each with the alias to pass to the db_*
    tools. To use another database or schema of the same server, call
    resource_select with database and schema.

    Args:
        kind: 'folders', 'databases' or 'repos'. Omit to list all.

    Returns:
        dict with folders, databases and/or repos, each entry with name,
        description, restricted and selected; databases also carry scopes.
    """
    if kind is not None and kind not in CATALOG_KINDS:
        return _error(f"kind must be one of {', '.join(CATALOG_KINDS)}")
    org_id = await resolve_org_id()
    project_id = await require_project_id()
    out: dict = {"status": "ok"}
    if kind in (None, "folders"):
        selected = await selections.selected_ids(project_id, "folders")
        out["folders"] = [_folder_item(s, selected) for s in await ssh_service.list_catalog(org_id)]
    if kind in (None, "databases"):
        selected = await selections.selected_ids(project_id, "databases")
        linked: dict[int, list[dict]] = defaultdict(list)
        for scope in await db_service.list_connections(org_id, project_id):
            linked[scope["id"]].append(scope)
        out["databases"] = [
            _database_item(c, selected, linked)
            for c in await db_service.list_catalog_connections(org_id)
        ]
    if kind in (None, "repos"):
        selected = await selections.selected_ids(project_id, "repos")
        out["repos"] = [_repo_item(r, selected) for r in await repo_catalog.list_catalog(org_id)]
    return out


@mcp.tool()
@requires_permission("context-write")
async def resource_select(
    kind: str,
    name: str,
    database: Optional[str] = None,
    schema: Optional[str] = None,
    alias: Optional[str] = None,
) -> dict:
    """Add a catalog resource to the active project, so its tools can use it.

    Requires the member role on the project. Restricted resources can only be
    added by an organization admin. For kind 'databases' the project links a
    scope of the connection: pass database (and schema on PostgreSQL) to choose
    it, otherwise the connection's default database is used. The same
    connection can be linked on several scopes, each with its own alias.

    Args:
        kind: 'folders', 'databases' or 'repos'.
        name: resource name, as shown by catalog_list.
        database: databases only: database of the scope.
        schema: databases only, PostgreSQL: schema of the scope (default public).
        alias: databases only: name the db_* tools will use for this scope
            (default: the connection name, or name/scope when already taken).

    Returns:
        dict with the selected resource and whether it was already selected;
        for databases also the scope id, alias, scope and inferred flag.
    """
    org_id = await resolve_org_id()
    project_id = await require_project_id()
    can_select, can_select_restricted = await selection_rights(org_id, project_id)
    if not can_select:
        raise PermissionDenied("Selecting catalog resources requires the member role on this project")
    if kind != "databases" and (database or schema or alias):
        return _error("database, schema and alias apply only to kind 'databases'")
    try:
        resource = await selections.find_resource_by_name(org_id, kind, name)
        if resource is None:
            return _error(f"'{name}' is not in the {kind} catalog")
        if kind == "databases":
            return await _select_database_scope(
                org_id, project_id, resource, database, schema, alias, can_select_restricted
            )
        result = await selections.select_resource(
            org_id, project_id, kind, resource["id"], get_current_user_id(), can_select_restricted
        )
    except selections.UnknownKindError as exc:
        return _error(str(exc))
    except selections.RestrictedResourceError:
        raise PermissionDenied(f"'{name}' is restricted: only an organization admin can add it to a project")
    return {"status": "ok", **result}


async def _select_database_scope(
    org_id: int,
    project_id: int,
    resource: dict,
    database: Optional[str],
    schema: Optional[str],
    alias: Optional[str],
    can_select_restricted: bool,
) -> dict:
    """Collega un perimetro della connessione al progetto. Senza database né
    schema il perimetro è quello predefinito della connessione, marcato come
    dedotto perché nessuno l'ha scelto; un perimetro già collegato non è un
    errore."""
    inferred = database is None and schema is None
    if inferred:
        connection = await db_service.get_catalog_connection(org_id, resource["id"])
        try:
            database, schema = default_scope(connection)
        except ScopeShapeError as exc:
            return _error(f"{exc}: pass database (and schema on PostgreSQL)")
    base = {"status": "ok", "kind": "databases", "name": resource["name"]}
    try:
        await project_scopes.create_scope(
            org_id, project_id, resource["id"], database, schema, alias,
            get_current_user_id(), can_select_restricted, inferred=inferred,
        )
    except project_scopes.ScopeConflictError as exc:
        existing = await db_service.find_project_scope(
            org_id, project_id, resource["id"], database, schema
        )
        if existing is None:
            return _error(str(exc))
        return {**base, "already_selected": True, **_scope_view(existing)}
    except ScopeShapeError as exc:
        return _error(str(exc))
    except selections.RestrictedResourceError:
        raise PermissionDenied(
            f"'{resource['name']}' is restricted: only an organization admin can add it to a project"
        )
    created = await db_service.find_project_scope(
        org_id, project_id, resource["id"], database, schema
    )
    return {**base, "already_selected": False, **(_scope_view(created) if created else {})}


@mcp.tool()
@requires_permission("context-write")
async def resource_deselect(kind: str, name: str) -> dict:
    """Remove a catalog resource from the active project. The resource stays in the catalog.

    Args:
        kind: 'folders', 'databases' or 'repos'.
        name: resource name, as shown by catalog_list.
    """
    org_id = await resolve_org_id()
    project_id = await require_project_id()
    can_select, _ = await selection_rights(org_id, project_id)
    if not can_select:
        raise PermissionDenied("Changing the project's resources requires the member role on this project")
    try:
        resource = await selections.find_resource_by_name(org_id, kind, name)
        if resource is None:
            return _error(f"'{name}' is not in the {kind} catalog")
        removed = await selections.deselect_resource(org_id, project_id, kind, resource["id"])
    except selections.UnknownKindError as exc:
        return _error(str(exc))
    if not removed:
        return _error(f"'{name}' is not selected in this project")
    return {"status": "ok", "kind": kind, "name": resource["name"]}
