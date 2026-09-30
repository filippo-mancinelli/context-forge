"""Conversione dei collegamenti progetto-connessione in collegamenti con perimetro.

Un database che arriva dalla forma precedente ha i collegamenti in
``project_db_connections``: ognuno diventa una riga di ``project_db_scopes``
con il perimetro che quel progetto vedeva, su PostgreSQL il database della
connessione e lo schema di default, su MySQL e MariaDB il solo database, su
SQLite nessuno dei due. Le righe nascono marcate come dedotte, perché nessuno le
ha ancora confermate. Un perimetro senza collegamento legacy è un collegamento a
tutti gli effetti e resta. Quando ogni collegamento legacy ha il suo perimetro
la tabella legacy viene rimossa; se anche uno solo ne è privo resta, e il log
lo dice. Senza la tabella legacy non c'è nulla da convertire.
Idempotente e serializzata: sicura a ogni avvio, anche quando due istanze
partono insieme.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from ..catalog.names import unique_name
from ..db import get_pool
from .engines import SUPPORTED_ENGINES
from .scopes import ScopeShapeError, default_scope

logger = logging.getLogger(__name__)

# Chiave fissa del lock che serializza la migrazione: resta la stessa a ogni
# avvio, così due istanze la riconoscono come lo stesso lavoro.
MIGRATION_LOCK_KEY = 7412009351

# Forma della tabella dei collegamenti prima del perimetro: serve a convertire
# un database che arriva da quella forma, mai a scriverci collegamenti nuovi.
LEGACY_DB_LINKS_DDL = """
CREATE TABLE IF NOT EXISTS project_db_connections (
    project_id       BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    db_connection_id BIGINT NOT NULL REFERENCES db_connections(id) ON DELETE CASCADE,
    added_by         BIGINT REFERENCES admin_users(id) ON DELETE SET NULL,
    added_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (project_id, db_connection_id)
);
CREATE INDEX IF NOT EXISTS project_db_connections_connection_idx
    ON project_db_connections (db_connection_id);
"""


async def _legacy_links_exist(conn) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass('project_db_connections') IS NOT NULL"))


async def _convert_legacy_links(conn, report: dict[str, Any]) -> None:
    """Crea un perimetro dedotto per ogni collegamento legacy che non ne ha."""
    taken: dict[int, set[str]] = defaultdict(set)
    for row in await conn.fetch("SELECT project_id, lower(alias) AS alias FROM project_db_scopes"):
        taken[row["project_id"]].add(row["alias"])

    pending = await conn.fetch(
        """
        SELECT l.project_id, l.db_connection_id, l.added_by, l.added_at,
               c.engine, c.database_name, c.name
          FROM project_db_connections l
          JOIN db_connections c ON c.id = l.db_connection_id
         WHERE NOT EXISTS (
                   SELECT 1 FROM project_db_scopes s
                    WHERE s.project_id = l.project_id
                      AND s.db_connection_id = l.db_connection_id
               )
         ORDER BY l.project_id, l.db_connection_id
        """
    )

    for row in pending:
        try:
            database, schema = default_scope(dict(row))
        except ScopeShapeError as exc:
            if row["engine"] not in SUPPORTED_ENGINES:
                # Un motore non più supportato non si inventa: il
                # collegamento resta senza perimetro e si vede.
                report["skipped"].append(f"{row['name']}: {exc}")
                continue
            # Senza un database utilizzabile il perimetro resta vuoto,
            # come alla selezione: il progetto continua a vedere la
            # connessione e la sceglie una persona.
            database, schema = None, None
        alias = unique_name(row["name"], taken[row["project_id"]])
        taken[row["project_id"]].add(alias.lower())
        if alias != row["name"]:
            report["renamed"].append(f"{row['name']} -> {alias}")
        scope_id = await conn.fetchval(
            """
            INSERT INTO project_db_scopes
                (project_id, db_connection_id, database_name, schema_name,
                 alias, scope_inferred, added_by, added_at)
            VALUES ($1, $2, $3, $4, $5, true, $6, $7)
            -- Il conflitto va mirato sull'unicità del perimetro, come alla
            -- selezione: un ON CONFLICT generico assorbirebbe anche una
            -- collisione sull'alias e il conteggio direbbe di aver creato
            -- un perimetro che non c'è.
            ON CONFLICT (project_id, db_connection_id, coalesce(database_name, ''),
                         coalesce(schema_name, ''))
                DO NOTHING
            RETURNING id
            """,
            row["project_id"], row["db_connection_id"], database, schema,
            alias, row["added_by"], row["added_at"],
        )
        # Si conta solo il perimetro nato davvero.
        if scope_id is not None:
            report["scopes"] += 1


async def _drop_legacy_links(conn) -> str:
    """Rimuove la tabella dei collegamenti senza perimetro, quando non serve più.

    I collegamenti che hanno già il loro perimetro si cancellano subito: una
    tabella che resta non deve poter far ricomparire un perimetro che qualcuno
    ha rimosso. La tabella si cancella solo se non ne resta nemmeno uno:
    altrimenti resta, e il log elenca i collegamenti rimasti senza perimetro e
    dice come sbloccarli, così nessun progetto perde un database in silenzio.
    Ritorna ``"dropped"``,
    ``"kept"`` oppure ``"absent"`` se la tabella non c'era.
    """
    if not await _legacy_links_exist(conn):
        return "absent"
    await conn.execute(
        """
        DELETE FROM project_db_connections l
         WHERE EXISTS (
                   SELECT 1 FROM project_db_scopes s
                    WHERE s.project_id = l.project_id
                      AND s.db_connection_id = l.db_connection_id
               )
        """
    )
    # Quello che resta è senza perimetro: la conversione non ha saputo dedurlo.
    missing = await conn.fetch(
        """
        SELECT project_id, db_connection_id
          FROM project_db_connections
         ORDER BY project_id, db_connection_id
        """
    )
    if missing:
        logger.warning(
            "Legacy table project_db_connections kept: %d links have no scope (%s). "
            "To let it go, either make the connection engine supported again, or "
            "delete the connection so its link goes with it, then restart the server.",
            len(missing),
            ", ".join(
                f"project {r['project_id']} -> connection {r['db_connection_id']}" for r in missing
            ),
        )
        return "kept"
    await conn.execute("DROP TABLE project_db_connections")
    return "dropped"


async def apply_db_scope_migration() -> dict[str, Any]:
    """Crea i perimetri mancanti per i collegamenti legacy e rimuove la tabella
    legacy quando ogni collegamento ha il suo perimetro. Ritorna quanti perimetri
    ha creato, gli alias cambiati per collisione, i collegamenti saltati e la
    sorte della tabella legacy."""
    report: dict[str, Any] = {
        "scopes": 0, "renamed": [], "skipped": [], "legacy_links": "absent",
    }
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Due istanze che partono insieme non devono correre sulla rimozione
            # della tabella: la prima tiene il lock fino a fine transazione, la
            # seconda aspetta e trova il lavoro già fatto.
            await conn.execute("SELECT pg_advisory_xact_lock($1)", MIGRATION_LOCK_KEY)
            if await _legacy_links_exist(conn):
                await _convert_legacy_links(conn, report)
            report["legacy_links"] = await _drop_legacy_links(conn)

    if (report["scopes"] or report["renamed"] or report["skipped"]
            or report["legacy_links"] != "absent"):
        logger.info("Database scope migration: %s", report)
    return report
