"""Organization catalog: machines, org-owned SSH folders and databases, selections.

Additive only. catalog.migration.apply_catalog_migration() converts the legacy
project-owned rows and drops the legacy columns at boot.
"""
from __future__ import annotations

VERSION = 5
NAME = "org_catalog"
TRANSACTIONAL = True

SQL = """
-- SSH access registered once per organization, reused by folders and DB tunnels.
CREATE TABLE IF NOT EXISTS machines (
    id              BIGSERIAL PRIMARY KEY,
    org_id          BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    host            TEXT NOT NULL,
    port            INT NOT NULL DEFAULT 22,
    username        TEXT NOT NULL,
    auth_method     TEXT NOT NULL DEFAULT 'password',   -- 'key' | 'password'
    password_enc    TEXT,
    private_key_enc TEXT,
    description     TEXT,
    status          TEXT NOT NULL DEFAULT 'unknown',
    error_message   TEXT,
    last_checked_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (org_id, host, port, username)
);

CREATE INDEX IF NOT EXISTS machines_org_idx ON machines (org_id);

-- SSH folders: credentials move to the machine.
ALTER TABLE ssh_sources ADD COLUMN IF NOT EXISTS machine_id BIGINT REFERENCES machines(id) ON DELETE RESTRICT;
ALTER TABLE ssh_sources ADD COLUMN IF NOT EXISTS restricted BOOLEAN NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS ssh_sources_machine_idx ON ssh_sources (machine_id);

-- Database tunnel through a catalog machine; NULL = direct connection.
ALTER TABLE db_connections ADD COLUMN IF NOT EXISTS ssh_machine_id BIGINT REFERENCES machines(id) ON DELETE RESTRICT;
ALTER TABLE db_connections ADD COLUMN IF NOT EXISTS restricted BOOLEAN NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS db_connections_machine_idx ON db_connections (ssh_machine_id);

-- Selections: which catalog resources each project can reach.
CREATE TABLE IF NOT EXISTS project_ssh_sources (
    project_id    BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    ssh_source_id BIGINT NOT NULL REFERENCES ssh_sources(id) ON DELETE CASCADE,
    added_by      BIGINT REFERENCES admin_users(id) ON DELETE SET NULL,
    added_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (project_id, ssh_source_id)
);

CREATE INDEX IF NOT EXISTS project_ssh_sources_source_idx ON project_ssh_sources (ssh_source_id);

CREATE TABLE IF NOT EXISTS project_db_connections (
    project_id       BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    db_connection_id BIGINT NOT NULL REFERENCES db_connections(id) ON DELETE CASCADE,
    added_by         BIGINT REFERENCES admin_users(id) ON DELETE SET NULL,
    added_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (project_id, db_connection_id)
);

CREATE INDEX IF NOT EXISTS project_db_connections_connection_idx ON project_db_connections (db_connection_id);
"""


async def upgrade(conn) -> None:
    await conn.execute(SQL)
