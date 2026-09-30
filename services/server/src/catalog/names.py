"""Regole di nome e di percorso condivise dal catalogo."""
from __future__ import annotations

import posixpath
from typing import Optional
from urllib.parse import urlparse, urlunparse


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


def normalize_repo_url(url: Optional[str]) -> Optional[str]:
    """URL di un repository remoto in forma canonica, per riconoscere i doppioni.

    Schema ``https``, host in minuscolo, senza credenziali, senza ``.git`` e senza
    barra finale. Gli indirizzi che non sono http(s) (file://, ssh) restano come sono.
    """
    if url is None or not url.strip():
        return None
    value = url.strip()
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return value
    netloc = parsed.hostname.lower()
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    path = parsed.path.rstrip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")].rstrip("/")
    return urlunparse(("https", netloc, path, "", "", ""))


def repo_url_name(url: str) -> str:
    """Ultimo segmento del percorso di un URL di repository."""
    return (normalize_repo_url(url) or "").rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")


def repo_name_for(url: str, branch: str, taken_lower: set[str]) -> str:
    """Nome proposto per un repository: l'ultimo segmento dell'URL, con ``@branch``
    se quel nome è già usato, e un suffisso numerico se è usato anche quello."""
    base = repo_url_name(url)
    if base.lower() in taken_lower:
        base = f"{base}@{branch}"
    return unique_name(base, taken_lower)
