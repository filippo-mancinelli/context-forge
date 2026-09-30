"""Conversione delle risorse di progetto nel catalogo dell'organizzazione.

Cartelle SSH e connessioni ai database portavano host, utenza e segreto SSH in
ogni riga e appartenevano a un solo progetto. Qui le credenziali SSH diventano
macchine censite una volta per organizzazione e il legame con il progetto
diventa una selezione. Idempotente: sicura a ogni avvio.
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from typing import Any

from ..datasources.secrets import decrypt_secret
from ..db import get_pool
from .names import machine_name, unique_name

logger = logging.getLogger(__name__)

_SSH_SOURCE_LEGACY_COLUMNS = (
    "project_id", "host", "port", "username", "auth_method", "password_enc", "private_key_enc",
)
_DB_LEGACY_COLUMNS = (
    "project_id", "ssh_enabled", "ssh_host", "ssh_port", "ssh_username",
    "ssh_auth_method", "ssh_password_enc", "ssh_private_key_enc",
)


def _secret_key(candidate: dict[str, Any]) -> tuple[str, str, str]:
    return (candidate["auth_method"] or "password", candidate["password"], candidate["private_key"])


def choose_secret(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Il segreto usato dal maggior numero di righe; a parità, quello della riga
    con il test riuscito più recente; senza test, il primo incontrato."""
    counts = Counter(_secret_key(c) for c in candidates)
    best = max(counts.values())
    tied = [c for c in candidates if counts[_secret_key(c)] == best]
    if len({_secret_key(c) for c in tied}) == 1:
        return tied[0]
    checked = [c for c in tied if c.get("status") == "ok" and c.get("last_checked_at")]
    if checked:
        return max(checked, key=lambda c: c["last_checked_at"])
    return tied[0]


async def _column_exists(conn, table: str, column: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = $1 AND column_name = $2",
            table,
            column,
        )
    )


async def _legacy_ssh_rows(conn, has_sources: bool, has_databases: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if has_sources:
        for r in await conn.fetch(
            "SELECT id, org_id, host, port, username, auth_method, password_enc, private_key_enc, "
            "status, last_checked_at FROM ssh_sources WHERE machine_id IS NULL ORDER BY id"
        ):
            rows.append({"table": "ssh_sources", **dict(r)})
    if has_databases:
        for r in await conn.fetch(
            "SELECT id, org_id, ssh_host AS host, ssh_port AS port, ssh_username AS username, "
            "ssh_auth_method AS auth_method, ssh_password_enc AS password_enc, "
            "ssh_private_key_enc AS private_key_enc, status, last_checked_at "
            "FROM db_connections "
            # Senza utenza non si può censire una macchina: la connessione resta diretta.
            "WHERE ssh_enabled AND ssh_machine_id IS NULL AND ssh_host IS NOT NULL "
            "AND ssh_username IS NOT NULL ORDER BY id"
        ):
            rows.append({"table": "db_connections", **dict(r)})
    for row in rows:
        row["port"] = row["port"] or 22
        row["auth_method"] = row["auth_method"] or "password"
        # Un segreto non decifrabile interrompe la migrazione: meglio fermare
        # l'avvio che perdere credenziali in silenzio.
        row["password"] = decrypt_secret(row["password_enc"] or "")
        row["private_key"] = decrypt_secret(row["private_key_enc"] or "")
    return rows


async def _create_machines(conn, rows: list[dict[str, Any]], report: dict[str, Any]) -> None:
    groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["org_id"], row["host"], row["port"], row["username"])].append(row)

    taken: dict[int, set[str]] = defaultdict(set)
    for r in await conn.fetch("SELECT org_id, lower(name) AS name FROM machines"):
        taken[r["org_id"]].add(r["name"])

    for (org_id, host, port, username), members in groups.items():
        machine_id = await conn.fetchval(
            "SELECT id FROM machines WHERE org_id = $1 AND host = $2 AND port = $3 AND username = $4",
            org_id, host, port, username,
        )
        if machine_id is None:
            chosen = choose_secret(members)
            name = unique_name(machine_name(username, host, port), taken[org_id])
            taken[org_id].add(name.lower())
            has_secret = bool(chosen["password"] or chosen["private_key"])
            machine_id = await conn.fetchval(
                """
                INSERT INTO machines (org_id, name, host, port, username, auth_method,
                                      password_enc, private_key_enc, status)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING id
                """,
                org_id, name, host, port, username, chosen["auth_method"],
                chosen["password_enc"] or "", chosen["private_key_enc"] or "",
                "unknown" if has_secret else "pending_secret",
            )
            report["machines"] += 1
            for member in members:
                if _secret_key(member) != _secret_key(chosen):
                    report["secret_replaced"].append(f"{member['table']}#{member['id']}")

        source_ids = [m["id"] for m in members if m["table"] == "ssh_sources"]
        database_ids = [m["id"] for m in members if m["table"] == "db_connections"]
        if source_ids:
            await conn.execute(
                "UPDATE ssh_sources SET machine_id = $1 WHERE id = ANY($2::bigint[])",
                machine_id, source_ids,
            )
        if database_ids:
            await conn.execute(
                "UPDATE db_connections SET ssh_machine_id = $1 WHERE id = ANY($2::bigint[])",
                machine_id, database_ids,
            )


async def _move_to_selections(conn, table: str, link: str, column: str, report: dict[str, Any]) -> None:
    """Rende i nomi unici nell'organizzazione e trasforma ``project_id`` in selezione."""
    taken: dict[int, set[str]] = defaultdict(set)
    rows = await conn.fetch(
        f"SELECT t.id, t.org_id, t.name, p.slug FROM {table} t "
        f"LEFT JOIN projects p ON p.id = t.project_id ORDER BY t.org_id, t.id"
    )
    for r in rows:
        name = r["name"]
        if name.lower() in taken[r["org_id"]]:
            renamed = unique_name(f"{name}-{r['slug'] or 'project'}", taken[r["org_id"]])
            await conn.execute(f"UPDATE {table} SET name = $1 WHERE id = $2", renamed, r["id"])
            report["renamed"].append(f"{table}#{r['id']}: {name} -> {renamed}")
            name = renamed
        taken[r["org_id"]].add(name.lower())
    # Un project_id che punta a un progetto cancellato non diventa una selezione.
    await conn.execute(
        f"INSERT INTO {link} (project_id, {column}) "
        f"SELECT project_id, id FROM {table} "
        f"WHERE project_id IN (SELECT id FROM projects) "
        f"ON CONFLICT DO NOTHING"
    )


async def _drop_legacy_columns(conn) -> None:
    await conn.execute("ALTER TABLE ssh_sources DROP CONSTRAINT IF EXISTS ssh_sources_project_id_name_key")
    await conn.execute("DROP INDEX IF EXISTS ssh_sources_project_idx")
    for column in _SSH_SOURCE_LEGACY_COLUMNS:
        await conn.execute(f"ALTER TABLE ssh_sources DROP COLUMN IF EXISTS {column}")
    await conn.execute("ALTER TABLE ssh_sources ALTER COLUMN machine_id SET NOT NULL")
    await conn.execute("ALTER TABLE db_connections DROP CONSTRAINT IF EXISTS db_connections_project_name_key")
    await conn.execute("ALTER TABLE db_connections DROP CONSTRAINT IF EXISTS db_connections_org_id_name_key")
    for column in _DB_LEGACY_COLUMNS:
        await conn.execute(f"ALTER TABLE db_connections DROP COLUMN IF EXISTS {column}")


async def _ensure_unique_names(conn) -> None:
    await conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS machines_org_lower_name_idx ON machines (org_id, lower(name))"
    )
    await conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ssh_sources_org_lower_name_idx ON ssh_sources (org_id, lower(name))"
    )
    await conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS db_connections_org_lower_name_idx ON db_connections (org_id, lower(name))"
    )


async def apply_catalog_migration() -> dict[str, Any]:
    """Converte le risorse legacy e garantisce l'unicità dei nomi. Ritorna un
    report con le macchine create, le righe il cui segreto è stato sostituito e
    i nomi cambiati."""
    report: dict[str, Any] = {"machines": 0, "secret_replaced": [], "renamed": []}
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            has_sources = await _column_exists(conn, "ssh_sources", "host")
            has_databases = await _column_exists(conn, "db_connections", "ssh_host")
            if has_sources or has_databases:
                rows = await _legacy_ssh_rows(conn, has_sources, has_databases)
                await _create_machines(conn, rows, report)
            if await _column_exists(conn, "ssh_sources", "project_id"):
                await _move_to_selections(conn, "ssh_sources", "project_ssh_sources", "ssh_source_id", report)
            if await _column_exists(conn, "db_connections", "project_id"):
                await _move_to_selections(conn, "db_connections", "project_db_connections", "db_connection_id", report)
            await _drop_legacy_columns(conn)
            await _ensure_unique_names(conn)
    if report["machines"] or report["secret_replaced"] or report["renamed"]:
        logger.info("Catalog migration: %s", report)
    return report
