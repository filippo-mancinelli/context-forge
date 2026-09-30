"""Macchine: accessi SSH censiti una volta per organizzazione.

Una macchina porta host, utenza e segreto; cartelle e tunnel dei database la
riusano, così una credenziale si cambia in un solo punto. I segreti sono
cifrati a riposo e mai restituiti: si espone solo ``has_secret``.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from ..datasources import engines
from ..datasources.secrets import decrypt_secret, encrypt_secret
from ..db import get_pool

_COLS = (
    "id, org_id, name, host, port, username, auth_method, password_enc, private_key_enc, "
    "description, status, error_message, last_checked_at, created_at, updated_at"
)


class MachineNotFoundError(Exception):
    pass


class MachineInUseError(Exception):
    def __init__(self, folders: int, databases: int):
        super().__init__(f"Machine is used by {folders} folders and {databases} databases")
        self.folders = folders
        self.databases = databases


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


def ssh_config(record: dict[str, Any]) -> dict[str, Any]:
    """Parametri di connessione SSH con il segreto in chiaro."""
    return {
        "host": record["host"],
        "port": record.get("port") or 22,
        "username": record["username"],
        "auth_method": record.get("auth_method") or "password",
        "password": decrypt_secret(record.get("password_enc") or ""),
        "private_key": decrypt_secret(record.get("private_key_enc") or ""),
    }


async def list_machines(org_id: int) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT {_COLS},
                   (SELECT count(*) FROM ssh_sources s WHERE s.machine_id = m.id) AS folder_count,
                   (SELECT count(*) FROM db_connections c WHERE c.ssh_machine_id = m.id) AS database_count
            FROM machines m
            WHERE m.org_id = $1
            ORDER BY lower(m.name)
            """,
            org_id,
        )
    return [_record_to_dict(r) for r in rows]


async def get_machine(org_id: int, machine_id: int, include_secret: bool = False) -> Optional[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_COLS} FROM machines WHERE org_id = $1 AND id = $2", org_id, machine_id
        )
    return _record_to_dict(row, include_secret=include_secret) if row else None


async def get_machine_by_name(org_id: int, name: str) -> Optional[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_COLS} FROM machines WHERE org_id = $1 AND lower(name) = lower($2)", org_id, name
        )
    return _record_to_dict(row) if row else None


async def find_machine(org_id: int, host: str, port: int, username: str) -> Optional[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_COLS} FROM machines "
            "WHERE org_id = $1 AND host = $2 AND port = $3 AND username = $4",
            org_id, host, int(port or 22), username,
        )
    return _record_to_dict(row) if row else None


async def create_machine(org_id: int, data: dict[str, Any]) -> dict[str, Any]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            INSERT INTO machines (org_id, name, host, port, username, auth_method,
                                  password_enc, private_key_enc, description)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING {_COLS}
            """,
            org_id,
            data["name"],
            data["host"],
            int(data.get("port") or 22),
            data["username"],
            data.get("auth_method") or "password",
            encrypt_secret(data.get("password") or ""),
            encrypt_secret(data.get("private_key") or ""),
            data.get("description"),
        )
    return _record_to_dict(row)


async def update_machine(org_id: int, machine_id: int, data: dict[str, Any]) -> Optional[dict[str, Any]]:
    existing = await get_machine(org_id, machine_id, include_secret=True)
    if existing is None:
        return None
    # Segreti vuoti in modifica = mantieni quelli memorizzati.
    password_enc = encrypt_secret(data["password"]) if data.get("password") else existing.get("password_enc") or ""
    private_key_enc = (
        encrypt_secret(data["private_key"]) if data.get("private_key") else existing.get("private_key_enc") or ""
    )
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE machines
               SET name = $3, host = $4, port = $5, username = $6, auth_method = $7,
                   password_enc = $8, private_key_enc = $9, description = $10,
                   status = 'unknown', error_message = NULL, updated_at = NOW()
             WHERE org_id = $1 AND id = $2
            RETURNING {_COLS}
            """,
            org_id,
            machine_id,
            data["name"],
            data["host"],
            int(data.get("port") or 22),
            data["username"],
            data.get("auth_method") or "password",
            password_enc,
            private_key_enc,
            data.get("description"),
        )
        database_ids = [
            r["id"] for r in await conn.fetch("SELECT id FROM db_connections WHERE ssh_machine_id = $1", machine_id)
        ]
    # I tunnel aperti con le credenziali precedenti vanno richiusi.
    for connection_id in database_ids:
        engines.dispose_engine(connection_id)
    return _record_to_dict(row) if row else None


async def delete_machine(org_id: int, machine_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM machines WHERE org_id = $1 AND id = $2", org_id, machine_id)
        if not exists:
            return False
        folders = await conn.fetchval("SELECT count(*) FROM ssh_sources WHERE machine_id = $1", machine_id)
        databases = await conn.fetchval("SELECT count(*) FROM db_connections WHERE ssh_machine_id = $1", machine_id)
        if folders or databases:
            raise MachineInUseError(int(folders), int(databases))
        await conn.execute("DELETE FROM machines WHERE org_id = $1 AND id = $2", org_id, machine_id)
    return True


async def _set_status(machine_id: int, status: str, error: Optional[str]) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE machines SET status = $2, error_message = $3, last_checked_at = NOW() WHERE id = $1",
            machine_id, status, error,
        )


async def test_machine(org_id: int, machine_id: int) -> dict[str, Any]:
    """Apre e chiude una sessione SSH con le credenziali della macchina; aggiorna lo stato."""
    from ..ssh_sources import client

    record = await get_machine(org_id, machine_id, include_secret=True)
    if record is None:
        raise MachineNotFoundError(str(machine_id))

    def _probe() -> None:
        client._open(ssh_config(record)).close()

    try:
        await asyncio.to_thread(_probe)
    except Exception as e:  # noqa: BLE001
        await _set_status(machine_id, "error", str(e))
        return {"status": "error", "error": str(e)}
    await _set_status(machine_id, "ok", None)
    return {"status": "ok"}


async def mark_pending_secret(org_id: int, machine_id: int) -> None:
    """Segna la macchina come censita ma priva di credenziale."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE machines
               SET status = 'pending_secret',
                   error_message = 'Credential not set: add the password or key from the Catalog page',
                   updated_at = NOW()
             WHERE org_id = $1 AND id = $2
            """,
            org_id, machine_id,
        )
