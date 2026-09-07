"""Link a una pagina della UI, per indirizzare l'utente dove serve la sua mano.

Un tool MCP crea una fonte senza credenziali: il segreto lo inserisce una
persona dalla UI, e la risposta del tool deve dire dove.
"""
from __future__ import annotations

from typing import Optional

from ..config import get_settings


def ui_link(path: str) -> Optional[str]:
    """URL assoluto di una pagina UI, o None se l'indirizzo non è configurato."""
    base = (get_settings().ui_base_url or "").rstrip("/")
    if not base:
        return None
    return f"{base}/{path.lstrip('/')}"
