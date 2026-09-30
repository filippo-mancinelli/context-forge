"""Repository del catalogo: censiti una volta per organizzazione, indicizzati una
volta e selezionati dai progetti che devono conoscerli.

Il token specifico di un repository è cifrato a riposo e mai restituito: si
espone solo ``has_token``. Il nome è unico nell'organizzazione senza distinzione
di maiuscole, e un repository remoto è unico anche per URL normalizzato e branch.
"""
from __future__ import annotations

import shutil
from typing import Any, Optional

import asyncpg

from ..config import RepoRecord
from ..datasources.secrets import decrypt_secret, encrypt_secret
from ..db import get_pool
from .names import normalize_repo_url, repo_name_for

REPO_TYPES = ("local", "github", "gitlab")

_COLS = (
    "r.id, r.org_id, r.name, r.type, r.url, r.path, r.branch, r.language, r.token_enc, r.description, "
    "r.restricted, r.status, r.last_indexed_at, r.indexed_commit, r.total_chunks, r.error_message"
)
_SELECTED = "JOIN project_repos pr ON pr.repo_id = r.id AND pr.project_id = $2"


class RepoNotFoundError(Exception):
    pass


class RepoNotAvailableError(Exception):
    """Il repository è nel catalogo ma il progetto non l'ha selezionato."""


class RepoConflictError(Exception):
    def __init__(self, existing: dict[str, Any]):
        super().__init__(f"Repository already in the catalog as '{existing['name']}'")
        self.existing = existing


def _to_dict(row: Any, include_token: bool = False) -> dict[str, Any]:
    d = dict(row)
    d["has_token"] = bool(d.get("token_enc"))
    if not include_token:
        d.pop("token_enc", None)
    if d.get("last_indexed_at") is not None and hasattr(d["last_indexed_at"], "isoformat"):
        d["last_indexed_at"] = d["last_indexed_at"].isoformat()
    return d


def to_record(repo: dict[str, Any]) -> RepoRecord:
    """Oggetto usato da indicizzatore e client git, con il token in chiaro."""
    return RepoRecord(
        id=repo["id"],
        org_id=repo["org_id"],
        name=repo["name"],
        type=repo["type"],
        url=repo.get("url"),
        path=repo.get("path"),
        branch=repo.get("branch") or "main",
        language=repo.get("language") or "auto",
        token=decrypt_secret(repo.get("token_enc") or "") or None,
        description=repo.get("description"),
        restricted=bool(repo.get("restricted")),
    )


async def _records(query: str, *args: Any) -> list[RepoRecord]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *args)
    return [to_record(_to_dict(r, include_token=True)) for r in rows]


async def list_catalog(org_id: int) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {_COLS}, (SELECT count(*) FROM project_repos p WHERE p.repo_id = r.id) AS project_count "
            "FROM repos r WHERE r.org_id = $1 ORDER BY lower(r.name)",
            org_id,
        )
    return [_to_dict(r) for r in rows]


async def list_project_repos(org_id: int, project_id: int) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {_COLS} FROM repos r {_SELECTED} WHERE r.org_id = $1 ORDER BY lower(r.name)",
            org_id, project_id,
        )
    return [_to_dict(r) for r in rows]


async def get_repo(org_id: int, repo_id: int, include_token: bool = False) -> Optional[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(f"SELECT {_COLS} FROM repos r WHERE r.org_id = $1 AND r.id = $2", org_id, repo_id)
    return _to_dict(row, include_token=include_token) if row else None


async def get_record(org_id: int, repo_id: int) -> Optional[RepoRecord]:
    repo = await get_repo(org_id, repo_id, include_token=True)
    return to_record(repo) if repo else None


async def find_repo(org_id: int, url: Optional[str], branch: str) -> Optional[dict[str, Any]]:
    """Repository remoto già censito con lo stesso URL normalizzato e lo stesso branch."""
    normalized = normalize_repo_url(url)
    if normalized is None:
        return None
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_COLS} FROM repos r WHERE r.org_id = $1 AND lower(r.url) = lower($2) AND r.branch = $3",
            org_id, normalized, branch or "main",
        )
    return _to_dict(row) if row else None


async def resolve_project_repo(org_id: int, project_id: int, name: str) -> RepoRecord:
    """Repository selezionato dal progetto, cercato per nome senza maiuscole."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_COLS} FROM repos r {_SELECTED} WHERE r.org_id = $1 AND lower(r.name) = lower($3)",
            org_id, project_id, name,
        )
        in_catalog = None
        if row is None:
            in_catalog = await conn.fetchval(
                "SELECT 1 FROM repos WHERE org_id = $1 AND lower(name) = lower($2)", org_id, name
            )
    if row is not None:
        return to_record(_to_dict(row, include_token=True))
    if in_catalog:
        raise RepoNotAvailableError(
            f"Repository '{name}' is not available in this project. "
            "Select it from the catalog first (catalog_list, resource_select)."
        )
    raise RepoNotFoundError(f"Repository '{name}' not found")


async def project_records(org_id: int, project_id: int) -> list[RepoRecord]:
    return await _records(f"SELECT {_COLS} FROM repos r {_SELECTED} WHERE r.org_id = $1", org_id, project_id)


async def selected_records(org_id: int) -> list[RepoRecord]:
    """Repository selezionati da almeno un progetto: gli unici che si aggiornano periodicamente."""
    return await _records(
        f"SELECT {_COLS} FROM repos r WHERE r.org_id = $1 "
        "AND EXISTS (SELECT 1 FROM project_repos p WHERE p.repo_id = r.id)",
        org_id,
    )


async def pending_records(org_id: int) -> list[RepoRecord]:
    return await _records(f"SELECT {_COLS} FROM repos r WHERE r.org_id = $1 AND r.status = 'pending'", org_id)


async def suggest_name(org_id: int, source: str, branch: str) -> str:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT lower(name) AS name FROM repos WHERE org_id = $1", org_id)
    return repo_name_for(source, branch, {r["name"] for r in rows})


def _validate(data: dict[str, Any]) -> dict[str, Any]:
    repo_type = data.get("type") or "local"
    if repo_type not in REPO_TYPES:
        raise ValueError(f"type must be one of {', '.join(REPO_TYPES)}")
    if repo_type == "local":
        path = (data.get("path") or "").strip()
        if not path:
            raise ValueError("A local repository needs a path")
        url = None
    else:
        url = normalize_repo_url(data.get("url"))
        if not url:
            raise ValueError("A remote repository needs a URL")
        path = None
    return {
        "type": repo_type,
        "url": url,
        "path": path,
        "branch": (data.get("branch") or "main").strip(),
        "language": data.get("language") or "auto",
    }


async def _conflict(conn, org_id: int, name: str, url: Optional[str], branch: str, exclude_id: int = 0):
    row = await conn.fetchrow(
        f"SELECT {_COLS} FROM repos r WHERE r.org_id = $1 AND r.id <> $5 AND (lower(r.name) = lower($2) "
        "OR ($3::text IS NOT NULL AND lower(r.url) = lower($3) AND r.branch = $4))",
        org_id, name, url, branch, exclude_id,
    )
    return _to_dict(row) if row else None


async def create_repo(org_id: int, data: dict[str, Any]) -> dict[str, Any]:
    fields = _validate(data)
    name = (data.get("name") or "").strip() or await suggest_name(
        org_id, fields["url"] or fields["path"], fields["branch"]
    )
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await _conflict(conn, org_id, name, fields["url"], fields["branch"])
        if existing:
            raise RepoConflictError(existing)
        try:
            row = await conn.fetchrow(
                f"""
                INSERT INTO repos AS r (org_id, name, type, url, path, branch, language, token_enc,
                                        description, restricted, status)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'pending')
                RETURNING {_COLS}
                """,
                org_id, name, fields["type"], fields["url"], fields["path"], fields["branch"], fields["language"],
                encrypt_secret(data["token"]) if data.get("token") else None,
                data.get("description"), bool(data.get("restricted")),
            )
        except asyncpg.UniqueViolationError:
            existing = await _conflict(conn, org_id, name, fields["url"], fields["branch"])
            raise RepoConflictError(existing or {"name": name})
    return _to_dict(row)


async def update_repo(org_id: int, repo_id: int, data: dict[str, Any]) -> Optional[dict[str, Any]]:
    current = await get_repo(org_id, repo_id, include_token=True)
    if current is None:
        return None
    fields = _validate(data)
    name = (data.get("name") or "").strip() or current["name"]
    # Token vuoto in modifica = mantieni quello memorizzato.
    token_enc = encrypt_secret(data["token"]) if data.get("token") else current.get("token_enc")
    source_changed = (fields["type"], fields["url"], fields["path"], fields["branch"]) != (
        current["type"], current["url"], current["path"], current["branch"]
    )
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await _conflict(conn, org_id, name, fields["url"], fields["branch"], exclude_id=repo_id)
        if existing:
            raise RepoConflictError(existing)
        try:
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE repos SET name = $3, type = $4, url = $5, path = $6, branch = $7, language = $8,
                                     token_enc = $9, description = $10, restricted = $11
                     WHERE org_id = $1 AND id = $2
                    """,
                    org_id, repo_id, name, fields["type"], fields["url"], fields["path"], fields["branch"],
                    fields["language"], token_enc, data.get("description"), bool(data.get("restricted")),
                )
                if source_changed:
                    # L'indice descrive il codice di prima: si ricostruisce da zero.
                    await conn.execute("DELETE FROM repo_chunks WHERE repo_id = $1", repo_id)
                    await conn.execute("DELETE FROM repo_symbols WHERE repo_id = $1", repo_id)
                    await conn.execute(
                        "UPDATE repos SET status = 'pending', indexed_commit = NULL, total_chunks = 0, "
                        "last_indexed_at = NULL, error_message = NULL WHERE id = $1",
                        repo_id,
                    )
        except asyncpg.UniqueViolationError:
            existing = await _conflict(conn, org_id, name, fields["url"], fields["branch"], exclude_id=repo_id)
            raise RepoConflictError(existing or {"name": name})
    if source_changed:
        remove_local_copy(to_record(current))
    return await get_repo(org_id, repo_id)


async def delete_repo(org_id: int, repo_id: int) -> bool:
    """Cancella il repository; indice, annotazioni, richieste e selezioni cadono in cascata."""
    current = await get_repo(org_id, repo_id, include_token=True)
    if current is None:
        return False
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM repos WHERE org_id = $1 AND id = $2", org_id, repo_id)
    remove_local_copy(to_record(current))
    return True


async def queue_index(org_id: int, repo_id: Optional[int], project_id: Optional[int]) -> None:
    """Accoda un'indicizzazione. ``repo_id`` None = tutti i repository selezionati dal progetto."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO index_requests (org_id, project_id, repo_id) VALUES ($1, $2, $3)",
            org_id, project_id, repo_id,
        )


def remove_local_copy(repo: RepoRecord) -> None:
    """Cancella la copia locale di un repository remoto: alla prossima indicizzazione si riclona."""
    if repo.type == "local":
        return
    from ..indexer.git_manager import get_repo_local_path

    shutil.rmtree(get_repo_local_path(repo, repo.org_id), ignore_errors=True)


async def cancel_index(org_id: int, repo_id: int) -> bool:
    """Ferma l'indicizzazione in corso, scarta le richieste in coda e sblocca lo stato.

    Ritorna True se c'era un'indicizzazione in corso da interrompere.
    """
    from ..indexer.indexer import cancel_index_task

    cancelled = cancel_index_task(org_id, repo_id)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE index_requests SET processed_at = NOW() "
            "WHERE org_id = $1 AND repo_id = $2 AND processed_at IS NULL",
            org_id, repo_id,
        )
        await conn.execute(
            "UPDATE repos SET status = CASE WHEN total_chunks > 0 THEN 'indexed' ELSE 'pending' END, "
            "error_message = NULL WHERE org_id = $1 AND id = $2 AND status = 'indexing'",
            org_id, repo_id,
        )
    return cancelled
