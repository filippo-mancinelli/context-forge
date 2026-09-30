"""Tool MCP per consultare il catalogo dell'organizzazione e sceglierne le risorse.

Il catalogo elenca cartelle SSH, database e repository censiti per l'organizzazione;
i tool del progetto vedono solo quelli selezionati. Selezionare richiede il ruolo
member sul progetto e, per una risorsa riservata, un admin dell'organizzazione.
Nessuna credenziale passa da qui.
"""
from __future__ import annotations

from typing import Optional

from ..catalog import repos as repo_catalog
from ..catalog import selections
from ..datasources import service as db_service
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


def _database_item(connection: dict, selected: set[int]) -> dict:
    return {
        "name": connection["name"],
        "engine": connection["engine"],
        "host": connection.get("host"),
        "database": connection.get("database_name"),
        "via_machine": connection.get("ssh_machine_name"),
        "description": connection.get("description"),
        "restricted": connection["restricted"],
        "selected": connection["id"] in selected,
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

    Args:
        kind: 'folders', 'databases' or 'repos'. Omit to list all.

    Returns:
        dict with folders, databases and/or repos, each entry with name,
        description, restricted and selected.
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
        out["databases"] = [
            _database_item(c, selected) for c in await db_service.list_catalog_connections(org_id)
        ]
    if kind in (None, "repos"):
        selected = await selections.selected_ids(project_id, "repos")
        out["repos"] = [_repo_item(r, selected) for r in await repo_catalog.list_catalog(org_id)]
    return out


@mcp.tool()
@requires_permission("context-write")
async def resource_select(kind: str, name: str) -> dict:
    """Add a catalog resource to the active project, so its tools can use it.

    Requires the member role on the project. Restricted resources can only be
    added by an organization admin.

    Args:
        kind: 'folders', 'databases' or 'repos'.
        name: resource name, as shown by catalog_list.

    Returns:
        dict with the selected resource and whether it was already selected.
    """
    org_id = await resolve_org_id()
    project_id = await require_project_id()
    can_select, can_select_restricted = await selection_rights(org_id, project_id)
    if not can_select:
        raise PermissionDenied("Selecting catalog resources requires the member role on this project")
    try:
        resource = await selections.find_resource_by_name(org_id, kind, name)
        if resource is None:
            return _error(f"'{name}' is not in the {kind} catalog")
        result = await selections.select_resource(
            org_id, project_id, kind, resource["id"], get_current_user_id(), can_select_restricted
        )
    except selections.UnknownKindError as exc:
        return _error(str(exc))
    except selections.RestrictedResourceError:
        raise PermissionDenied(f"'{name}' is restricted: only an organization admin can add it to a project")
    return {"status": "ok", **result}


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
