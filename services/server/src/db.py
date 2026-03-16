"""Database initialization and connection management."""
from __future__ import annotations

import logging
from typing import AsyncIterator

import asyncpg
from asyncpg import Pool

from .config import get_settings

logger = logging.getLogger(__name__)

_pool: Pool | None = None

DDL = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS repos (
    name          TEXT PRIMARY KEY,
    type          TEXT NOT NULL DEFAULT 'local',
    url           TEXT,
    path          TEXT,
    branch        TEXT DEFAULT 'main',
    language      TEXT DEFAULT 'auto',
    status        TEXT DEFAULT 'pending',
    last_indexed_at TIMESTAMPTZ,
    total_chunks  INTEGER DEFAULT 0,
    error_message TEXT,
    config        JSONB DEFAULT '{{}}'
);

CREATE TABLE IF NOT EXISTS repo_chunks (
    id            BIGSERIAL PRIMARY KEY,
    repo_name     TEXT NOT NULL,
    file_path     TEXT NOT NULL,
    chunk_index   INTEGER NOT NULL,
    chunk_type    TEXT DEFAULT 'code',
    content       TEXT NOT NULL,
    metadata      JSONB DEFAULT '{{}}',
    embedding     vector({dims}),
    indexed_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (repo_name, file_path, chunk_index)
);

CREATE INDEX IF NOT EXISTS repo_chunks_embedding_idx
    ON repo_chunks USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

CREATE INDEX IF NOT EXISTS repo_chunks_repo_idx ON repo_chunks (repo_name);

CREATE TABLE IF NOT EXISTS jobs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tool          TEXT NOT NULL,
    params        JSONB DEFAULT '{{}}',
    status        TEXT DEFAULT 'pending',
    result        JSONB,
    error_message TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS index_requests (
    id            BIGSERIAL PRIMARY KEY,
    repo_name     TEXT,
    requested_at  TIMESTAMPTZ DEFAULT NOW(),
    processed_at  TIMESTAMPTZ
);
"""


async def get_pool() -> Pool:
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = await asyncpg.create_pool(
            settings.database_url,
            min_size=2,
            max_size=10,
            command_timeout=60,
        )
    return _pool


async def init_db() -> None:
    settings = get_settings()
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(DDL.format(dims=settings.embeddings_dims))
    logger.info("Database initialized")


async def close_db() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
