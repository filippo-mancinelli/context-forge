"""Selezione del progetto attivo per sessione MCP (endpoint org-level).

Sull'endpoint ``/mcp/{org}`` il progetto non è nel path: il client lo sceglie
con il tool ``use_project`` e la scelta va ricordata tra una chiamata e l'altra
della stessa sessione. FastMCP (streamable-http) identifica la sessione con
l'header ``Mcp-Session-Id``.

La mappa è in-memory nel processo: vincola l'endpoint org-level a un singolo
worker (o a sticky-session). Per il deploy attuale (container singolo) è
sufficiente; scalando su più worker questa selezione va spostata su uno store
condiviso (es. Redis).
"""
from __future__ import annotations

from typing import Optional

SESSION_ID_HEADER = "mcp-session-id"

_selected_project: dict[str, int] = {}


def set_selected_project(session_id: str, project_id: int) -> None:
    if session_id:
        _selected_project[session_id] = project_id


def get_selected_project(session_id: Optional[str]) -> Optional[int]:
    if not session_id:
        return None
    return _selected_project.get(session_id)


def clear_session(session_id: Optional[str]) -> None:
    if session_id:
        _selected_project.pop(session_id, None)
