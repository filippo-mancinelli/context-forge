"""Database scopes: a project links a catalog connection on a database and schema.

Additive only. datasources.scope_migration.apply_db_scope_migration() turns
the legacy project_db_connections links into inferred scopes and drops that
table at boot.
"""
from __future__ import annotations

VERSION = 7
NAME = "db_scopes"
TRANSACTIONAL = True

SQL = """
-- A project's link to a connection, on one database/schema, under an alias.
-- The same connection can be linked several times on different scopes.
CREATE TABLE IF NOT EXISTS project_db_scopes (
    id               BIGSERIAL PRIMARY KEY,
    project_id       BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    db_connection_id BIGINT NOT NULL REFERENCES db_connections(id) ON DELETE CASCADE,
    database_name  TEXT,
    schema_name      TEXT,
    alias            TEXT NOT NULL,
    -- Scope derived from the connection, not confirmed by a person yet.
    scope_inferred BOOLEAN NOT NULL DEFAULT false,
    added_by         BIGINT REFERENCES admin_users(id) ON DELETE SET NULL,
    added_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Uniqueness normalises NULLs, so two empty scopes cannot coexist.
CREATE UNIQUE INDEX IF NOT EXISTS project_db_scopes_unique_idx
    ON project_db_scopes (project_id, db_connection_id,
                          coalesce(database_name, ''), coalesce(schema_name, ''));
CREATE UNIQUE INDEX IF NOT EXISTS project_db_scopes_alias_idx
    ON project_db_scopes (project_id, lower(alias));
CREATE INDEX IF NOT EXISTS project_db_scopes_connection_idx
    ON project_db_scopes (db_connection_id);

-- Scopes seen at the last successful check, offered when the server is down.
ALTER TABLE db_connections ADD COLUMN IF NOT EXISTS available_scopes JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE db_connections ADD COLUMN IF NOT EXISTS scopes_checked_at TIMESTAMPTZ;

-- Schema the query ran on, as text: the audit survives the link's removal.
ALTER TABLE db_query_log ADD COLUMN IF NOT EXISTS schema_name TEXT;
"""


async def upgrade(conn) -> None:
    await conn.execute(SQL)
