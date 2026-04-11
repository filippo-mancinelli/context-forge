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

CREATE TABLE IF NOT EXISTS app_runtime_config (
    id                 SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    forge_config       JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    settings_overrides JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    updated_at         TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS admin_users (
    id            BIGSERIAL PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    salt          TEXT NOT NULL,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash    TEXT PRIMARY KEY,
    user_id       BIGINT NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    expires_at    TIMESTAMPTZ NOT NULL,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS auth_sessions_user_idx ON auth_sessions (user_id);
CREATE INDEX IF NOT EXISTS auth_sessions_expires_idx ON auth_sessions (expires_at);

CREATE TABLE IF NOT EXISTS mcp_api_keys (
    id           BIGSERIAL PRIMARY KEY,
    name         TEXT NOT NULL,
    key_hash     TEXT UNIQUE NOT NULL,
    scope        TEXT NOT NULL DEFAULT 'read,write',
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    last_used_at TIMESTAMPTZ,
    expires_at   TIMESTAMPTZ,
    created_by   BIGINT REFERENCES admin_users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS mcp_api_keys_key_hash_idx ON mcp_api_keys (key_hash);
CREATE INDEX IF NOT EXISTS mcp_api_keys_expires_idx ON mcp_api_keys (expires_at);

-- OAuth 2.0 tables for MCP server authentication
CREATE TABLE IF NOT EXISTS oauth_clients (
    id           TEXT PRIMARY KEY,
    client_id    TEXT UNIQUE NOT NULL,
    client_secret TEXT,
    name         TEXT NOT NULL,
    redirect_uris TEXT[] NOT NULL DEFAULT '{}',
    scopes       TEXT NOT NULL DEFAULT 'read,write',
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS oauth_authorization_codes (
    code         TEXT PRIMARY KEY,
    client_id    TEXT NOT NULL REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
    user_id      BIGINT REFERENCES admin_users(id) ON DELETE CASCADE,
    expires_at   TIMESTAMPTZ NOT NULL,
    redirect_uri TEXT NOT NULL,
    scope        TEXT NOT NULL,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS oauth_authorization_codes_client_idx ON oauth_authorization_codes(client_id);
CREATE INDEX IF NOT EXISTS oauth_authorization_codes_expires_idx ON oauth_authorization_codes(expires_at);

CREATE TABLE IF NOT EXISTS oauth_tokens (
    access_token  TEXT PRIMARY KEY,
    refresh_token TEXT UNIQUE,
    client_id     TEXT NOT NULL REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
    user_id       BIGINT REFERENCES admin_users(id) ON DELETE CASCADE,
    scope         TEXT NOT NULL,
    expires_at    TIMESTAMPTZ NOT NULL,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS oauth_tokens_access_idx ON oauth_tokens(access_token);
CREATE INDEX IF NOT EXISTS oauth_tokens_refresh_idx ON oauth_tokens(refresh_token);
CREATE INDEX IF NOT EXISTS oauth_tokens_expires_idx ON oauth_tokens(expires_at);
CREATE INDEX IF NOT EXISTS oauth_tokens_client_idx ON oauth_tokens(client_id);
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
