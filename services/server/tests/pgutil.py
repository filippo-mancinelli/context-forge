"""Support for tests that run against a real PostgreSQL.

Tests marked with ``requires_pg`` use a database created on the fly on the
server pointed to by ``TEST_DATABASE_URL`` (with the pgvector extension) and
dropped at the end of the test. Without the variable they are skipped.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Awaitable, Callable, Optional, TypeVar

import pytest

from src import db

T = TypeVar("T")

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

requires_pg = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set")


def database_url(name: str) -> str:
    base, _, _ = TEST_DATABASE_URL.rpartition("/")
    return f"{base}/{name}"


def run_db(fn: Callable[[], Awaitable[T]]) -> T:
    """Run ``fn`` in a fresh event loop with its own pool.

    asyncpg binds a pool to the loop that created it: each run opens its own
    pool and closes it before returning.
    """

    async def _main() -> T:
        db._pool = None
        try:
            return await fn()
        finally:
            await db.close_db()

    return asyncio.run(_main())


async def prepare_schema() -> None:
    """Full application schema on an empty database."""
    from src.catalog.migration import apply_catalog_migration
    from src.catalog.repo_migration import apply_repo_catalog_migration

    await db.init_db()
    await apply_catalog_migration()
    await apply_repo_catalog_migration()


async def make_legacy_schema() -> None:
    """Bring SSH folders and databases back to the project-owned shape.

    Used to exercise the conversion on a database created with the current schema.
    """
    await execute("DROP INDEX IF EXISTS ssh_sources_org_lower_name_idx")
    await execute("DROP INDEX IF EXISTS db_connections_org_lower_name_idx")
    await execute(
        """
        ALTER TABLE ssh_sources
            ALTER COLUMN machine_id DROP NOT NULL,
            ADD COLUMN project_id BIGINT REFERENCES projects(id) ON DELETE CASCADE,
            ADD COLUMN host TEXT,
            ADD COLUMN port INT DEFAULT 22,
            ADD COLUMN username TEXT,
            ADD COLUMN auth_method TEXT DEFAULT 'password',
            ADD COLUMN password_enc TEXT,
            ADD COLUMN private_key_enc TEXT,
            ADD CONSTRAINT ssh_sources_project_id_name_key UNIQUE (project_id, name)
        """
    )
    await execute(
        """
        ALTER TABLE db_connections
            ADD COLUMN project_id BIGINT,
            ADD COLUMN ssh_enabled BOOLEAN NOT NULL DEFAULT false,
            ADD COLUMN ssh_host TEXT,
            ADD COLUMN ssh_port INT DEFAULT 22,
            ADD COLUMN ssh_username TEXT,
            ADD COLUMN ssh_auth_method TEXT,
            ADD COLUMN ssh_password_enc TEXT,
            ADD COLUMN ssh_private_key_enc TEXT,
            ADD CONSTRAINT db_connections_project_name_key UNIQUE (project_id, name)
        """
    )


async def make_legacy_repo_schema() -> None:
    """Bring repositories and derived data back to the name- and project-keyed shape.

    The ``repo_id`` columns stay nullable, as after migration 0006 on a database
    not converted yet.
    """
    await execute("DROP TABLE project_repos")
    await execute("DROP INDEX repos_org_lower_name_idx")
    await execute("DROP INDEX repos_org_url_branch_idx")
    await execute(
        """
        ALTER TABLE repo_chunks
            DROP CONSTRAINT repo_chunks_repo_fkey,
            DROP CONSTRAINT repo_chunks_repo_unique,
            ALTER COLUMN repo_id DROP NOT NULL,
            ADD COLUMN repo_name TEXT NOT NULL,
            ADD COLUMN project_id BIGINT NOT NULL,
            ADD CONSTRAINT repo_chunks_org_unique UNIQUE (org_id, repo_name, file_path, chunk_index)
        """
    )
    await execute(
        """
        ALTER TABLE repo_symbols
            DROP CONSTRAINT repo_symbols_repo_fkey,
            DROP CONSTRAINT repo_symbols_repo_pkey,
            ALTER COLUMN repo_id DROP NOT NULL,
            ADD COLUMN project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            ADD COLUMN repo_name TEXT NOT NULL,
            ADD PRIMARY KEY (project_id, repo_name, file_path, name, kind)
        """
    )
    await execute(
        """
        ALTER TABLE chunk_annotations
            DROP CONSTRAINT chunk_annotations_repo_fkey,
            ALTER COLUMN repo_id DROP NOT NULL,
            ADD COLUMN repo_name TEXT NOT NULL,
            ADD COLUMN project_id BIGINT NOT NULL
        """
    )
    await execute(
        "ALTER TABLE index_requests DROP CONSTRAINT index_requests_repo_fkey, ADD COLUMN repo_name TEXT"
    )
    await execute(
        """
        ALTER TABLE repos
            DROP CONSTRAINT repos_pkey,
            DROP COLUMN id,
            ADD COLUMN project_id BIGINT NOT NULL,
            ADD CONSTRAINT repos_org_name_pkey PRIMARY KEY (org_id, name)
        """
    )


async def fetch(query: str, *args: Any) -> list[dict]:
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        return [dict(r) for r in await conn.fetch(query, *args)]


async def fetchval(query: str, *args: Any) -> Any:
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(query, *args)


async def execute(query: str, *args: Any) -> None:
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(query, *args)


async def seed_org(slug: str = "acme") -> int:
    return await fetchval(
        "INSERT INTO organizations (name, slug, memory_namespace) VALUES ($1, $1, $2) RETURNING id",
        slug,
        f"org_{slug}",
    )


async def seed_project(org_id: int, slug: str) -> int:
    org_slug = await fetchval("SELECT slug FROM organizations WHERE id = $1", org_id)
    return await fetchval(
        "INSERT INTO projects (org_id, name, slug, memory_namespace) VALUES ($1, $2, $2, $3) RETURNING id",
        org_id,
        slug,
        f"{org_slug}--{slug}",
    )


async def seed_user(username: str, org_id: Optional[int] = None, role: str = "member") -> int:
    user_id = await fetchval(
        "INSERT INTO admin_users (username, password_hash, salt) VALUES ($1, 'x', 'x') RETURNING id",
        username,
    )
    if org_id is not None:
        await execute(
            "INSERT INTO organization_members (org_id, user_id, role) VALUES ($1, $2, $3)",
            org_id,
            user_id,
            role,
        )
    return user_id


async def seed_project_member(project_id: int, user_id: int, role: str = "member") -> None:
    await execute(
        "INSERT INTO project_members (project_id, user_id, role) VALUES ($1, $2, $3)",
        project_id,
        user_id,
        role,
    )
