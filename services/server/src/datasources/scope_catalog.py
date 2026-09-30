"""Perimetri disponibili su una connessione del catalogo.

L'elenco si prende dal server dal vivo quando serve sceglierlo, e ogni lettura
riuscita resta sulla connessione (``available_scopes``, ``scopes_checked_at``)
come memoria dell'ultima verità osservata. Quando il server non risponde si
restituisce quella fotografia con la sua data, dichiarando che non è stata
letta ora: il perimetro si può comunque scrivere a mano e viene verificato al
primo uso.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import time
from typing import Any, Callable, Optional

from sqlalchemy.engine import Engine

from ..db import get_pool
from . import engines, introspect, service
from .scopes import scope_label

logger = logging.getLogger(__name__)

# Tetto dell'intera lettura: su PostgreSQL si apre un engine per database.
LIST_TIMEOUT_SECONDS = 30


def _database_opener(engine_name: str, engine: Engine) -> Callable[[str], Engine]:
    """Apre un engine usa e getta sullo stesso server, tunnel compreso, per un altro database."""

    def open_database(database: str) -> Engine:
        return engines.ephemeral_engine(engine_name, engine.url.set(database=database))

    return open_database


def _payload(
    engine_name: str,
    scopes: list[dict[str, Any]],
    checked_at: Optional[datetime.datetime],
    live: bool,
    error: Optional[str],
) -> dict[str, Any]:
    return {
        "scopes": [
            {
                "database": s.get("database"),
                "schema": s.get("schema"),
                "label": scope_label(engine_name, s.get("database"), s.get("schema")),
            }
            for s in scopes
        ],
        "checked_at": checked_at.isoformat() if checked_at else None,
        "live": live,
        "error": error,
    }


async def _stored(
    org_id: int, connection_id: int
) -> tuple[list[dict[str, Any]], Optional[datetime.datetime]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT available_scopes, scopes_checked_at FROM db_connections "
            "WHERE org_id = $1 AND id = $2",
            org_id, connection_id,
        )
    if row is None:
        return [], None
    value = row["available_scopes"]
    if isinstance(value, str):
        value = json.loads(value or "[]")
    return list(value or []), row["scopes_checked_at"]


async def _save(
    org_id: int, connection_id: int, scopes: list[dict[str, Any]]
) -> Optional[datetime.datetime]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "UPDATE db_connections SET available_scopes = $3::jsonb, scopes_checked_at = NOW() "
            "WHERE org_id = $1 AND id = $2 RETURNING scopes_checked_at",
            org_id, connection_id, json.dumps(scopes),
        )


async def list_available_scopes(org_id: int, connection_id: int, refresh: bool) -> dict[str, Any]:
    """Perimetri della connessione: dal vivo con ``refresh``, altrimenti l'ultima fotografia."""
    record = await service.get_catalog_connection(org_id, connection_id, include_secret=refresh)
    engine_name = record["engine"]
    if refresh:
        try:
            engine = await service._resolve_engine(record)
            # Lo stesso tetto vale per l'attesa e per il lavoro: il thread non si
            # può interrompere, quindi conosce la scadenza e si ferma da sé
            # invece di continuare ad aprire database che nessuno leggerà più.
            deadline = time.monotonic() + LIST_TIMEOUT_SECONDS
            found = await asyncio.wait_for(
                asyncio.to_thread(
                    introspect.list_scopes,
                    engine,
                    _database_opener(engine_name, engine),
                    deadline,
                ),
                timeout=LIST_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 - il server remoto può fallire in qualunque modo
            logger.info("Scope listing failed for connection %s: %s", connection_id, exc)
            scopes, checked_at = await _stored(org_id, connection_id)
            return _payload(engine_name, scopes, checked_at, False, str(exc) or type(exc).__name__)
        checked_at = await _save(org_id, connection_id, found)
        return _payload(engine_name, found, checked_at, True, None)
    scopes, checked_at = await _stored(org_id, connection_id)
    return _payload(engine_name, scopes, checked_at, False, None)
