"""Risoluzione dei progetti accessibili all'identità MCP corrente.

Condiviso fra i tool di selezione (``project_tools``) e quelli di gestione
(``project_admin``): entrambi devono partire dallo stesso elenco, altrimenti si
potrebbe rinominare o cancellare un progetto che non si è autorizzati a vedere.
"""
from __future__ import annotations

from typing import Optional

from .context import (
    get_current_allowed_projects,
    get_current_org_id,
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


async def resolve_accessible(project: str) -> Optional[dict]:
    """Progetto accessibile con questo id o slug, altrimenti None."""
    for candidate in await accessible_projects():
        if str(candidate["id"]) == str(project) or candidate["slug"] == project:
            return candidate
    return None
