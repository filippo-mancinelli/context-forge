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
from typing import Any, Optional

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
