"""Org-scoped service layer for external database connections.

Bridges the async application (asyncpg metadata store) with the synchronous
SQLAlchemy engines (executed in worker threads). Every schema/describe result
is enriched with the human-curated annotations from ``db_annotations``, and
every executed query is recorded in ``db_query_log``.
"""
from __future__ import annotations

import asyncio
import base64
import datetime
import decimal
import json
import logging
import os
import re
import time
import uuid
from typing import Any, Optional

import sqlalchemy as sa

from ..db import get_pool
from . import engines, introspect
from .scopes import (
    ScopeShapeError,
    ScopeViolationError,
    connect_options,
    introspection_schema,
    normalize_scope,
    scope_label,
    url_database,
)
from .secrets import decrypt_secret, encrypt_secret
from .validator import (
    QueryValidationError,
    ScopeReferenceError,
    validate_query,
    validate_write_query,
)

logger = logging.getLogger(__name__)

QUERY_TIMEOUT_SECONDS = 15
MAX_ROWS_HARD_CAP = 500
MAX_CELL_CHARS = 2000

_CONN_SELECT = (
    "SELECT c.id, c.org_id, c.name, c.engine, c.host, c.port, c.database_name, c.username, "
    "c.password_enc, c.options, c.description, c.restricted, c.status, c.error_message, "
    "c.last_checked_at, c.created_at, c.updated_at, c.ssh_machine_id, "
    "(c.ssh_machine_id IS NOT NULL) AS ssh_enabled, m.name AS ssh_machine_name, "
    "m.host AS ssh_host, m.port AS ssh_port, m.username AS ssh_username, "
    "m.auth_method AS ssh_auth_method, m.password_enc AS ssh_password_enc, "
    "m.private_key_enc AS ssh_private_key_enc "
    "FROM db_connections c LEFT JOIN machines m ON m.id = c.ssh_machine_id"
)
# Vista del catalogo: la connessione con i perimetri visti all'ultimo controllo
# riuscito, che il dialogo di censimento mostra in sola lettura.
_CATALOG_SELECT = _CONN_SELECT.replace(
    "FROM db_connections c",
    ", c.available_scopes, c.scopes_checked_at FROM db_connections c",
)
# Colonne del perimetro: solo la vista di progetto le porta, il collegamento
# le lega alla connessione.
_SCOPE_COLUMNS = (
    "pds.id AS scope_id, pds.alias, pds.database_name AS scope_database, "
    "pds.schema_name AS scope_schema, pds.scope_inferred"
)
# Vista del progetto: $1 = org, $2 = progetto. Il collegamento porta il perimetro.
_PROJECT_SELECT = (
    _CONN_SELECT.replace("FROM db_connections c", f", {_SCOPE_COLUMNS} FROM db_connections c")
    + " JOIN project_db_scopes pds ON pds.db_connection_id = c.id AND pds.project_id = $2"
)


class ConnectionNotFoundError(Exception):
    pass


class ConnectionAmbiguousError(Exception):
    pass


class ConnectionNotSelectedError(ConnectionNotFoundError):
    """La connessione esiste nel catalogo ma il progetto non l'ha selezionata."""


_LIST_SENTINELS = frozenset({"__list__", "__all__", "list", "all", "*"})


def is_list_sentinel(ref: str | None) -> bool:
    """True when an agent passed a placeholder instead of a real connection name."""
    if ref is None:
        return False
    return not str(ref).strip() or str(ref).strip().lower() in _LIST_SENTINELS


def _normalize_hint(value: str) -> str:
    return re.sub(r"[\s_\-]+", " ", str(value).lower()).strip()


def _score_connection(conn: dict[str, Any], hint: str) -> float:
    hint_norm = _normalize_hint(hint)
    if not hint_norm:
        return 0.0
    best = 0.0
    # Il nome con cui l'agente cerca un collegamento è l'alias, il nome della
    # connessione, la descrizione o il perimetro; il database predefinito
    # della connessione non identifica più un collegamento.
    for field in ("alias", "name", "description", "scope_label"):
        val = conn.get(field) or ""
        val_norm = _normalize_hint(val)
        if not val_norm:
            continue
        if hint_norm == val_norm:
            return 100.0
        if hint_norm in val_norm or val_norm in hint_norm:
            best = max(best, 85.0)
        hint_tokens = set(hint_norm.split())
        val_tokens = set(val_norm.split())
        if hint_tokens and val_tokens:
            overlap = len(hint_tokens & val_tokens) / len(hint_tokens | val_tokens)
            best = max(best, overlap * 75.0)
    return best


def _connection_not_found_message(ref: str | int, connections: list[dict[str, Any]]) -> str:
    names = [c.get("alias") or c["name"] for c in connections]
    if names:
        return f"Database connection '{ref}' not found. Available: {', '.join(names)}"
    return f"Database connection '{ref}' not found. No connections are configured."


async def resolve_connection(
    org_id: int,
    project_id: int,
    hint: str | None = None,
    *,
    context_hints: list[str] | None = None,
    min_score: float = 35.0,
) -> dict[str, Any]:
    """Pick the best-matching connection for a hint and optional conversation context."""
    hints = [h.strip() for h in ([hint] if hint else []) + (context_hints or []) if h and h.strip()]

    # Un nome esatto del catalogo non selezionato dal progetto va segnalato come
    # tale prima di qualsiasi ricerca approssimata.
    for h in hints:
        if is_list_sentinel(h):
            continue
        try:
            return await get_scope(org_id, project_id, h)
        except ConnectionNotSelectedError:
            raise
        except ConnectionNotFoundError:
            pass

    connections = await list_connections(org_id, project_id)
    if not connections:
        raise ConnectionNotFoundError("No database connections configured")
    if not hints:
        raise ConnectionNotFoundError(
            "No connection specified. Call db_list() or get_database_schema with no arguments."
        )

    scored: list[tuple[float, dict[str, Any]]] = []
    for conn in connections:
        score = max((_score_connection(conn, h) for h in hints), default=0.0)
        if score >= min_score:
            scored.append((score, conn))

    if not scored:
        primary = hints[0]
        raise ConnectionNotFoundError(_connection_not_found_message(primary, connections))

    scored.sort(key=lambda item: item[0], reverse=True)
    if len(scored) > 1 and scored[0][0] - scored[1][0] < 12:
        names = [c.get("alias") or c["name"] for _, c in scored[:3]]
        raise ConnectionAmbiguousError(
            f"Multiple database connections match ({', '.join(names)}). "
            "Specify the exact connection name."
        )
    return scored[0][1]


# --------------------------------------------------------------------------- #
# Connection CRUD (metadata store)
# --------------------------------------------------------------------------- #
def _record_to_dict(row: Any, include_secret: bool = False) -> dict[str, Any]:
    d = dict(row)
    if isinstance(d.get("options"), str):
        d["options"] = json.loads(d["options"] or "{}")
    if isinstance(d.get("available_scopes"), str):
        d["available_scopes"] = json.loads(d["available_scopes"] or "[]")
    d["has_password"] = bool(d.get("password_enc"))
    d["has_ssh_secret"] = bool(d.get("ssh_password_enc") or d.get("ssh_private_key_enc"))
    if "scope_id" in d:
        # Il perimetro esiste solo nella vista di progetto. L'etichetta descrive
        # quello applicato davvero: per un perimetro dedotto, la connessione
        # così com'è; per uno confermato, database e schema scelti.
        d["scope_label"] = scope_label(d["engine"], *_effective_scope(d))
    if not include_secret:
        d.pop("password_enc", None)
        d.pop("ssh_password_enc", None)
        d.pop("ssh_private_key_enc", None)
    for key in ("last_checked_at", "created_at", "updated_at", "scopes_checked_at"):
        if d.get(key) is not None:
            d[key] = d[key].isoformat()
    return d


async def list_connections(org_id: int, project_id: int) -> list[dict[str, Any]]:
    """Connessioni selezionate dal progetto, con il perimetro del collegamento."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"{_PROJECT_SELECT} WHERE c.org_id = $1 ORDER BY lower(pds.alias)", org_id, project_id
        )
        counts = await conn.fetch(
            "SELECT connection_id, count(*) AS annotation_count FROM db_annotations "
            "WHERE connection_id = ANY($1::bigint[]) GROUP BY connection_id",
            [r["id"] for r in rows],
        )
    count_map = {r["connection_id"]: r["annotation_count"] for r in counts}
    out = []
    for r in rows:
        d = _record_to_dict(r)
        d["annotation_count"] = count_map.get(r["id"], 0)
        out.append(d)
    return out


async def get_scope(
    org_id: int, project_id: int, ref: int | str, include_secret: bool = False
) -> dict[str, Any]:
    """Perimetro del progetto, con la sua connessione.

    Un riferimento numerico è l'id del perimetro. Al suo posto è accettato
    l'id di una connessione che nel progetto ha un solo perimetro: è la
    compatibilità, per un rilascio, con gli indirizzi salvati quando il
    dettaglio si apriva per connessione; con più perimetri la scelta sarebbe
    arbitraria e la richiesta è rifiutata. Gli id di perimetro e di connessione
    condividono lo stesso spazio di valori: se un numero tocca sia un perimetro
    sia una connessione selezionata in questo progetto, le due letture sono
    confrontate, e solo se indicano perimetri diversi la richiesta è rifiutata;
    se coincidono (il caso comune quando le due sequenze avanzano insieme) si
    apre quel perimetro senza ambiguità. Un riferimento testuale è l'alias del
    perimetro o il nome della connessione: l'alias esatto prevale, e a parità
    vince il perimetro più vecchio.
    """
    by_id = isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit())
    pool = await get_pool()
    async with pool.acquire() as conn:
        if by_id:
            value: Any = int(ref)
            scope_row = await conn.fetchrow(
                f"{_PROJECT_SELECT} WHERE c.org_id = $1 AND pds.id = $3",
                org_id, project_id, value,
            )
            # Il fallback sull'id di connessione è il percorso di compatibilità
            # con gli indirizzi salvati prima di questo blocco: sparirà quando
            # le rotte passeranno sempre l'id del perimetro. Gli id dei due
            # spazi (perimetro e connessione) condividono lo stesso range: in un
            # sistema nuovo le due sequenze avanzano insieme, quindi lo stesso
            # numero spesso apre lo stesso perimetro da entrambe le letture, ed
            # è una coincidenza innocua. Solo quando le due letture indicano
            # perimetri diversi il numero non decide da solo quale sia, e va
            # rifiutato invece di scegliere a caso.
            conn_rows = await conn.fetch(
                f"{_PROJECT_SELECT} WHERE c.org_id = $1 AND c.id = $3 ORDER BY pds.id",
                org_id, project_id, value,
            )
            if len(conn_rows) > 1:
                aliases = ", ".join(r["alias"] for r in conn_rows)
                raise ConnectionAmbiguousError(
                    f"Database connection #{value} has {len(conn_rows)} scopes in this project "
                    f"({aliases}): open the data source by its own id or alias."
                )
            if (
                scope_row is not None
                and conn_rows
                and conn_rows[0]["scope_id"] != scope_row["scope_id"]
            ):
                raise ConnectionAmbiguousError(
                    f"Id {value} is ambiguous in this project: it matches both the scope "
                    f"'{scope_row['alias']}' and the connection '{conn_rows[0]['name']}'. "
                    "Pass the scope id explicitly."
                )
            row = scope_row if scope_row is not None else (conn_rows[0] if conn_rows else None)
            catalog_column, catalog_value = "c.id", value
        else:
            value = ref
            row = await conn.fetchrow(
                f"{_PROJECT_SELECT} WHERE c.org_id = $1 "
                "AND (lower(pds.alias) = lower($3) OR c.name = $3) "
                "ORDER BY (lower(pds.alias) = lower($3)) DESC, pds.id LIMIT 1",
                org_id, project_id, value,
            )
            catalog_column, catalog_value = "c.name", value
        in_catalog = None
        if row is None:
            in_catalog = await conn.fetchval(
                f"SELECT 1 FROM db_connections c WHERE c.org_id = $1 AND {catalog_column} = $2",
                org_id, catalog_value,
            )
    if row is not None:
        return _record_to_dict(row, include_secret=include_secret)
    if in_catalog:
        raise ConnectionNotSelectedError(
            f"Database connection '{ref}' is not available in this project. "
            "Select it from the catalog first (catalog_list, resource_select)."
        )
    connections = await list_connections(org_id, project_id)
    raise ConnectionNotFoundError(_connection_not_found_message(ref, connections))


async def get_connection(
    org_id: int, project_id: int, ref: int | str, include_secret: bool = False
) -> dict[str, Any]:
    """Sinonimo di ``get_scope`` per i chiamanti che parlano di connessione."""
    return await get_scope(org_id, project_id, ref, include_secret=include_secret)


async def get_scope_by_id(
    org_id: int, project_id: int, scope_id: int, include_secret: bool = False
) -> dict[str, Any]:
    """The project's scope with exactly this id: no alias, name or connection-id fallback."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"{_PROJECT_SELECT} WHERE c.org_id = $1 AND pds.id = $3",
            org_id, project_id, int(scope_id),
        )
    if row is None:
        raise ConnectionNotFoundError(f"Data source #{scope_id} is not in this project")
    return _record_to_dict(row, include_secret=include_secret)


async def list_catalog_connections(org_id: int) -> list[dict[str, Any]]:
    """Connessioni del catalogo, ciascuna con il numero di perimetri che i
    progetti ne hanno collegato."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT q.*, (SELECT count(*) FROM project_db_scopes s WHERE s.db_connection_id = q.id) "
            f"AS scope_count FROM ({_CATALOG_SELECT} WHERE c.org_id = $1) q ORDER BY lower(q.name)",
            org_id,
        )
    return [_record_to_dict(r) for r in rows]


async def get_catalog_connection(
    org_id: int, connection_id: int, include_secret: bool = False
) -> dict[str, Any]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"{_CATALOG_SELECT} WHERE c.org_id = $1 AND c.id = $2", org_id, connection_id
        )
    if row is None:
        raise ConnectionNotFoundError(f"Database connection '{connection_id}' not found")
    return _record_to_dict(row, include_secret=include_secret)


async def find_connection(
    org_id: int,
    ssh_machine_id: Optional[int],
    host: Optional[str],
    port: Optional[int],
    username: Optional[str],
    engine: Optional[str],
) -> Optional[dict[str, Any]]:
    """Connessione già censita verso lo stesso server, per la stessa via, con
    la stessa utenza e con lo stesso motore. Il database non conta: lo sceglie
    il perimetro del progetto, e un secondo database dello stesso server non è
    una seconda connessione. Il motore invece conta: due motori sullo stesso
    indirizzo sono due server, e il perimetro ha una forma diversa su ciascuno."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""{_CONN_SELECT}
             WHERE c.org_id = $1
               AND c.ssh_machine_id IS NOT DISTINCT FROM $2
               AND c.host IS NOT DISTINCT FROM $3
               AND c.port IS NOT DISTINCT FROM $4
               AND c.username IS NOT DISTINCT FROM $5
               AND lower(c.engine) IS NOT DISTINCT FROM lower($6)
             ORDER BY c.id LIMIT 1""",
            org_id, ssh_machine_id, host, port, username, engine,
        )
    return _record_to_dict(row) if row else None


async def find_project_scope(
    org_id: int,
    project_id: int,
    connection_id: int,
    database: Optional[str],
    schema: Optional[str],
) -> Optional[dict[str, Any]]:
    """Perimetro del progetto su quella connessione con quel database e schema,
    confrontati nella forma canonica del motore; ``None`` se non c'è o se la
    coppia non ha una forma valida."""
    for row in await list_connections(org_id, project_id):
        if row["id"] != connection_id:
            continue
        try:
            wanted = normalize_scope(row["engine"], database, schema)
        except ScopeShapeError:
            return None
        if (row.get("scope_database"), row.get("scope_schema")) == wanted:
            return row
    return None


async def _check_payload(conn, org_id: int, data: dict[str, Any]) -> None:
    if data.get("engine") not in engines.SUPPORTED_ENGINES:
        raise ValueError(
            f"Unsupported engine '{data.get('engine')}'. "
            f"Supported: {', '.join(engines.SUPPORTED_ENGINES)}"
        )
    machine_id = data.get("ssh_machine_id")
    if machine_id is not None and not await conn.fetchval(
        "SELECT 1 FROM machines WHERE id = $1 AND org_id = $2", machine_id, org_id
    ):
        raise ValueError("Machine not found in this organization")


async def create_connection(org_id: int, data: dict[str, Any]) -> dict[str, Any]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _check_payload(conn, org_id, data)
        connection_id = await conn.fetchval(
            """
            INSERT INTO db_connections
                (org_id, name, engine, host, port, database_name, username, password_enc,
                 options, description, ssh_machine_id, restricted)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10, $11, $12)
            RETURNING id
            """,
            org_id,
            data["name"],
            data["engine"],
            data.get("host"),
            data.get("port"),
            data.get("database_name"),
            data.get("username"),
            encrypt_secret(data.get("password") or ""),
            json.dumps(data.get("options") or {}),
            data.get("description"),
            data.get("ssh_machine_id"),
            bool(data.get("restricted")),
        )
    return await get_catalog_connection(org_id, connection_id)


async def update_connection(org_id: int, connection_id: int, data: dict[str, Any]) -> dict[str, Any]:
    existing = await get_catalog_connection(org_id, connection_id, include_secret=True)
    # Password vuota nel payload = mantieni quella memorizzata.
    password_enc = encrypt_secret(data["password"]) if data.get("password") else existing.get("password_enc") or ""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _check_payload(conn, org_id, data)
        updated = await conn.fetchval(
            """
            UPDATE db_connections
               SET name = $3, engine = $4, host = $5, port = $6, database_name = $7, username = $8,
                   password_enc = $9, options = $10::jsonb, description = $11,
                   ssh_machine_id = $12, restricted = $13,
                   status = 'unknown', error_message = NULL, updated_at = NOW()
             WHERE org_id = $1 AND id = $2
            RETURNING id
            """,
            org_id,
            connection_id,
            data["name"],
            data["engine"],
            data.get("host"),
            data.get("port"),
            data.get("database_name"),
            data.get("username"),
            password_enc,
            json.dumps(data.get("options") or {}),
            data.get("description"),
            data.get("ssh_machine_id"),
            bool(data.get("restricted")),
        )
    if updated is None:
        raise ConnectionNotFoundError(f"Database connection '{connection_id}' not found")
    engines.dispose_engine(connection_id)
    return await get_catalog_connection(org_id, connection_id)


async def delete_connection(org_id: int, connection_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        deleted = await conn.fetchval(
            "DELETE FROM db_connections WHERE org_id = $1 AND id = $2 RETURNING id", org_id, connection_id
        )
    if deleted is None:
        raise ConnectionNotFoundError(f"Database connection '{connection_id}' not found")
    engines.dispose_engine(connection_id)


# --------------------------------------------------------------------------- #
# Engine resolution
# --------------------------------------------------------------------------- #
async def _resolve_engine(record: dict[str, Any]) -> sa.engine.Engine:
    password = decrypt_secret(record.get("password_enc") or "")
    host = record.get("host")
    port = record.get("port")
    if record.get("ssh_enabled"):
        # Dietro bastion: il DB si raggiunge attraverso un forward SSH locale.
        ssh_cfg = {
            "host": record.get("ssh_host"),
            "port": record.get("ssh_port") or 22,
            "username": record.get("ssh_username"),
            "auth_method": record.get("ssh_auth_method"),
            "password": decrypt_secret(record.get("ssh_password_enc") or ""),
            "private_key": decrypt_secret(record.get("ssh_private_key_enc") or ""),
        }
        host, port = engines.ensure_tunnel(record["id"], ssh_cfg, host, port)
    database, schema = _effective_scope(record)
    url = engines.build_url(
        engine=record["engine"],
        host=host,
        port=port,
        database=database,
        username=record.get("username"),
        password=password,
        options=record.get("options") or {},
        extra_query=connect_options(record["engine"], schema),
    )
    return engines.get_engine(
        record["id"], record["engine"], url, _scope_key(record)
    )


def _effective_scope(record: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """Perimetro applicato davvero: (database della URL, schema o ``None``).

    Solo un perimetro confermato restringe la connessione: impone il proprio
    database, il proprio schema come search_path e come schema di default
    dell'introspezione, e lo schema finisce nel log delle query. Un perimetro
    dedotto, come la connessione letta dal catalogo senza progetto, segue la
    connessione così com'è oggi: il suo database attuale e nessuno schema.

    Fail-closed: solo ``scope_inferred`` esplicitamente ``True`` conta come
    dedotto. Un record che non porta affatto la colonna (non capita su una
    riga vera, che la porta sempre) conta come confermato, non come dedotto,
    così un valore mancante restringe invece di aprire. ``engine`` si legge
    solo nel ramo che ne ha bisogno: il ramo dedotto non lo tocca, e un
    record parziale che dichiara solo di essere dedotto non solleva un
    errore che non gli spetta.
    """
    if record.get("scope_inferred") is True:
        return record.get("database_name"), None
    engine = record["engine"]
    scope_database = record.get("scope_database")
    return (
        url_database(engine, record.get("database_name"), scope_database),
        introspection_schema(engine, scope_database, record.get("scope_schema")),
    )


def effective_scope(record: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """Public form of ``_effective_scope``: (database, schema) the scope applies."""
    return _effective_scope(record)


def _scope_key(record: dict[str, Any]) -> tuple:
    """Chiave di cache dell'engine: il perimetro su cui la connessione è aperta."""
    return _effective_scope(record)


def _confines(record: dict[str, Any]) -> bool:
    """Vero se il perimetro è confermato e ha uno schema da imporre.

    Fail-closed come ``_effective_scope``: conta come dedotto solo un
    ``scope_inferred`` esplicitamente ``True``, così un record che non porta la
    colonna resta confermato in tutte le guardie e non se ne apre una sola.
    """
    return record.get("scope_inferred") is not True and _effective_scope(record)[1] is not None


async def _scope_violation(
    org_id: int, project_id: int, record: dict[str, Any], detail: str
) -> ScopeViolationError:
    """Errore che nomina il perimetro del collegamento, elenca gli alias del
    progetto e indica come aggiungere il perimetro che manca."""
    aliases = [c.get("alias") or c["name"] for c in await list_connections(org_id, project_id)]
    alias = record.get("alias") or record["name"]
    label = record.get("scope_label") or scope_label(
        record["engine"], record.get("scope_database"), record.get("scope_schema")
    )
    message = (
        f"{detail} The data source '{alias}' is limited to the scope '{label}'. "
        f"Data sources of this project: {', '.join(aliases)}. "
        "To work on another scope, add it to the project with catalog_list and resource_select."
    )
    return ScopeViolationError(message, alias, label, aliases)


async def _requested_schema(
    org_id: int, project_id: int, record: dict[str, Any], schema: Optional[str]
) -> Optional[str]:
    """Schema su cui lavora la richiesta.

    Senza schema esplicito vale quello del perimetro. Un perimetro confermato
    ammette soltanto il proprio schema, confrontato senza distinguere le
    maiuscole come fa il validator sui nomi nudi, e lavora comunque sulla forma
    scritta nel perimetro; uno dedotto segue la connessione e accetta lo schema
    chiesto così come arriva.
    """
    scoped = _effective_scope(record)[1]
    requested = (schema or "").strip() or None
    if requested is None:
        return scoped
    if _confines(record):
        if requested.lower() != (scoped or "").lower():
            raise await _scope_violation(
                org_id, project_id, record, f"The schema '{requested}' is outside the scope."
            )
        return scoped
    return requested


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
_DOCKER_HOST_ALIAS = "host.docker.internal"


def _running_in_container() -> bool:
    return os.path.exists("/.dockerenv")


async def _probe_alternate_host(record: dict[str, Any], host: str) -> bool:
    """Check whether the connection would work with a different host."""
    url = engines.build_url(
        engine=record["engine"],
        host=host,
        port=record.get("port"),
        database=record.get("database_name"),
        username=record.get("username"),
        password=decrypt_secret(record.get("password_enc") or ""),
        options=record.get("options") or {},
    )
    try:
        await asyncio.wait_for(
            asyncio.to_thread(engines.probe_url, record["engine"], url), timeout=10
        )
        return True
    except Exception:  # noqa: BLE001
        return False


async def list_available_scopes(org_id: int, connection_id: int, refresh: bool) -> dict[str, Any]:
    """Inoltra a ``scope_catalog.list_available_scopes``.

    L'import resta qui dentro perché ``scope_catalog`` risale a questo modulo
    per risolvere l'engine: importarlo in testa al file creerebbe un ciclo.
    Il nome vive qui, sul modulo, così ``test_connection`` lo richiama e lo
    si può sostituire nei test senza toccare l'implementazione vera.
    """
    from .scope_catalog import list_available_scopes as _list_available_scopes

    return await _list_available_scopes(org_id, connection_id, refresh)


async def test_connection(org_id: int, connection_id: int) -> dict[str, Any]:
    """Try to connect; persist the resulting status on the connection row.

    When the server runs inside a container and a loopback host fails, it also
    probes ``host.docker.internal`` (the host machine, where sibling containers
    publish their ports) and returns it as ``suggested_host`` if reachable.
    """
    record = await get_catalog_connection(org_id, connection_id, include_secret=True)
    status, error, suggested_host = "ok", None, None
    try:
        engine = await _resolve_engine(record)
        await asyncio.wait_for(asyncio.to_thread(engines.ping, engine), timeout=10)
    except Exception as e:  # noqa: BLE001
        status, error = "error", str(e)

    host = (record.get("host") or "").strip().lower()
    if status == "error" and host in _LOOPBACK_HOSTS and _running_in_container() and not record.get("ssh_enabled"):
        if await _probe_alternate_host(record, _DOCKER_HOST_ALIAS):
            suggested_host = _DOCKER_HOST_ALIAS
            error = (
                f"'{record.get('host')}' points to the context-forge container itself, "
                f"not to the machine it runs on. The same connection succeeded via "
                f"'{_DOCKER_HOST_ALIAS}' — update the host to fix it."
            )

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE db_connections SET status = $3, error_message = $4, last_checked_at = NOW() "
            "WHERE org_id = $1 AND id = $2",
            org_id, connection_id, status, error,
        )
    if status == "ok":
        # Un test riuscito aggiorna anche i perimetri visti sulla connessione;
        # un loro fallimento (anche locale) resta nella fotografia e non deve
        # cambiare l'esito del test, già calcolato e già salvato sopra.
        try:
            await list_available_scopes(org_id, connection_id, refresh=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("scope refresh after connection test failed: %s", exc)
    return {"status": status, "error": error, "suggested_host": suggested_host}


# --------------------------------------------------------------------------- #
# Annotations (data dictionary)
# --------------------------------------------------------------------------- #
async def list_annotations(connection_id: int) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT schema_name, table_name, column_name, description "
            "FROM db_annotations WHERE connection_id=$1 "
            "ORDER BY schema_name, table_name, column_name",
            connection_id,
        )
    return [dict(r) for r in rows]


async def upsert_annotations(connection_id: int, items: list[dict[str, Any]]) -> int:
    """Insert/update annotations; an empty description deletes the entry."""
    pool = await get_pool()
    written = 0
    async with pool.acquire() as conn:
        async with conn.transaction():
            for item in items:
                schema_name = item.get("schema_name") or ""
                table_name = item["table_name"]
                column_name = item.get("column_name") or ""
                description = (item.get("description") or "").strip()
                if not description:
                    await conn.execute(
                        "DELETE FROM db_annotations WHERE connection_id=$1 "
                        "AND schema_name=$2 AND table_name=$3 AND column_name=$4",
                        connection_id,
                        schema_name,
                        table_name,
                        column_name,
                    )
                else:
                    await conn.execute(
                        """
                        INSERT INTO db_annotations
                            (connection_id, schema_name, table_name, column_name, description)
                        VALUES ($1, $2, $3, $4, $5)
                        ON CONFLICT (connection_id, schema_name, table_name, column_name)
                        DO UPDATE SET description = EXCLUDED.description, updated_at = NOW()
                        """,
                        connection_id,
                        schema_name,
                        table_name,
                        column_name,
                        description,
                    )
                written += 1
    return written


async def scope_annotations(
    org_id: int, project_id: int, ref: int | str
) -> list[dict[str, Any]]:
    """Annotazioni viste attraverso un perimetro.

    Le annotazioni sono della connessione: un perimetro confermato vede solo
    quelle del proprio schema e quelle scritte senza schema, un perimetro
    dedotto le vede tutte, come la connessione.
    """
    record = await get_scope(org_id, project_id, ref)
    annotations = await list_annotations(record["id"])
    if _confines(record):
        scoped = _effective_scope(record)[1]
        annotations = [a for a in annotations if a["schema_name"] in ("", scoped)]
    return annotations


async def save_scope_annotations(
    org_id: int, project_id: int, ref: int | str, items: list[dict[str, Any]]
) -> int:
    """Scrive annotazioni attraverso un perimetro.

    Per un perimetro confermato uno schema mancante diventa quello del
    perimetro e uno schema diverso è rifiutato prima di scrivere; un perimetro
    dedotto scrive gli schemi così come arrivano.

    Da un perimetro confermato il salvataggio porta anche via il gemello senza
    schema di ogni riga scritta o cancellata: quel gemello vale come jolly per
    ogni perimetro della connessione, quindi sopravvivergli lascerebbe la
    descrizione visibile agli altri perimetri e farebbe ricomparire al
    ricaricamento una descrizione appena cancellata.
    """
    record = await get_scope(org_id, project_id, ref)
    scoped = _effective_scope(record)[1]
    confined = _confines(record)
    normalized = []
    for item in items:
        schema_name = (item.get("schema_name") or "").strip()
        if confined:
            if not schema_name:
                schema_name = scoped
            elif schema_name != scoped:
                raise await _scope_violation(
                    org_id, project_id, record,
                    f"The schema '{schema_name}' is outside the scope.",
                )
        normalized.append({**item, "schema_name": schema_name})
    written = await upsert_annotations(record["id"], normalized)
    if confined:
        await _delete_default_schema_twins(record["id"], normalized)
    return written


async def _delete_default_schema_twins(
    connection_id: int, items: list[dict[str, Any]]
) -> None:
    """Toglie le annotazioni scritte senza schema delle stesse tabelle e colonne."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            for item in items:
                await conn.execute(
                    "DELETE FROM db_annotations WHERE connection_id=$1 AND schema_name='' "
                    "AND table_name=$2 AND column_name=$3",
                    connection_id,
                    item["table_name"],
                    item.get("column_name") or "",
                )


async def _annotation_maps(
    connection_id: int, schema_name: Optional[str]
) -> tuple[dict[str, str], dict[tuple[str, str], str]]:
    """Return ({table: description}, {(table, column): description}) for a schema.

    Annotations saved without a schema ("" = default schema) match any schema
    so simple single-schema databases don't need to spell it out.
    """
    annotations = await list_annotations(connection_id)
    table_map: dict[str, str] = {}
    column_map: dict[tuple[str, str], str] = {}
    for a in annotations:
        if a["schema_name"] not in ("", schema_name):
            continue
        if a["column_name"]:
            column_map[(a["table_name"], a["column_name"])] = a["description"]
        else:
            table_map[a["table_name"]] = a["description"]
    return table_map, column_map


# --------------------------------------------------------------------------- #
# Schema context
# --------------------------------------------------------------------------- #
async def schema_overview(
    org_id: int, project_id: int, ref: int | str, schema: Optional[str] = None
) -> dict[str, Any]:
    record = await get_scope(org_id, project_id, ref, include_secret=True)
    target = await _requested_schema(org_id, project_id, record, schema)
    engine = await _resolve_engine(record)
    overview = await asyncio.wait_for(
        asyncio.to_thread(introspect.get_overview, engine, target),
        timeout=QUERY_TIMEOUT_SECONDS * 2,
    )
    if _confines(record):
        # Un perimetro confermato non rivela i nomi degli altri schemi del server.
        overview["schemas"] = [overview["schema"]]
    table_map, _ = await _annotation_maps(record["id"], overview["schema"])
    for t in overview["tables"]:
        t["description"] = table_map.get(t["name"])
    # L'alias nomina il perimetro usato dal progetto; il fallback sul nome
    # serve solo per le righe di catalogo, che non hanno un perimetro.
    overview["connection"] = record.get("alias") or record["name"]
    overview["connection_id"] = record["id"]
    overview["alias"] = record.get("alias")
    overview["scope_label"] = record.get("scope_label")
    overview["scope_inferred"] = record.get("scope_inferred")
    return overview


async def describe_table(
    org_id: int,
    project_id: int,
    ref: int | str,
    table: str,
    schema: Optional[str] = None,
    sample_rows: int = 0,
) -> dict[str, Any]:
    record = await get_scope(org_id, project_id, ref, include_secret=True)
    target = await _requested_schema(org_id, project_id, record, schema)
    engine = await _resolve_engine(record)
    detail = await asyncio.wait_for(
        asyncio.to_thread(introspect.describe_table, engine, table, target),
        timeout=QUERY_TIMEOUT_SECONDS * 2,
    )
    table_map, column_map = await _annotation_maps(record["id"], detail["schema"])
    detail["description"] = table_map.get(table)
    for col in detail["columns"]:
        col["description"] = column_map.get((table, col["name"]))
    # L'alias nomina il perimetro usato dal progetto; il fallback sul nome
    # serve solo per le righe di catalogo, che non hanno un perimetro.
    detail["connection"] = record.get("alias") or record["name"]
    detail["connection_id"] = record["id"]

    if sample_rows > 0:
        qualified = introspect.quote_identifier(engine, table)
        if detail["schema"]:
            qualified = f"{introspect.quote_identifier(engine, detail['schema'])}.{qualified}"
        n = max(1, min(int(sample_rows), 10))
        try:
            sample = await run_query(
                org_id, project_id, ref, f"SELECT * FROM {qualified} LIMIT {n}",
                max_rows=n, source="sample",
            )
            detail["sample_rows"] = sample.get("rows", [])
        except Exception as e:  # noqa: BLE001
            detail["sample_rows"] = []
            detail["sample_error"] = str(e)
    return detail


# --------------------------------------------------------------------------- #
# Read-only query execution
# --------------------------------------------------------------------------- #
def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, str) and len(value) > MAX_CELL_CHARS:
            return value[:MAX_CELL_CHARS] + "…"
        return value
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, datetime.timedelta):
        return value.total_seconds()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return "base64:" + base64.b64encode(bytes(value)[:512]).decode("ascii")
    return str(value)[:MAX_CELL_CHARS]


def _execute_readonly(
    engine: sa.engine.Engine, sql: str, max_rows: int
) -> tuple[list[str], list[dict[str, Any]], bool]:
    """Run a validated statement on a worker thread; returns (columns, rows, truncated)."""
    with engine.connect() as conn:
        # Belt and braces: a server-side timeout where the dialect supports it,
        # in addition to the asyncio.wait_for backstop around this thread.
        try:
            if engine.dialect.name == "postgresql":
                conn.exec_driver_sql(f"SET statement_timeout = {QUERY_TIMEOUT_SECONDS * 1000}")
            elif engine.dialect.name == "mysql":
                conn.exec_driver_sql(f"SET SESSION MAX_EXECUTION_TIME = {QUERY_TIMEOUT_SECONDS * 1000}")
        except Exception:  # noqa: BLE001 - unsupported on some versions (e.g. MariaDB)
            pass

        result = conn.execute(sa.text(sql))
        if not result.returns_rows:
            return [], [], False
        columns = list(result.keys())
        fetched = result.fetchmany(max_rows + 1)
        truncated = len(fetched) > max_rows
        rows = [
            {col: _json_safe(val) for col, val in zip(columns, row)}
            for row in fetched[:max_rows]
        ]
        return columns, rows, truncated


async def run_query(
    org_id: int,
    project_id: int,
    ref: int | str,
    sql: str,
    max_rows: int = 100,
    source: str = "mcp",
) -> dict[str, Any]:
    record = await get_scope(org_id, project_id, ref, include_secret=True)
    max_rows = max(1, min(int(max_rows), MAX_ROWS_HARD_CAP))

    try:
        # Lo schema consentito è quello del perimetro confermato; un perimetro
        # dedotto non lo impone, ma i cataloghi di sistema restano esclusi.
        validated = validate_query(
            sql, max_rows=max_rows, allowed_schema=_effective_scope(record)[1]
        )
    except ScopeReferenceError as e:
        refused = await _scope_violation(org_id, project_id, record, str(e))
        if source != "sample":
            await _log_query(record, org_id, project_id, source, sql, False, str(refused), 0, 0)
        raise refused from e
    except QueryValidationError as e:
        if source != "sample":
            await _log_query(record, org_id, project_id, source, sql, False, str(e), 0, 0)
        raise
    engine = await _resolve_engine(record)

    started = time.monotonic()
    success, error = True, None
    columns: list[str] = []
    rows: list[dict[str, Any]] = []
    truncated = False
    try:
        columns, rows, truncated = await asyncio.wait_for(
            asyncio.to_thread(_execute_readonly, engine, validated, max_rows),
            timeout=QUERY_TIMEOUT_SECONDS * 2,
        )
    except asyncio.TimeoutError:
        success, error = False, f"Query timed out after {QUERY_TIMEOUT_SECONDS * 2}s"
    except Exception as e:  # noqa: BLE001
        success, error = False, str(e)
    duration_ms = int((time.monotonic() - started) * 1000)

    if source != "sample":
        await _log_query(record, org_id, project_id, source, validated,
                         success, error, len(rows), duration_ms)

    if not success:
        raise RuntimeError(error)

    return {
        # L'alias nomina il perimetro usato dal progetto; il fallback sul nome
        # serve solo per le righe di catalogo, che non hanno un perimetro.
        "connection": record.get("alias") or record["name"],
        "sql": validated,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "duration_ms": duration_ms,
    }


async def _log_query(record, org_id, project_id, source, sql, success, error,
                     rows_returned, duration_ms) -> None:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO db_query_log (connection_id, org_id, project_id, source, sql_text, "
                "success, error_message, rows_returned, duration_ms, schema_name) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
                record["id"], org_id, project_id, source, sql,
                success, error, rows_returned, duration_ms,
                _effective_scope(record)[1],
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("db_query_log insert failed: %s", e)


def _execute_write(engine, sql: str) -> int:
    """Execute a validated DML statement in its own transaction; returns rowcount."""
    with engine.begin() as conn:
        # The DB-side timeout is authoritative here: it runs inside the same
        # transaction as the DML, so a timeout aborts and rolls back the write
        # instead of leaving Python guessing about an outcome it can't observe.
        try:
            if engine.dialect.name == "postgresql":
                conn.exec_driver_sql(f"SET statement_timeout = {QUERY_TIMEOUT_SECONDS * 1000}")
            elif engine.dialect.name == "mysql":
                conn.exec_driver_sql(f"SET SESSION MAX_EXECUTION_TIME = {QUERY_TIMEOUT_SECONDS * 1000}")
        except Exception:  # noqa: BLE001 - unsupported on some versions (e.g. MariaDB)
            pass

        result = conn.execute(sa.text(sql))
        return int(result.rowcount if result.rowcount is not None else 0)


async def run_write(
    org_id: int,
    project_id: int,
    ref: int | str,
    sql: str,
    source: str = "mcp",
    *,
    by_scope_id: bool = False,
) -> dict[str, Any]:
    # by_scope_id: ``ref`` is a scope id and nothing else (approved writes).
    if by_scope_id:
        record = await get_scope_by_id(org_id, project_id, int(ref), include_secret=True)
    else:
        record = await get_scope(org_id, project_id, ref, include_secret=True)
    try:
        validated = validate_write_query(sql, allowed_schema=_effective_scope(record)[1])
    except ScopeReferenceError as e:
        refused = await _scope_violation(org_id, project_id, record, str(e))
        await _log_query(record, org_id, project_id, source, sql, False, str(refused), 0, 0)
        raise refused from e
    except QueryValidationError as e:
        await _log_query(record, org_id, project_id, source, sql, False, str(e), 0, 0)
        raise
    engine = await _resolve_engine(record)

    started = time.monotonic()
    success, error = True, None
    rowcount = 0
    try:
        rowcount = await asyncio.to_thread(_execute_write, engine, validated)
    except Exception as e:  # noqa: BLE001
        success, error = False, str(e)
    duration_ms = int((time.monotonic() - started) * 1000)

    await _log_query(record, org_id, project_id, source, validated,
                     success, error, rowcount, duration_ms)
    if not success:
        raise RuntimeError(error)
    return {
        # L'alias nomina il perimetro usato dal progetto; il fallback sul nome
        # serve solo per le righe di catalogo, che non hanno un perimetro.
        "connection": record.get("alias") or record["name"],
        "sql": validated,
        "row_count": rowcount,
        "duration_ms": duration_ms,
    }


async def query_log(
    org_id: int, project_id: int, connection_id: int, limit: int = 50
) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, source, sql_text, success, error_message, rows_returned, "
            "duration_ms, schema_name, created_at FROM db_query_log "
            "WHERE org_id=$1 AND project_id=$2 AND connection_id=$3 ORDER BY created_at DESC LIMIT $4",
            org_id, project_id, connection_id, limit,
        )
    out = []
    for r in rows:
        d = dict(r)
        if d.get("created_at"):
            d["created_at"] = d["created_at"].isoformat()
        out.append(d)
    return out


async def mark_pending_secret(org_id: int, connection_id: int) -> None:
    """Segna la connessione come censita ma priva di credenziale."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE db_connections
               SET status = 'pending_secret',
                   error_message = 'Credential not set: add the password from the Catalog page',
                   updated_at = NOW()
             WHERE org_id = $1 AND id = $2
            """,
            org_id,
            connection_id,
        )
