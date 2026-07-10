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
import time
import uuid
from typing import Any, Optional

import sqlalchemy as sa

from ..db import get_pool
from . import engines, introspect
from .secrets import decrypt_secret, encrypt_secret
from .validator import QueryValidationError, validate_query

logger = logging.getLogger(__name__)

QUERY_TIMEOUT_SECONDS = 15
MAX_ROWS_HARD_CAP = 500
MAX_CELL_CHARS = 2000

_CONN_FIELDS = (
    "id, org_id, name, engine, host, port, database_name, username, password_enc, "
    "options, description, status, error_message, last_checked_at, created_at, updated_at"
)


class ConnectionNotFoundError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Connection CRUD (metadata store)
# --------------------------------------------------------------------------- #
def _record_to_dict(row: Any, include_secret: bool = False) -> dict[str, Any]:
    d = dict(row)
    if isinstance(d.get("options"), str):
        d["options"] = json.loads(d["options"] or "{}")
    d["has_password"] = bool(d.get("password_enc"))
    if not include_secret:
        d.pop("password_enc", None)
    for key in ("last_checked_at", "created_at", "updated_at"):
        if d.get(key) is not None:
            d[key] = d[key].isoformat()
    return d


async def list_connections(org_id: int) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {_CONN_FIELDS} FROM db_connections WHERE org_id=$1 ORDER BY name",
            org_id,
        )
        counts = await conn.fetch(
            """
            SELECT c.id, count(a.id) AS annotation_count
            FROM db_connections c
            LEFT JOIN db_annotations a ON a.connection_id = c.id
            WHERE c.org_id = $1
            GROUP BY c.id
            """,
            org_id,
        )
    count_map = {r["id"]: r["annotation_count"] for r in counts}
    out = []
    for r in rows:
        d = _record_to_dict(r)
        d["annotation_count"] = count_map.get(r["id"], 0)
        out.append(d)
    return out


async def get_connection(org_id: int, ref: int | str, include_secret: bool = False) -> dict[str, Any]:
    """Fetch a connection by id (int) or name (str) within the organization."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
            row = await conn.fetchrow(
                f"SELECT {_CONN_FIELDS} FROM db_connections WHERE org_id=$1 AND id=$2",
                org_id,
                int(ref),
            )
        else:
            row = await conn.fetchrow(
                f"SELECT {_CONN_FIELDS} FROM db_connections WHERE org_id=$1 AND name=$2",
                org_id,
                ref,
            )
    if row is None:
        raise ConnectionNotFoundError(f"Database connection '{ref}' not found")
    return _record_to_dict(row, include_secret=include_secret)


async def create_connection(org_id: int, data: dict[str, Any]) -> dict[str, Any]:
    if data.get("engine") not in engines.SUPPORTED_ENGINES:
        raise ValueError(
            f"Unsupported engine '{data.get('engine')}'. "
            f"Supported: {', '.join(engines.SUPPORTED_ENGINES)}"
        )
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            INSERT INTO db_connections
                (org_id, name, engine, host, port, database_name, username,
                 password_enc, options, description)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10)
            RETURNING {_CONN_FIELDS}
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
        )
    return _record_to_dict(row)


async def update_connection(org_id: int, connection_id: int, data: dict[str, Any]) -> dict[str, Any]:
    existing = await get_connection(org_id, connection_id, include_secret=True)
    if data.get("engine") not in engines.SUPPORTED_ENGINES:
        raise ValueError(f"Unsupported engine '{data.get('engine')}'")

    # Empty password in the payload means "keep the stored one".
    if data.get("password"):
        password_enc = encrypt_secret(data["password"])
    else:
        password_enc = existing.get("password_enc") or ""

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE db_connections
            SET name=$3, engine=$4, host=$5, port=$6, database_name=$7, username=$8,
                password_enc=$9, options=$10::jsonb, description=$11,
                status='unknown', error_message=NULL, updated_at=NOW()
            WHERE org_id=$1 AND id=$2
            RETURNING {_CONN_FIELDS}
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
        )
    if row is None:
        raise ConnectionNotFoundError(f"Database connection '{connection_id}' not found")
    engines.dispose_engine(connection_id)
    return _record_to_dict(row)


async def delete_connection(org_id: int, connection_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        deleted = await conn.fetchval(
            "DELETE FROM db_connections WHERE org_id=$1 AND id=$2 RETURNING id",
            org_id,
            connection_id,
        )
    if deleted is None:
        raise ConnectionNotFoundError(f"Database connection '{connection_id}' not found")
    engines.dispose_engine(connection_id)


# --------------------------------------------------------------------------- #
# Engine resolution
# --------------------------------------------------------------------------- #
async def _resolve_engine(record: dict[str, Any]) -> sa.engine.Engine:
    password = decrypt_secret(record.get("password_enc") or "")
    url = engines.build_url(
        engine=record["engine"],
        host=record.get("host"),
        port=record.get("port"),
        database=record.get("database_name"),
        username=record.get("username"),
        password=password,
        options=record.get("options") or {},
    )
    return engines.get_engine(record["id"], record["engine"], url)


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


async def test_connection(org_id: int, connection_id: int) -> dict[str, Any]:
    """Try to connect; persist the resulting status on the connection row.

    When the server runs inside a container and a loopback host fails, it also
    probes ``host.docker.internal`` (the host machine, where sibling containers
    publish their ports) and returns it as ``suggested_host`` if reachable.
    """
    record = await get_connection(org_id, connection_id, include_secret=True)
    status, error, suggested_host = "ok", None, None
    try:
        engine = await _resolve_engine(record)
        await asyncio.wait_for(asyncio.to_thread(engines.ping, engine), timeout=10)
    except Exception as e:  # noqa: BLE001
        status, error = "error", str(e)

    host = (record.get("host") or "").strip().lower()
    if status == "error" and host in _LOOPBACK_HOSTS and _running_in_container():
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
            "UPDATE db_connections SET status=$3, error_message=$4, last_checked_at=NOW() "
            "WHERE org_id=$1 AND id=$2",
            org_id,
            connection_id,
            status,
            error,
        )
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
async def schema_overview(org_id: int, ref: int | str, schema: Optional[str] = None) -> dict[str, Any]:
    record = await get_connection(org_id, ref, include_secret=True)
    engine = await _resolve_engine(record)
    overview = await asyncio.wait_for(
        asyncio.to_thread(introspect.get_overview, engine, schema),
        timeout=QUERY_TIMEOUT_SECONDS * 2,
    )
    table_map, _ = await _annotation_maps(record["id"], overview["schema"])
    for t in overview["tables"]:
        t["description"] = table_map.get(t["name"])
    overview["connection"] = record["name"]
    overview["connection_id"] = record["id"]
    return overview


async def describe_table(
    org_id: int,
    ref: int | str,
    table: str,
    schema: Optional[str] = None,
    sample_rows: int = 0,
) -> dict[str, Any]:
    record = await get_connection(org_id, ref, include_secret=True)
    engine = await _resolve_engine(record)
    detail = await asyncio.wait_for(
        asyncio.to_thread(introspect.describe_table, engine, table, schema),
        timeout=QUERY_TIMEOUT_SECONDS * 2,
    )
    table_map, column_map = await _annotation_maps(record["id"], detail["schema"])
    detail["description"] = table_map.get(table)
    for col in detail["columns"]:
        col["description"] = column_map.get((table, col["name"]))
    detail["connection"] = record["name"]
    detail["connection_id"] = record["id"]

    if sample_rows > 0:
        qualified = introspect.quote_identifier(engine, table)
        if detail["schema"]:
            qualified = f"{introspect.quote_identifier(engine, detail['schema'])}.{qualified}"
        n = max(1, min(int(sample_rows), 10))
        try:
            sample = await run_query(
                org_id, ref, f"SELECT * FROM {qualified} LIMIT {n}",
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
    ref: int | str,
    sql: str,
    max_rows: int = 100,
    source: str = "mcp",
) -> dict[str, Any]:
    record = await get_connection(org_id, ref, include_secret=True)
    max_rows = max(1, min(int(max_rows), MAX_ROWS_HARD_CAP))

    validated = validate_query(sql, max_rows=max_rows)
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
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO db_query_log (connection_id, org_id, source, sql_text, "
                    "success, error_message, rows_returned, duration_ms) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                    record["id"], org_id, source, validated,
                    success, error, len(rows), duration_ms,
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("db_query_log insert failed: %s", e)

    if not success:
        raise RuntimeError(error)

    return {
        "connection": record["name"],
        "sql": validated,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "duration_ms": duration_ms,
    }


async def query_log(org_id: int, connection_id: int, limit: int = 50) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, source, sql_text, success, error_message, rows_returned, "
            "duration_ms, created_at FROM db_query_log "
            "WHERE org_id=$1 AND connection_id=$2 ORDER BY created_at DESC LIMIT $3",
            org_id, connection_id, limit,
        )
    out = []
    for r in rows:
        d = dict(r)
        if d.get("created_at"):
            d["created_at"] = d["created_at"].isoformat()
        out.append(d)
    return out
