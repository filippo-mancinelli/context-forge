"""Collegamenti di un progetto alle connessioni del catalogo, con il perimetro scelto.

Un collegamento dice su quale database e quale schema di una connessione il
progetto lavora, e con quale alias le pagine e i tool lo chiamano. Lo stesso
progetto può collegare la stessa connessione su più perimetri. Salvare un
collegamento vale come conferma del perimetro: la riga non è più dedotta.
Il collegamento di un progetto a un database esiste solo come perimetro in
``project_db_scopes``: creare, modificare e rimuovere un perimetro non tocca
altre tabelle.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

import asyncpg

from ..catalog.names import unique_name
from ..catalog.selections import ResourceNotFoundError, RestrictedResourceError
from ..db import get_pool
from . import service
from .scopes import normalize_scope, scope_label

__all__ = [
    "ALIAS_MAX_LENGTH", "AliasError", "ResourceNotFoundError", "RestrictedResourceError",
    "ScopeConflictError", "ScopeNotFoundError", "create_scope", "default_alias",
    "delete_scope", "update_scope",
]

ALIAS_MAX_LENGTH = 100


class ScopeNotFoundError(Exception):
    """Il collegamento non esiste in questo progetto."""


class ScopeConflictError(Exception):
    """Alias o perimetro già usati da un altro collegamento dello stesso progetto."""


class AliasError(ValueError):
    """L'alias non rispetta le regole: è una richiesta da correggere."""


def _validate_alias(alias: str) -> str:
    """Regole di un alias, scelto a mano o generato di default: qui vive
    l'unico punto che le controlla, così l'invarianza vale per entrambi."""
    if len(alias) > ALIAS_MAX_LENGTH:
        raise AliasError(f"The alias is longer than {ALIAS_MAX_LENGTH} characters")
    if alias.isdigit():
        # Un riferimento fatto di sole cifre viene letto come id della connessione.
        raise AliasError("The alias cannot be a number")
    return alias


def default_alias(
    existing_aliases: Iterable[str], connection_name: str, label: str, first_for_connection: bool
) -> str:
    """Alias proposto: il nome della connessione per il primo collegamento del
    progetto verso quella connessione, ``nome/perimetro`` per i successivi, con
    un suffisso numerico se nel progetto è già occupato. Il risultato rispetta
    sempre le stesse regole di un alias scelto a mano, anche quando il nome
    della connessione è lunghissimo o è già un numero: la base si accorcia per
    lasciare posto a un eventuale suffisso, e un risultato di sole cifre prende
    un prefisso prima di essere accorciato."""
    taken = {a.lower() for a in existing_aliases}
    base = connection_name if first_for_connection else f"{connection_name}/{label}"
    if base.isdigit():
        base = f"db-{base}"
    reserve = len(f"-{len(taken) + 2}")
    base = base[: ALIAS_MAX_LENGTH - reserve] or "scope"
    alias = unique_name(base, taken)
    while len(alias) > ALIAS_MAX_LENGTH:
        base = base[:-1]
        alias = unique_name(base, taken)
    return _validate_alias(alias)


def _clean_alias(alias: Optional[str]) -> Optional[str]:
    if alias is None:
        return None
    alias = alias.strip()
    if not alias:
        return None
    return _validate_alias(alias)


async def _check_free(
    conn,
    project_id: int,
    connection_id: int,
    database: Optional[str],
    schema: Optional[str],
    alias: str,
    exclude_id: Optional[int],
) -> None:
    """Rifiuta un perimetro o un alias già usati da un altro collegamento del progetto."""
    clash = await conn.fetchval(
        """
        SELECT alias FROM project_db_scopes
         WHERE project_id = $1 AND db_connection_id = $2
           AND coalesce(database_name, '') = coalesce($3::text, '')
           AND coalesce(schema_name, '') = coalesce($4::text, '')
           AND id IS DISTINCT FROM $5::bigint
        """,
        project_id, connection_id, database, schema, exclude_id,
    )
    if clash is not None:
        raise ScopeConflictError(f"This project already uses this scope as '{clash}'")
    taken = await conn.fetchval(
        "SELECT 1 FROM project_db_scopes WHERE project_id = $1 AND lower(alias) = lower($2) "
        "AND id IS DISTINCT FROM $3::bigint",
        project_id, alias, exclude_id,
    )
    if taken:
        raise ScopeConflictError(f"The alias '{alias}' is already used in this project")


def _conflict_from_violation(exc: asyncpg.UniqueViolationError, alias: str) -> ScopeConflictError:
    """Stesso messaggio del controllo preventivo, letto dal vincolo che ha
    respinto la scrittura: una corsa fra due salvataggi concorrenti supera il
    controllo preventivo di entrambi, ma non l'indice del secondo che arriva."""
    if exc.constraint_name == "project_db_scopes_alias_idx":
        return ScopeConflictError(f"The alias '{alias}' is already used in this project")
    return ScopeConflictError("This scope is already used in this project")


async def _scope_row(org_id: int, project_id: int, scope_id: int) -> dict[str, Any]:
    """Il collegamento come lo vede il progetto, con le stesse chiavi di get_connection."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"{service._PROJECT_SELECT} WHERE c.org_id = $1 AND pds.id = $3",
            org_id, project_id, scope_id,
        )
    if row is None:
        raise ScopeNotFoundError(f"Data source #{scope_id} is not in this project")
    return service._record_to_dict(row)


async def create_scope(
    org_id: int,
    project_id: int,
    connection_id: int,
    database: Optional[str],
    schema: Optional[str],
    alias: Optional[str],
    user_id: Optional[int],
    can_select_restricted: bool,
    inferred: bool = False,
) -> dict[str, Any]:
    """Collega la connessione al progetto sul perimetro scelto, già confermato.

    Con ``inferred`` il perimetro nasce invece dedotto: è la forma per un
    perimetro che nessuno ha scelto, ricavato dalla connessione, che resta a
    enforcement morbido e porta con sé l'avviso di verificarlo.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            project_in_org = await conn.fetchval(
                "SELECT 1 FROM projects WHERE id = $1 AND org_id = $2", project_id, org_id
            )
            connection = await conn.fetchrow(
                "SELECT id, name, engine, restricted FROM db_connections WHERE id = $1 AND org_id = $2",
                connection_id, org_id,
            )
            if not project_in_org or connection is None:
                raise ResourceNotFoundError(f"databases #{connection_id}")
            if connection["restricted"] and not can_select_restricted:
                raise RestrictedResourceError(connection["name"])
            database, schema = normalize_scope(connection["engine"], database, schema)
            rows = await conn.fetch(
                "SELECT db_connection_id, alias FROM project_db_scopes WHERE project_id = $1",
                project_id,
            )
            alias = _clean_alias(alias) or default_alias(
                [r["alias"] for r in rows],
                connection["name"],
                scope_label(connection["engine"], database, schema),
                first_for_connection=not any(r["db_connection_id"] == connection_id for r in rows),
            )
            await _check_free(conn, project_id, connection_id, database, schema, alias, None)
            try:
                scope_id = await conn.fetchval(
                    """
                    INSERT INTO project_db_scopes
                        (project_id, db_connection_id, database_name, schema_name, alias,
                         scope_inferred, added_by)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    RETURNING id
                    """,
                    project_id, connection_id, database, schema, alias, inferred, user_id,
                )
            except asyncpg.UniqueViolationError as exc:
                # Due salvataggi concorrenti: gli indici unici hanno l'ultima parola.
                raise _conflict_from_violation(exc, alias) from exc
    return await _scope_row(org_id, project_id, scope_id)


async def update_scope(
    org_id: int,
    project_id: int,
    scope_id: int,
    database: Optional[str],
    schema: Optional[str],
    alias: Optional[str],
) -> dict[str, Any]:
    """Cambia perimetro e alias del collegamento e lo segna come confermato."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            current = await conn.fetchrow(
                """
                SELECT s.id, s.db_connection_id, s.alias, c.engine
                  FROM project_db_scopes s
                  JOIN db_connections c ON c.id = s.db_connection_id
                  JOIN projects p ON p.id = s.project_id
                 WHERE s.id = $1 AND s.project_id = $2 AND p.org_id = $3
                """,
                scope_id, project_id, org_id,
            )
            if current is None:
                raise ScopeNotFoundError(f"Data source #{scope_id} is not in this project")
            database, schema = normalize_scope(current["engine"], database, schema)
            alias = _clean_alias(alias) or current["alias"]
            await _check_free(
                conn, project_id, current["db_connection_id"], database, schema, alias, scope_id
            )
            try:
                await conn.execute(
                    "UPDATE project_db_scopes SET database_name = $2, schema_name = $3, "
                    "alias = $4, scope_inferred = false WHERE id = $1",
                    scope_id, database, schema, alias,
                )
            except asyncpg.UniqueViolationError as exc:
                raise _conflict_from_violation(exc, alias) from exc
    return await _scope_row(org_id, project_id, scope_id)


async def delete_scope(org_id: int, project_id: int, scope_id: int) -> None:
    """Toglie il collegamento."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            connection_id = await conn.fetchval(
                """
                DELETE FROM project_db_scopes s
                 USING projects p
                 WHERE s.id = $1 AND s.project_id = $2
                   AND p.id = s.project_id AND p.org_id = $3
                RETURNING s.db_connection_id
                """,
                scope_id, project_id, org_id,
            )
            if connection_id is None:
                raise ScopeNotFoundError(f"Data source #{scope_id} is not in this project")
