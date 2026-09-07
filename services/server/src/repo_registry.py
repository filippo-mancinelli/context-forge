"""Registrazione di un repository nell'organizzazione e nel progetto attivo.

La stessa operazione serve alla route REST e al tool MCP: vive qui perché non
resti duplicata in due punti che possono divergere.
"""
from __future__ import annotations

from typing import Optional

from .db import get_pool
from .indexer.indexer import sync_repos_config
from .org_config import get_org_config, persist_org_config


class RepoAlreadyExistsError(Exception):
    """Il nome del repository è già usato nell'organizzazione."""


async def add_repo(
    org_id: int,
    project_id: int,
    name: str,
    type: str,
    url: Optional[str] = None,
    path: Optional[str] = None,
    branch: str = "main",
    language: Optional[str] = None,
) -> dict:
    """Registra un repository e lo lega al progetto indicato."""
    from .config import RepoConfig

    cfg = await get_org_config(org_id)
    # I nomi dei repository sono unici nell'organizzazione (riusabili tra org).
    if any(r.name == name for r in cfg.repos):
        raise RepoAlreadyExistsError(f"Repository '{name}' already exists")

    cfg.repos.append(
        RepoConfig(
            name=name,
            type=type,
            url=url,
            path=path,
            branch=branch,
            language=language or "auto",
        )
    )
    await persist_org_config(org_id, cfg)
    await sync_repos_config(org_id)

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE repos SET project_id=$1 WHERE org_id=$2 AND name=$3",
            project_id,
            org_id,
            name,
        )

    return {"name": name, "type": type, "branch": branch, "language": language or "auto"}


async def queue_index(org_id: int, project_id: int, repo_name: Optional[str]) -> None:
    """Accoda una richiesta di indicizzazione (None = tutti i repo del progetto)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO index_requests (org_id, project_id, repo_name) VALUES ($1, $2, $3)",
            org_id,
            project_id,
            repo_name,
        )
