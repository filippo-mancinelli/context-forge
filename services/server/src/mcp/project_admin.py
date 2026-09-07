"""Tool MCP per il ciclo di vita dei progetti sull'endpoint org-level.

Creare, rinominare, cancellare un progetto e abilitarci un utente sono
operazioni di governance dell'organizzazione: richiedono il permesso
``projects-write`` e un'identità utente (Keycloak). Una API key resta legata ai
progetti su cui è stata emessa e non può gestirne il ciclo di vita, nemmeno se
un admin le assegna il permesso.
"""
from __future__ import annotations

from typing import Optional

from fastmcp import Context

from .. import projects as projects_module
from .. import tenancy
from ..config import get_settings
from .context import (
    get_current_org_id,
    get_current_user_id,
    get_selected_project_id,
    set_current_project_id,
)
from .permissions import requires_permission
from . import project_access
from .server import mcp
from .session_state import set_selected_project

API_KEY_MESSAGE = (
    "Project management requires user authentication (OAuth); API keys cannot "
    "manage projects. Connect with your user account to use this tool."
)


def _error(message: str) -> dict:
    return {"status": "error", "error": message}


def _require_identity() -> Optional[dict]:
    """None se l'identità corrente può gestire progetti, altrimenti l'errore."""
    if get_current_org_id() is None:
        return _error("No organization in context")
    if get_current_user_id() is None:
        return _error(API_KEY_MESSAGE)
    return None


async def _mcp_url(org_id: int, project_slug: str) -> Optional[str]:
    """URL dell'endpoint MCP dedicato al progetto, se il server sa il suo indirizzo pubblico."""
    base = (get_settings().public_mcp_url or "").rstrip("/")
    if not base:
        return None
    org = await tenancy.get_organization(org_id)
    if org is None:
        return None
    return f"{base}/mcp/{org['slug']}/{project_slug}"


@mcp.tool()
@requires_permission("projects-write")
async def create_project(name: str, ctx: Context = None) -> dict:
    """Create a new project in this organization and select it for this session.

    A project is a container: repositories, documents, web sites, databases, SSH
    sources, memory and API keys all belong to exactly one. Requires an
    organization admin (or owner) user account.

    Args:
        name: human-readable project name. The slug is derived from it.

    Returns:
        dict with the created project (id, slug, name) and mcp_url, the
        dedicated MCP endpoint for it.
    """
    denied = _require_identity()
    if denied is not None:
        return denied
    org_id = get_current_org_id()
    try:
        project = await projects_module.create_project(org_id, name)
    except ValueError as exc:
        return _error(str(exc))

    set_current_project_id(project["id"])
    sid = project_access.session_id(ctx)
    if sid:
        set_selected_project(sid, project["id"])

    return {
        "status": "ok",
        "project": project_access.public_project(project),
        "mcp_url": await _mcp_url(org_id, project["slug"]),
    }


@mcp.tool()
@requires_permission("projects-write")
async def rename_project(project: str, name: str) -> dict:
    """Rename a project. Its slug and MCP endpoint stay the same.

    Args:
        project: project id or slug, among those you can access.
        name: new human-readable name.

    Returns:
        dict with the updated project, or an error if it is not accessible.
    """
    denied = _require_identity()
    if denied is not None:
        return denied
    match = await project_access.resolve_accessible(project)
    if match is None:
        return _error(f"Project '{project}' not found or not accessible")

    updated = await projects_module.update_project(match["id"], name)
    if updated is None:
        return _error(f"Project '{project}' no longer exists")
    return {
        "status": "ok",
        "project": project_access.public_project({**updated, "role": match.get("role")}),
    }


async def _count_memories(namespace: str) -> Optional[int]:
    """Quante memorie esistono nel namespace del progetto.

    None se mem0 non è raggiungibile: in quel caso non si può affermare che il
    progetto sia vuoto, e la cancellazione va rifiutata.
    """
    from .memory import _get_memory

    try:
        memory = await _get_memory(get_current_org_id())
        result = memory.get_all(user_id=namespace)
        items = result.get("results", result) if isinstance(result, dict) else result
        return len(items or [])
    except Exception:
        return None


@mcp.tool()
@requires_permission("projects-write")
async def delete_project(project: str) -> dict:
    """Delete an empty project. Projects holding resources are never deleted.

    A project can only be removed once it holds no repositories, documents, web
    sites, databases, API contracts, SSH sources, chat sessions, jobs, API keys
    or memories: its resources carry the project id without a foreign key, so
    deleting a populated project would leave unreachable rows behind. The
    organization's default project is never deleted.

    Args:
        project: project id or slug, among those you can access.

    Returns:
        dict confirming the deletion, or an error with `resources` listing what
        still occupies the project.
    """
    denied = _require_identity()
    if denied is not None:
        return denied
    match = await project_access.resolve_accessible(project)
    if match is None:
        return _error(f"Project '{project}' not found or not accessible")
    if match["slug"] == projects_module.DEFAULT_PROJECT_SLUG:
        return _error("The default project cannot be deleted")

    resources = await projects_module.count_project_resources(match["id"])
    if resources:
        return {
            **_error(
                f"Project '{match['slug']}' still holds resources; remove them first"
            ),
            "resources": resources,
        }

    memories = await _count_memories(match["memory_namespace"])
    if memories is None:
        return _error(
            "Cannot verify the project's memories right now; deletion refused"
        )
    if memories:
        return {
            **_error(
                f"Project '{match['slug']}' still holds memories; remove them first"
            ),
            "resources": {"memories": memories},
        }

    removed = await projects_module.delete_project(match["id"])
    if not removed:
        return _error(f"Project '{project}' no longer exists")
    return {"status": "ok", "deleted": project_access.public_project(match)}


# Ruoli assegnabili su un progetto: 'owner' esiste solo a livello di organizzazione.
PROJECT_MEMBER_ROLES = ("viewer", "member", "admin")


async def _target_project(project: Optional[str]) -> Optional[dict]:
    """Progetto indicato esplicitamente, oppure quello selezionato in sessione."""
    if project is not None:
        return await project_access.resolve_accessible(project)
    selected = get_selected_project_id()
    if selected is None:
        return None
    return await projects_module.get_project(selected)


@mcp.tool()
@requires_permission("projects-write")
async def add_project_member(
    user: str, role: str = "member", project: Optional[str] = None
) -> dict:
    """Give an organization member access to a project.

    The user must already belong to this organization. Only an organization
    owner picks the project role: for other callers the new member joins as
    'member'.

    Args:
        user: the member's email address, or their numeric user id.
        role: viewer, member or admin. Defaults to member.
        project: project id or slug. Defaults to the project selected in this
            session.

    Returns:
        dict with the user id and the role that was actually granted.
    """
    denied = _require_identity()
    if denied is not None:
        return denied
    if role not in PROJECT_MEMBER_ROLES:
        return _error(f"role must be one of {', '.join(PROJECT_MEMBER_ROLES)}")

    target = await _target_project(project)
    if target is None:
        return _error(
            "No project. Pass one, or select it first with use_project"
            if project is None
            else f"Project '{project}' not found or not accessible"
        )

    org_id = get_current_org_id()
    user_id: Optional[int]
    if user.isdigit():
        user_id = int(user)
    else:
        from ..api.security import get_user_id_by_email

        user_id = await get_user_id_by_email(user)
    if user_id is None:
        return _error(f"User '{user}' not found")

    if await tenancy.get_membership_role(org_id, user_id) is None:
        return _error(f"User '{user}' is not a member of this organization")

    caller_role = await tenancy.get_membership_role(org_id, get_current_user_id())
    effective_role = role if caller_role == "owner" else "member"
    await projects_module.add_project_member(target["id"], user_id, effective_role)

    return {
        "status": "ok",
        "member": {"user_id": user_id, "role": effective_role},
        "project": project_access.public_project(target),
    }
