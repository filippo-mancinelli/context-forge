"""Schema introspection for external databases via the SQLAlchemy Inspector.

All functions here are synchronous (they hold a DBAPI connection) and are
executed via ``asyncio.to_thread`` by the service layer.

Two levels of context:
  - ``get_overview``  — shallow: schemas, tables/views, column counts, row estimates.
  - ``describe_table`` — deep: columns with types/defaults/comments, PK, FKs,
    indexes, unique constraints.

Row estimates use catalog statistics (``pg_class.reltuples``,
``information_schema.TABLES.TABLE_ROWS``) rather than COUNT(*) so the overview
stays cheap even on large databases.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

import sqlalchemy as sa
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


def _table_comment(insp: sa.Inspector, table: str, schema: Optional[str]) -> Optional[str]:
    try:
        return (insp.get_table_comment(table, schema=schema) or {}).get("text")
    except NotImplementedError:
        return None
    except Exception:  # noqa: BLE001 - comments are best-effort context
        return None


def _row_estimates(engine: Engine, schema: Optional[str]) -> dict[str, int]:
    """Best-effort row-count estimates per table from catalog statistics."""
    dialect = engine.dialect.name
    try:
        with engine.connect() as conn:
            if dialect == "postgresql":
                rows = conn.execute(
                    sa.text(
                        "SELECT c.relname, c.reltuples::bigint FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE c.relkind = 'r' AND n.nspname = :schema"
                    ),
                    {"schema": schema or "public"},
                ).fetchall()
                return {r[0]: max(int(r[1]), 0) for r in rows}
            if dialect == "mysql":
                rows = conn.execute(
                    sa.text(
                        "SELECT TABLE_NAME, TABLE_ROWS FROM information_schema.TABLES "
                        "WHERE TABLE_SCHEMA = COALESCE(:schema, DATABASE())"
                    ),
                    {"schema": schema},
                ).fetchall()
                return {r[0]: int(r[1] or 0) for r in rows}
    except Exception as e:  # noqa: BLE001
        logger.debug("row estimates unavailable: %s", e)
    return {}


def get_overview(engine: Engine, schema: Optional[str] = None) -> dict[str, Any]:
    insp = sa.inspect(engine)
    default_schema = insp.default_schema_name
    target_schema = schema or default_schema

    schemas: list[str] = []
    try:
        # Hide internal catalogs; they are noise for coding agents.
        hidden = {"information_schema", "pg_catalog", "pg_toast", "performance_schema", "mysql", "sys"}
        schemas = [s for s in insp.get_schema_names() if s not in hidden]
    except NotImplementedError:
        pass

    estimates = _row_estimates(engine, target_schema)

    tables: list[dict[str, Any]] = []
    for name in sorted(insp.get_table_names(schema=target_schema)):
        tables.append(
            {
                "name": name,
                "comment": _table_comment(insp, name, target_schema),
                "column_count": len(insp.get_columns(name, schema=target_schema)),
                "estimated_rows": estimates.get(name),
            }
        )

    views: list[str] = []
    try:
        views = sorted(insp.get_view_names(schema=target_schema))
    except NotImplementedError:
        pass

    return {
        "dialect": engine.dialect.name,
        "default_schema": default_schema,
        "schema": target_schema,
        "schemas": schemas,
        "tables": tables,
        "views": views,
    }


def describe_table(engine: Engine, table: str, schema: Optional[str] = None) -> dict[str, Any]:
    insp = sa.inspect(engine)
    target_schema = schema or insp.default_schema_name

    if not insp.has_table(table, schema=target_schema):
        raise ValueError(f"Table '{table}' not found in schema '{target_schema}'")

    columns: list[dict[str, Any]] = []
    for col in insp.get_columns(table, schema=target_schema):
        columns.append(
            {
                "name": col["name"],
                "type": str(col["type"]),
                "nullable": bool(col.get("nullable", True)),
                "default": str(col["default"]) if col.get("default") is not None else None,
                "comment": col.get("comment"),
                "autoincrement": col.get("autoincrement") in (True, "auto"),
            }
        )

    pk = insp.get_pk_constraint(table, schema=target_schema) or {}

    foreign_keys: list[dict[str, Any]] = []
    for fk in insp.get_foreign_keys(table, schema=target_schema):
        foreign_keys.append(
            {
                "columns": fk.get("constrained_columns", []),
                "referred_schema": fk.get("referred_schema"),
                "referred_table": fk.get("referred_table"),
                "referred_columns": fk.get("referred_columns", []),
            }
        )

    indexes: list[dict[str, Any]] = []
    try:
        for idx in insp.get_indexes(table, schema=target_schema):
            indexes.append(
                {
                    "name": idx.get("name"),
                    "columns": [c for c in idx.get("column_names", []) if c],
                    "unique": bool(idx.get("unique")),
                }
            )
    except NotImplementedError:
        pass

    uniques: list[dict[str, Any]] = []
    try:
        for uc in insp.get_unique_constraints(table, schema=target_schema):
            uniques.append({"name": uc.get("name"), "columns": uc.get("column_names", [])})
    except NotImplementedError:
        pass

    estimates = _row_estimates(engine, target_schema)

    return {
        "schema": target_schema,
        "table": table,
        "comment": _table_comment(insp, table, target_schema),
        "columns": columns,
        "primary_key": pk.get("constrained_columns", []),
        "foreign_keys": foreign_keys,
        "indexes": indexes,
        "unique_constraints": uniques,
        "estimated_rows": estimates.get(table),
    }


def quote_identifier(engine: Engine, identifier: str) -> str:
    """Safely quote a table/schema identifier for the engine's dialect."""
    return engine.dialect.identifier_preparer.quote(identifier)


# Schemi e database che non sono mai un perimetro da proporre a un progetto.
_PG_HIDDEN_SCHEMAS = frozenset({"information_schema", "pg_catalog", "pg_toast"})
_PG_HIDDEN_PREFIXES = ("pg_temp_", "pg_toast_temp_")
_MYSQL_HIDDEN_DATABASES = frozenset({"information_schema", "performance_schema", "mysql", "sys"})


def _pg_databases(engine: Engine) -> list[str]:
    """Database del server a cui ci si può connettere, esclusi i template."""
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT datname FROM pg_database "
                "WHERE NOT datistemplate AND datallowconn ORDER BY datname"
            )
        ).fetchall()
    return [r[0] for r in rows]


def _schema_names(engine: Engine) -> list[str]:
    return list(sa.inspect(engine).get_schema_names())


def _mysql_databases(engine: Engine) -> list[str]:
    with engine.connect() as conn:
        return [r[0] for r in conn.execute(sa.text("SHOW DATABASES")).fetchall()]


def _visible_pg_schema(schema: str) -> bool:
    return schema not in _PG_HIDDEN_SCHEMAS and not schema.startswith(_PG_HIDDEN_PREFIXES)


def _deadline_passed(deadline: Optional[float]) -> bool:
    """Vero quando il tempo concesso alla lettura è finito (orologio monotono)."""
    return deadline is not None and time.monotonic() >= deadline


def list_scopes(
    engine: Engine,
    open_database: Callable[[str], Engine],
    deadline: Optional[float] = None,
) -> list[dict[str, Optional[str]]]:
    """Perimetri che l'utenza della connessione può scegliere, ordinati.

    Su PostgreSQL ogni database del server con i suoi schemi: il database su
    cui l'engine è già aperto riusa l'engine, gli altri passano da
    ``open_database`` e vengono chiusi subito dopo. Un database che l'utenza non
    può aprire non offre perimetri e viene saltato. Su MySQL e MariaDB i
    database visibili, che coincidono con gli schemi. SQLite non ha nulla da
    scegliere.

    ``deadline`` è un istante dell'orologio monotono: superato quell'istante non
    si apre nessun altro database e si restituisce quello che si è già visto,
    perché ogni database in più costa una connessione al server e un thread.
    """
    dialect = engine.dialect.name
    if dialect == "postgresql":
        current = engine.url.database
        found: list[dict[str, Optional[str]]] = []
        for database in _pg_databases(engine):
            if database == current:
                schemas = _schema_names(engine)
            else:
                if _deadline_passed(deadline):
                    logger.debug("scope listing: out of time before %s", database)
                    continue
                try:
                    other = open_database(database)
                except Exception as exc:  # noqa: BLE001 - un database non apribile non offre perimetri
                    logger.debug("scope listing: cannot open %s: %s", database, exc)
                    continue
                try:
                    schemas = _schema_names(other)
                except Exception as exc:  # noqa: BLE001 - privilegio CONNECT mancante o database in chiusura
                    logger.debug("scope listing: cannot read schemas of %s: %s", database, exc)
                    continue
                finally:
                    other.dispose()
            found.extend(
                {"database": database, "schema": schema}
                for schema in sorted(schemas)
                if _visible_pg_schema(schema)
            )
        return found
    if dialect == "mysql":
        return [
            {"database": database, "schema": None}
            for database in sorted(_mysql_databases(engine))
            if database not in _MYSQL_HIDDEN_DATABASES
        ]
    return []
