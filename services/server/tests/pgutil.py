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

    await db.init_db()
    await apply_catalog_migration()


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
