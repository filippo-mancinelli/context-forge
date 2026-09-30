"""Repository catalog: org-owned repositories keyed by id, selected by projects.

Additive only. catalog.repo_migration.apply_repo_catalog_migration() adds the
repos id, the keys and project_repos, converts the name-keyed rows and drops
the legacy columns at boot.
"""
from __future__ import annotations

VERSION = 6
NAME = "repo_catalog"
TRANSACTIONAL = True

SQL = """
-- Per-repository token (encrypted like datasource secrets), description, admin-only flag.
ALTER TABLE repos ADD COLUMN IF NOT EXISTS token_enc   TEXT;
ALTER TABLE repos ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE repos ADD COLUMN IF NOT EXISTS restricted  BOOLEAN NOT NULL DEFAULT false;

-- Derived data keyed by repository id; NOT NULL and foreign keys come with the conversion.
ALTER TABLE repo_chunks       ADD COLUMN IF NOT EXISTS repo_id BIGINT;
ALTER TABLE repo_symbols      ADD COLUMN IF NOT EXISTS repo_id BIGINT;
ALTER TABLE chunk_annotations ADD COLUMN IF NOT EXISTS repo_id BIGINT;
-- NULL repo_id with a project_id: every repository the project selects.
ALTER TABLE index_requests    ADD COLUMN IF NOT EXISTS repo_id BIGINT;

CREATE INDEX IF NOT EXISTS chunk_annotations_repo_file_idx ON chunk_annotations (repo_id, file_path);
"""


async def upgrade(conn) -> None:
    await conn.execute(SQL)
