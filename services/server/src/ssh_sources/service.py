"""Cartelle lette via SSH: catalogo dell'organizzazione e vista del progetto.

Una cartella è una radice su una macchina del catalogo, con i glob che ne
limitano la lettura. Il catalogo appartiene all'organizzazione; un progetto
vede solo le cartelle che ha selezionato. I segreti stanno sulla macchina e non
sono mai restituiti dalle API: si espone solo ``has_secret``.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from ..catalog.names import normalize_root
from ..datasources.secrets import decrypt_secret
from ..db import get_pool
from . import client

_SELECT = (
    "SELECT s.id, s.org_id, s.name, s.machine_id, m.name AS machine_name, "
    "m.host, m.port, m.username, m.auth_method, m.password_enc, m.private_key_enc, "
    "m.status AS machine_status, s.root_path, s.include_globs, s.exclude_globs, "
    "s.description, s.restricted, s.status, s.error_message, s.last_checked_at, "
    "s.created_at, s.updated_at "
    "FROM ssh_sources s JOIN machines m ON m.id = s.machine_id"
)
# Vista del progetto: $1 = org, $2 = progetto.
_PROJECT_SELECT = (
    f"{_SELECT} JOIN project_ssh_sources ps ON ps.ssh_source_id = s.id AND ps.project_id = $2"
)


class SSHSourceNotFoundError(Exception):
    pass


class SSHSourceNotSelectedError(SSHSourceNotFoundError):
    pass


def _record_to_dict(row: Any, include_secret: bool = False) -> dict[str, Any]:
    d = dict(row)
    d["has_secret"] = bool(d.get("password_enc") or d.get("private_key_enc"))
    if not include_secret:
        d.pop("password_enc", None)
        d.pop("private_key_enc", None)
    for key in ("last_checked_at", "created_at", "updated_at"):
        if d.get(key) is not None and hasattr(d[key], "isoformat"):
            d[key] = d[key].isoformat()
    return d


def decrypted_conn(record: dict[str, Any]) -> dict[str, Any]:
    """Parametri di connessione con i segreti in chiaro, per il client SFTP."""
    return {
        "host": record["host"],
        "port": record.get("port") or 22,
        "username": record["username"],
        "auth_method": record.get("auth_method") or "password",
        "password": decrypt_secret(record.get("password_enc") or ""),
        "private_key": decrypt_secret(record.get("private_key_enc") or ""),
        "root_path": record["root_path"],
        "include_globs": record.get("include_globs"),
        "exclude_globs": record.get("exclude_globs"),
    }


# ── Catalogo dell'organizzazione ──────────────────────────────────────────────


async def list_catalog(org_id: int) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT q.*, (SELECT count(*) FROM project_ssh_sources ps WHERE ps.ssh_source_id = q.id) "
            f"AS project_count FROM ({_SELECT} WHERE s.org_id = $1) q ORDER BY lower(q.name)",
            org_id,
        )
    return [_record_to_dict(r) for r in rows]


async def get_catalog_source(
    org_id: int, source_id: int, include_secret: bool = False
) -> Optional[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(f"{_SELECT} WHERE s.org_id = $1 AND s.id = $2", org_id, source_id)
    return _record_to_dict(row, include_secret=include_secret) if row else None


async def find_source(org_id: int, machine_id: int, root_path: str) -> Optional[dict[str, Any]]:
    """Cartella già censita sulla stessa macchina e con la stessa radice."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"{_SELECT} WHERE s.org_id = $1 AND s.machine_id = $2 AND s.root_path = $3",
            org_id, machine_id, normalize_root(root_path),
        )
    return _record_to_dict(row) if row else None


async def _ensure_machine(conn, org_id: int, machine_id: int) -> None:
    if not await conn.fetchval("SELECT 1 FROM machines WHERE id = $1 AND org_id = $2", machine_id, org_id):
        raise ValueError("Machine not found in this organization")


async def create_source(org_id: int, data: dict[str, Any]) -> dict[str, Any]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_machine(conn, org_id, data["machine_id"])
        source_id = await conn.fetchval(
            """
            INSERT INTO ssh_sources (org_id, name, machine_id, root_path, include_globs,
                                     exclude_globs, description, restricted)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id
            """,
            org_id,
            data["name"],
            data["machine_id"],
            normalize_root(data["root_path"]),
            data.get("include_globs"),
            data.get("exclude_globs"),
            data.get("description"),
            bool(data.get("restricted")),
        )
    return await get_catalog_source(org_id, source_id)


async def update_source(org_id: int, source_id: int, data: dict[str, Any]) -> Optional[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_machine(conn, org_id, data["machine_id"])
        updated = await conn.fetchval(
            """
            UPDATE ssh_sources
               SET name = $3, machine_id = $4, root_path = $5, include_globs = $6,
                   exclude_globs = $7, description = $8, restricted = $9,
                   status = 'unknown', error_message = NULL, updated_at = NOW()
             WHERE org_id = $1 AND id = $2
            RETURNING id
            """,
            org_id,
            source_id,
            data["name"],
            data["machine_id"],
            normalize_root(data["root_path"]),
            data.get("include_globs"),
            data.get("exclude_globs"),
            data.get("description"),
            bool(data.get("restricted")),
        )
    return await get_catalog_source(org_id, source_id) if updated else None


async def delete_source(org_id: int, source_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        deleted = await conn.fetchval(
            "DELETE FROM ssh_sources WHERE org_id = $1 AND id = $2 RETURNING id", org_id, source_id
        )
    return deleted is not None


async def _set_status(source_id: int, status: str, error: Optional[str]) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE ssh_sources SET status = $2, error_message = $3, last_checked_at = NOW() WHERE id = $1",
            source_id, status, error,
        )


async def test_source(org_id: int, source_id: int) -> dict[str, Any]:
    """Verifica la connessione e l'accesso alla radice; aggiorna lo stato."""
    record = await get_catalog_source(org_id, source_id, include_secret=True)
    if record is None:
        raise SSHSourceNotFoundError(str(source_id))
    try:
        await asyncio.to_thread(client.check_connection, decrypted_conn(record))
    except Exception as e:  # noqa: BLE001
        await _set_status(source_id, "error", str(e))
        return {"status": "error", "error": str(e)}
    await _set_status(source_id, "ok", None)
    return {"status": "ok"}


# ── Vista del progetto ────────────────────────────────────────────────────────


async def list_sources(org_id: int, project_id: int) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"{_PROJECT_SELECT} WHERE s.org_id = $1 ORDER BY lower(s.name)", org_id, project_id
        )
    return [_record_to_dict(r) for r in rows]


async def get_source(
    org_id: int, project_id: int, source_id: int, include_secret: bool = False
) -> Optional[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"{_PROJECT_SELECT} WHERE s.org_id = $1 AND s.id = $3", org_id, project_id, source_id
        )
    return _record_to_dict(row, include_secret=include_secret) if row else None


async def resolve_source(
    org_id: int, project_id: int, ref: Any, include_secret: bool = False
) -> dict[str, Any]:
    """Risolve una cartella selezionata dal progetto per id o per nome (senza maiuscole)."""
    by_id = str(ref).isdigit()
    column = "s.id" if by_id else "lower(s.name)"
    value: Any = int(ref) if by_id else str(ref).lower()
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"{_PROJECT_SELECT} WHERE s.org_id = $1 AND {column} = $3", org_id, project_id, value
        )
        in_catalog = None
        if row is None:
            in_catalog = await conn.fetchval(
                f"SELECT 1 FROM ssh_sources s WHERE s.org_id = $1 AND {column} = $2", org_id, value
            )
    if row is not None:
        return _record_to_dict(row, include_secret=include_secret)
    if in_catalog:
        raise SSHSourceNotSelectedError(
            f"SSH source '{ref}' is not available in this project. "
            "Select it from the catalog first (catalog_list, resource_select)."
        )
    raise SSHSourceNotFoundError(f"SSH source '{ref}' not found")
