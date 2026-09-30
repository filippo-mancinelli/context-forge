"""Fixtures shared by the tests."""
import asyncio
import uuid

import asyncpg
import pytest

from src import config, db
from tests import pgutil


@pytest.fixture
def pg_database(monkeypatch):
    """Empty PostgreSQL database with the application schema, dropped after the test."""
    if not pgutil.TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    name = f"cf_test_{uuid.uuid4().hex[:12]}"

    async def _admin(statement: str) -> None:
        conn = await asyncpg.connect(pgutil.TEST_DATABASE_URL)
        try:
            await conn.execute(statement)
        finally:
            await conn.close()

    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    monkeypatch.setattr(config.get_settings(), "database_url", pgutil.database_url(name))
    try:
        pgutil.run_db(pgutil.prepare_schema)
        yield name
    finally:
        asyncio.run(_admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture
def baseline_database(monkeypatch, tmp_path):
    """Database with the versioned migrations only, before any boot conversion."""
    if not pgutil.TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    name = f"cf_test_{uuid.uuid4().hex[:12]}"

    async def _admin(statement: str) -> None:
        conn = await asyncpg.connect(pgutil.TEST_DATABASE_URL)
        try:
            await conn.execute(statement)
        finally:
            await conn.close()

    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    monkeypatch.setattr(config.get_settings(), "database_url", pgutil.database_url(name))
    monkeypatch.setattr(config.get_settings(), "encryption_key", "test-key")
    # The repository conversion moves clone directories: keep them in a temporary tree.
    monkeypatch.setattr(config.get_settings(), "repos_cache_dir", str(tmp_path / "repos-cache"))
    try:
        pgutil.run_db(db.init_db)
        yield name
    finally:
        asyncio.run(_admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
