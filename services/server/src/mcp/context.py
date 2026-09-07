"""Request-scoped tenant context for MCP tool execution.

The MCP auth middleware resolves the calling key/token to an organization and
stores it here, so tools partition data per tenant without changing their
public signatures. When unset (e.g. single-tenant deployments with auth
disabled) tools fall back to the default organization.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

_current_namespace: ContextVar[Optional[str]] = ContextVar("cf_memory_namespace", default=None)
_current_org_id: ContextVar[Optional[int]] = ContextVar("cf_org_id", default=None)
_current_project_id: ContextVar[Optional[int]] = ContextVar("cf_project_id", default=None)
# Utente autenticato (solo canale OIDC/Keycloak): serve a filtrare i progetti
# accessibili sull'endpoint MCP org-level. None con le API key (impersonali).
_current_user_id: ContextVar[Optional[int]] = ContextVar("cf_user_id", default=None)
# Progetti su cui la API key corrente è abilitata (None = nessun vincolo di key,
# es. autenticazione utente o key org-wide).
_current_allowed_projects: ContextVar[Optional[frozenset]] = ContextVar(
    "cf_allowed_projects", default=None
)


def set_current_namespace(namespace: Optional[str]) -> None:
    _current_namespace.set(namespace)


def get_current_namespace() -> Optional[str]:
    return _current_namespace.get()


def set_current_org_id(org_id: Optional[int]) -> None:
    _current_org_id.set(org_id)


def get_current_org_id() -> Optional[int]:
    return _current_org_id.get()


async def resolve_org_id() -> Optional[int]:
    """Return the context org id, falling back to the default organization."""
    org_id = _current_org_id.get()
    if org_id is not None:
        return org_id
    from ..tenancy import ensure_default_org

    return await ensure_default_org()


def set_current_project_id(project_id: Optional[int]) -> None:
    _current_project_id.set(project_id)


def get_current_project_id() -> Optional[int]:
    return _current_project_id.get()


def _selected_project_for_session() -> Optional[int]:
    """Progetto scelto con ``use_project`` per la sessione MCP corrente
    (endpoint org-level).

    La scelta è salvata per sessione in uno store in-memory. Va riletta qui,
    nel task in cui viene eseguito il tool, tramite l'id di sessione FastMCP:
    un contextvar valorizzato fuori dall'esecuzione del tool (es. middleware
    ASGI) gira in un task diverso e non lo raggiunge.
    """
    try:
        from fastmcp.server.dependencies import get_context

        ctx = get_context()
    except Exception:
        return None
    if ctx is None:
        return None
    try:
        session_id = ctx.session_id
    except Exception:
        return None
    if not session_id:
        return None
    from .session_state import get_selected_project

    selected = get_selected_project(session_id)
    if selected is None:
        return None
    # Una API key vincolata a un sottoinsieme di progetti non può operare fuori
    # da quelli, anche se lo store contenesse una selezione precedente.
    allowed = _current_allowed_projects.get()
    if allowed is not None and selected not in allowed:
        return None
    return selected


def get_selected_project_id() -> Optional[int]:
    """Progetto effettivamente selezionato per la sessione: dal contextvar
    (endpoint scoped) o dallo store di sessione (endpoint org-level, scelta via
    use_project). Nessun fallback al progetto default: riflette solo scelte
    esplicite, così i tool di stato (list_projects, current_project) sono
    coerenti con quanto vedono i tool scoped."""
    project_id = _current_project_id.get()
    if project_id is not None:
        return project_id
    return _selected_project_for_session()


async def resolve_memory_namespace() -> Optional[str]:
    """Namespace di memoria del progetto corrente.

    Sull'endpoint org-level il namespace nel context è quello dell'org: viene
    fissato all'autenticazione, quando ancora nessun progetto è stato scelto.
    La scelta fatta con ``use_project`` vive nello store di sessione, quindi il
    namespace va ricavato da lì al momento dell'uso, altrimenti le memorie di un
    progetto finirebbero nel calderone dell'organizzazione.
    """
    project_id = get_selected_project_id()
    if project_id is not None:
        from ..projects import get_project

        project = await get_project(project_id)
        if project and project.get("memory_namespace"):
            return project["memory_namespace"]
    return get_current_namespace()


async def resolve_project_id() -> Optional[int]:
    """Progetto dal context. Il fallback sul progetto default dell'org esiste
    solo con l'autenticazione MCP disabilitata (dev locale single-tenant):
    con auth attiva la selezione deve essere esplicita (path o use_project)."""
    project_id = _current_project_id.get()
    if project_id is not None:
        return project_id
    # Endpoint org-level: la selezione fatta con use_project vive nello store di
    # sessione, non nel contextvar visibile al task del tool. Rileggila qui.
    selected = _selected_project_for_session()
    if selected is not None:
        return selected
    from ..config import get_settings

    if get_settings().mcp_auth_mode != "disabled":
        return None
    org_id = await resolve_org_id()
    if org_id is None:
        return None
    from ..projects import get_default_project_id

    return await get_default_project_id(org_id)


async def require_project_id() -> int:
    """Progetto corrente per i tool scoped; errore guida se non selezionato."""
    project_id = await resolve_project_id()
    if project_id is None:
        from .permissions import ToolError

        raise ToolError(
            "No project selected: call use_project (org-level endpoint) "
            "or connect to /mcp/{org}/{project}"
        )
    return project_id


def set_current_user_id(user_id: Optional[int]) -> None:
    _current_user_id.set(user_id)


def get_current_user_id() -> Optional[int]:
    return _current_user_id.get()


def set_current_allowed_projects(project_ids: Optional[frozenset]) -> None:
    _current_allowed_projects.set(project_ids)


def get_current_allowed_projects() -> Optional[frozenset]:
    return _current_allowed_projects.get()
