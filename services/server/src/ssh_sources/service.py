"""CRUD e risoluzione delle sorgenti filesystem SSH (scoped per progetto).

I segreti (password/chiave privata) sono cifrati a riposo e mai restituiti dalle
API: si espone solo ``has_secret``. La lettura effettiva dei file avviene nei
tool MCP tramite ``client``.
"""
from __future__ import annotations

from typing import Any, Optional

from ..datasources.secrets import decrypt_secret, encrypt_secret
from ..db import get_pool
from . import client

_COLS = (
    "id, org_id, project_id, name, host, port, username, auth_method, "
    "password_enc, private_key_enc, root_path, include_globs, exclude_globs, "
    "description, status, error_message, last_checked_at, created_at, updated_at"
)


class SSHSourceNotFoundError(Exception):
    pass


class SSHSourceAmbiguousError(Exception):
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


async def list_sources(org_id: int, project_id: int) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {_COLS} FROM ssh_sources WHERE org_id=$1 AND project_id=$2 ORDER BY name",
            org_id,
            project_id,
        )
    return [_record_to_dict(r) for r in rows]


async def get_source(
    org_id: int, project_id: int, source_id: int, include_secret: bool = False
) -> Optional[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_COLS} FROM ssh_sources WHERE org_id=$1 AND project_id=$2 AND id=$3",
            org_id,
            project_id,
            source_id,
        )
    return _record_to_dict(row, include_secret=include_secret) if row else None


async def resolve_source(
    org_id: int, project_id: int, ref: str, include_secret: bool = False
) -> dict[str, Any]:
    """Risolve una sorgente per id numerico o per nome (usata dai tool MCP)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if ref.isdigit():
            row = await conn.fetchrow(
                f"SELECT {_COLS} FROM ssh_sources WHERE org_id=$1 AND project_id=$2 AND id=$3",
                org_id, project_id, int(ref),
            )
        else:
            rows = await conn.fetch(
                f"SELECT {_COLS} FROM ssh_sources WHERE org_id=$1 AND project_id=$2 AND name=$3",
                org_id, project_id, ref,
            )
            if len(rows) > 1:
                raise SSHSourceAmbiguousError(ref)
            row = rows[0] if rows else None
    if row is None:
        raise SSHSourceNotFoundError(ref)
    return _record_to_dict(row, include_secret=include_secret)


async def create_source(org_id: int, project_id: int, data: dict[str, Any]) -> dict[str, Any]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            INSERT INTO ssh_sources
                (org_id, project_id, name, host, port, username, auth_method,
                 password_enc, private_key_enc, root_path, include_globs,
                 exclude_globs, description)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
            RETURNING {_COLS}
            """,
            org_id,
            project_id,
            data["name"],
            data["host"],
            data.get("port") or 22,
            data["username"],
            data.get("auth_method") or "password",
            encrypt_secret(data.get("password") or ""),
            encrypt_secret(data.get("private_key") or ""),
            data["root_path"],
            data.get("include_globs"),
            data.get("exclude_globs"),
            data.get("description"),
        )
    return _record_to_dict(row)


async def update_source(
    org_id: int, project_id: int, source_id: int, data: dict[str, Any]
) -> Optional[dict[str, Any]]:
    existing = await get_source(org_id, project_id, source_id, include_secret=True)
    if existing is None:
        return None
    # Segreti vuoti in update = mantieni quelli memorizzati.
    if data.get("password"):
        password_enc = encrypt_secret(data["password"])
    else:
        password_enc = existing.get("password_enc") or ""
    if data.get("private_key"):
        private_key_enc = encrypt_secret(data["private_key"])
    else:
        private_key_enc = existing.get("private_key_enc") or ""

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE ssh_sources
            SET name=$4, host=$5, port=$6, username=$7, auth_method=$8,
                password_enc=$9, private_key_enc=$10, root_path=$11,
                include_globs=$12, exclude_globs=$13, description=$14,
                status='unknown', error_message=NULL, updated_at=NOW()
            WHERE org_id=$1 AND project_id=$2 AND id=$3
            RETURNING {_COLS}
            """,
            org_id,
            project_id,
            source_id,
            data["name"],
            data["host"],
            data.get("port") or 22,
            data["username"],
            data.get("auth_method") or "password",
            password_enc,
            private_key_enc,
            data["root_path"],
            data.get("include_globs"),
            data.get("exclude_globs"),
            data.get("description"),
        )
    return _record_to_dict(row) if row else None


async def delete_source(org_id: int, project_id: int, source_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        deleted = await conn.fetchval(
            "DELETE FROM ssh_sources WHERE org_id=$1 AND project_id=$2 AND id=$3 RETURNING id",
            org_id,
            project_id,
            source_id,
        )
    return deleted is not None


async def _set_status(source_id: int, status: str, error: Optional[str]) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE ssh_sources SET status=$2, error_message=$3, last_checked_at=NOW() WHERE id=$1",
            source_id,
            status,
            error,
        )


async def test_source(org_id: int, project_id: int, source_id: int) -> dict[str, Any]:
    """Verifica la connessione e l'accesso alla radice; aggiorna lo stato."""
    import asyncio

    record = await get_source(org_id, project_id, source_id, include_secret=True)
    if record is None:
        raise SSHSourceNotFoundError(str(source_id))
    conn = decrypted_conn(record)
    try:
        await asyncio.to_thread(client.check_connection, conn)
    except Exception as e:  # noqa: BLE001
        await _set_status(source_id, "error", str(e))
        return {"status": "error", "error": str(e)}
    await _set_status(source_id, "ok", None)
    return {"status": "ok"}


async def mark_pending_secret(org_id: int, project_id: int, source_id: int) -> None:
    """Segna la sorgente come creata ma priva di credenziale."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE ssh_sources
               SET status='pending_secret',
                   error_message='Credential not set: add the password or key from the UI',
                   updated_at=NOW()
             WHERE org_id=$1 AND project_id=$2 AND id=$3
            """,
            org_id,
            project_id,
            source_id,
        )
