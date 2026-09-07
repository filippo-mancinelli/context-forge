"""Tool MCP per l'endpoint org-level: elenco e selezione del progetto attivo.

Sull'endpoint ``/mcp/{org}`` il progetto non è nel path. Il client scopre i
progetti a cui ha accesso con ``list_projects`` e ne sceglie uno con
``use_project``; la scelta è ricordata per la sessione MCP corrente e applicata
alle chiamate successive dei tool scoped.
"""
from __future__ import annotations

from fastmcp import Context

from .server import mcp
from .context import get_selected_project_id, set_current_project_id
from . import project_access
from .permissions import audit_only
from .session_state import set_selected_project


@mcp.tool()
@audit_only
async def list_projects(ctx: Context = None) -> dict:
    """List the projects you can access in this organization.

    Use this on the organization-level MCP endpoint to discover which projects
    are available to you, then call use_project to pick one.

    Returns:
        dict with `projects` (id, slug, name, role) and the currently selected
        project id, if any.
    """
    projects = await project_access.accessible_projects()
    # Selezione esplicita di sessione (nessun fallback al progetto default).
    selected = get_selected_project_id()
    return {
        "projects": [project_access.public_project(p) for p in projects],
        "selected_project_id": selected,
    }


@mcp.tool()
@audit_only
async def use_project(project: str, ctx: Context = None) -> dict:
    """Select the active project for subsequent tool calls in this session.

    Args:
        project: project id or slug, among those returned by list_projects.

    Returns:
        dict confirming the selected project, or an error if it is not
        accessible to you.
    """
    match = await project_access.resolve_accessible(project)
    if match is None:
        return {
            "status": "error",
            "error": f"Project '{project}' not found or not accessible",
        }

    set_current_project_id(match["id"])
    sid = project_access.session_id(ctx)
    if sid:
        set_selected_project(sid, match["id"])
    return {"status": "ok", "project": project_access.public_project(match)}


@mcp.tool()
@audit_only
async def current_project(ctx: Context = None) -> dict:
    """Show the project currently selected for this session, if any."""
    selected = get_selected_project_id()
    if selected is None:
        return {"selected_project_id": None}
    from ..projects import get_project

    proj = await get_project(selected)
    return {
        "selected_project_id": selected,
        "project": project_access.public_project(proj) if proj else None,
    }
