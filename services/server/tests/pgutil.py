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
    await db.init_db()


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
