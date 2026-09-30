"""Regole di nome e di percorso condivise dal catalogo."""
from __future__ import annotations

import posixpath


def machine_name(username: str, host: str, port: int = 22) -> str:
    """Nome proposto per una macchina: ``utenza@host``, con la porta se non è 22."""
    name = f"{username}@{host}"
    return name if int(port or 22) == 22 else f"{name}:{port}"


def unique_name(base: str, taken_lower: set[str]) -> str:
    """``base``, oppure la prima variante ``base-N`` non ancora usata (senza maiuscole)."""
    if base.lower() not in taken_lower:
        return base
    suffix = 2
    while f"{base}-{suffix}".lower() in taken_lower:
        suffix += 1
    return f"{base}-{suffix}"


def normalize_root(path: str) -> str:
    """Radice di una cartella in forma canonica, per riconoscere i doppioni."""
    return posixpath.normpath(path.strip())
