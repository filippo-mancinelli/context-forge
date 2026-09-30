"""Risoluzione dei progetti accessibili all'identità MCP corrente e dei diritti
che vi ha.

Condiviso fra i tool di selezione (``project_tools``) e quelli di gestione
(``project_admin``): entrambi devono partire dallo stesso elenco, altrimenti si
potrebbe rinominare o cancellare un progetto che non si è autorizzati a vedere.
Lo stesso vale per chi sceglie una risorsa del catalogo: i tool del catalogo e
quelli che registrano una sorgente calcolano i diritti nello stesso modo.
"""
from __future__ import annotations

from typing import Optional

from .context import (
    get_current_allowed_projects,
    get_current_org_id,
    get_current_principal,
    get_current_user_id,
)


def session_id(ctx) -> Optional[str]:
    """Id della sessione MCP corrente, se il transport lo espone."""
    try:
        return ctx.session_id if ctx is not None else None
    except Exception:
        return None


def public_project(project: dict) -> dict:
    return {
        "id": project["id"],
        "slug": project["slug"],
        "name": project["name"],
        "role": project.get("role"),
    }


async def accessible_projects() -> list[dict]:
    """Progetti su cui l'identità corrente può operare.

    Con autenticazione utente (Keycloak) il filtro è per ruolo + abilitazione;
    con API key è l'elenco dei progetti consentiti sulla key (o tutti, se
    org-wide).
    """
    org_id = get_current_org_id()
    if org_id is None:
        return []
    user_id = get_current_user_id()
    if user_id is not None:
        from ..projects import list_accessible_projects

        return await list_accessible_projects(org_id, user_id)

    # API key: nessuna identità utente. Elenco dei progetti consentiti dalla key
    # (None = org-wide → tutti i progetti dell'org).
    from ..projects import get_project, list_projects

    allowed = get_current_allowed_projects()
    if allowed is None:
        rows = await list_projects(org_id)
        return [{**r, "role": "api-key"} for r in rows]
    out = []
    for pid in allowed:
        proj = await get_project(pid)
        if proj is not None and proj["org_id"] == org_id:
            out.append({**proj, "role": "api-key"})
    return out


async def selection_rights(org_id: int, project_id: int) -> tuple[bool, bool]:
    """(può selezionare, può selezionare risorse riservate) per l'identità corrente.

    Una API key è già vincolata ai progetti su cui è stata emessa: seleziona le
    risorse normali, mai quelle riservate, che richiedono una persona admin.
    """
    from ..projects import resolve_project_access
    from ..tenancy import get_membership_role, role_at_least

    principal = get_current_principal()
    if principal.kind in ("api_key", "anonymous"):
        # An API key is already bound to its projects; anonymous means auth is disabled.
        return True, False
    user_id = get_current_user_id()
    if principal.kind != "user" or user_id is None:
        return False, False
    project_role = await resolve_project_access(org_id, user_id, project_id)
    org_role = await get_membership_role(org_id, user_id)
    return role_at_least(project_role, "member"), role_at_least(org_role, "admin")


async def resolve_accessible(project: str) -> Optional[dict]:
    """Progetto accessibile con questo id o slug, altrimenti None."""
    for candidate in await accessible_projects():
        if str(candidate["id"]) == str(project) or candidate["slug"] == project:
            return candidate
    return None
