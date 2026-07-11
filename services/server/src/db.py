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
-- Bump maintenance_work_mem for this session so building the ivfflat vector
-- index below has enough memory (default 64MB is just under what it needs).
SET maintenance_work_mem = '256MB';

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

-- Content-hash lookup used to reuse embeddings for identical chunks
-- (e.g. a second branch of the same repo, or force re-indexes).
CREATE INDEX IF NOT EXISTS repo_chunks_org_md5_idx ON repo_chunks (org_id, md5(content));

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
    redirect_uris TEXT[] NOT NULL DEFAULT '{{}}',
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

-- ===== Multi-tenancy: organizations, members, invitations =====
CREATE TABLE IF NOT EXISTS organizations (
    id                BIGSERIAL PRIMARY KEY,
    name              TEXT NOT NULL,
    slug              TEXT UNIQUE NOT NULL,
    memory_namespace  TEXT UNIQUE NOT NULL,
    created_at        TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS organization_members (
    org_id     BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id    BIGINT NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    role       TEXT NOT NULL DEFAULT 'member',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (org_id, user_id)
);

CREATE INDEX IF NOT EXISTS org_members_user_idx ON organization_members (user_id);

CREATE TABLE IF NOT EXISTS organization_invitations (
    id          BIGSERIAL PRIMARY KEY,
    org_id      BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    email       TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'member',
    token_hash  TEXT UNIQUE NOT NULL,
    invited_by  BIGINT REFERENCES admin_users(id) ON DELETE SET NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    accepted_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS org_invitations_org_idx ON organization_invitations (org_id);
CREATE INDEX IF NOT EXISTS org_invitations_token_idx ON organization_invitations (token_hash);

-- Idempotent column migrations for tenant scoping on pre-existing tables.
ALTER TABLE admin_users   ADD COLUMN IF NOT EXISTS email   TEXT;
ALTER TABLE mcp_api_keys  ADD COLUMN IF NOT EXISTS org_id  BIGINT;
ALTER TABLE jobs          ADD COLUMN IF NOT EXISTS org_id  BIGINT;
ALTER TABLE repos         ADD COLUMN IF NOT EXISTS org_id  BIGINT;
-- Git commit a repo was last indexed at; enables diff-based incremental re-indexing.
ALTER TABLE repos         ADD COLUMN IF NOT EXISTS indexed_commit TEXT;

CREATE INDEX IF NOT EXISTS mcp_api_keys_org_idx ON mcp_api_keys (org_id);
CREATE INDEX IF NOT EXISTS jobs_org_idx ON jobs (org_id);
CREATE INDEX IF NOT EXISTS repos_org_idx ON repos (org_id);

-- Per-organization runtime configuration (repos + indexing settings).
-- Global infrastructure settings (providers, embeddings, LLM) stay in
-- app_runtime_config.settings_overrides because they are tied to the shared
-- vector store dimension.
CREATE TABLE IF NOT EXISTS org_runtime_config (
    org_id       BIGINT PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
    forge_config JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);

-- Tenant scoping for indexed chunks and index requests.
ALTER TABLE repo_chunks    ADD COLUMN IF NOT EXISTS org_id BIGINT;
ALTER TABLE index_requests ADD COLUMN IF NOT EXISTS org_id BIGINT;

CREATE INDEX IF NOT EXISTS repo_chunks_org_repo_idx ON repo_chunks (org_id, repo_name);

-- ===== Knowledge base: uploaded documents + their embedded chunks =====
CREATE TABLE IF NOT EXISTS kb_documents (
    id            BIGSERIAL PRIMARY KEY,
    org_id        BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    title         TEXT NOT NULL,
    filename      TEXT NOT NULL,
    content_type  TEXT,
    extension     TEXT,
    size_bytes    BIGINT DEFAULT 0,
    sha256        TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',
    total_chunks  INTEGER DEFAULT 0,
    char_count    INTEGER DEFAULT 0,
    error_message TEXT,
    stored_path   TEXT,
    metadata      JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    uploaded_at   TIMESTAMPTZ DEFAULT NOW(),
    processed_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS kb_documents_org_idx ON kb_documents (org_id);
CREATE INDEX IF NOT EXISTS kb_documents_status_idx ON kb_documents (status);

CREATE TABLE IF NOT EXISTS kb_chunks (
    id            BIGSERIAL PRIMARY KEY,
    org_id        BIGINT NOT NULL,
    document_id   BIGINT NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
    chunk_index   INTEGER NOT NULL,
    content       TEXT NOT NULL,
    metadata      JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    embedding     vector({dims}),
    indexed_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS kb_chunks_embedding_idx
    ON kb_chunks USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

CREATE INDEX IF NOT EXISTS kb_chunks_org_idx ON kb_chunks (org_id);
CREATE INDEX IF NOT EXISTS kb_chunks_doc_idx ON kb_chunks (document_id);

-- ===== Hybrid search: lexical full-text vectors alongside dense embeddings =====
-- A generated tsvector column lets us rank chunks by exact-token relevance
-- (identifiers, error strings, config keys) and fuse that ranking with vector
-- similarity via RRF. The two-argument to_tsvector(regconfig, text) form is
-- IMMUTABLE, which is required for use in a STORED generated column.
ALTER TABLE repo_chunks ADD COLUMN IF NOT EXISTS
    content_tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;
ALTER TABLE kb_chunks ADD COLUMN IF NOT EXISTS
    content_tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;

CREATE INDEX IF NOT EXISTS repo_chunks_tsv_idx ON repo_chunks USING GIN (content_tsv);
CREATE INDEX IF NOT EXISTS kb_chunks_tsv_idx ON kb_chunks USING GIN (content_tsv);

-- ===== External data sources: managed database connections =====
CREATE TABLE IF NOT EXISTS db_connections (
    id              BIGSERIAL PRIMARY KEY,
    org_id          BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    engine          TEXT NOT NULL,
    host            TEXT,
    port            INTEGER,
    database_name   TEXT,
    username        TEXT,
    password_enc    TEXT,
    options         JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    description     TEXT,
    status          TEXT NOT NULL DEFAULT 'unknown',
    error_message   TEXT,
    last_checked_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (org_id, name)
);

CREATE INDEX IF NOT EXISTS db_connections_org_idx ON db_connections (org_id);

-- Human-curated data dictionary: descriptions for tables (column_name = '')
-- and columns, merged into schema introspection results. This is the context
-- a live database cannot provide by itself (enum meanings, encrypted fields,
-- tenancy conventions, ...).
CREATE TABLE IF NOT EXISTS db_annotations (
    id            BIGSERIAL PRIMARY KEY,
    connection_id BIGINT NOT NULL REFERENCES db_connections(id) ON DELETE CASCADE,
    schema_name   TEXT NOT NULL DEFAULT '',
    table_name    TEXT NOT NULL,
    column_name   TEXT NOT NULL DEFAULT '',
    description   TEXT NOT NULL,
    updated_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (connection_id, schema_name, table_name, column_name)
);

CREATE INDEX IF NOT EXISTS db_annotations_conn_idx ON db_annotations (connection_id);

-- Audit trail of every read-only query executed through context-forge.
CREATE TABLE IF NOT EXISTS db_query_log (
    id            BIGSERIAL PRIMARY KEY,
    connection_id BIGINT NOT NULL REFERENCES db_connections(id) ON DELETE CASCADE,
    org_id        BIGINT NOT NULL,
    source        TEXT NOT NULL DEFAULT 'mcp',
    sql_text      TEXT NOT NULL,
    success       BOOLEAN NOT NULL DEFAULT TRUE,
    error_message TEXT,
    rows_returned INTEGER DEFAULT 0,
    duration_ms   INTEGER DEFAULT 0,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS db_query_log_conn_idx ON db_query_log (connection_id, created_at DESC);

-- ===== API contracts: ingested OpenAPI specs and GraphQL schemas =====
CREATE TABLE IF NOT EXISTS api_contracts (
    id             BIGSERIAL PRIMARY KEY,
    org_id         BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name           TEXT NOT NULL,
    type           TEXT NOT NULL,          -- 'openapi' | 'graphql'
    source_url     TEXT,                   -- spec URL / GraphQL endpoint (NULL for pasted specs)
    description    TEXT,
    title          TEXT,
    version        TEXT,
    raw_spec       TEXT,
    status         TEXT NOT NULL DEFAULT 'pending',
    error_message  TEXT,
    endpoint_count INTEGER DEFAULT 0,
    fetched_at     TIMESTAMPTZ,
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    updated_at     TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (org_id, name)
);

CREATE INDEX IF NOT EXISTS api_contracts_org_idx ON api_contracts (org_id);

-- One row per operation (REST method+path, or GraphQL query/mutation field)
-- so endpoints are individually listable and searchable.
CREATE TABLE IF NOT EXISTS api_endpoints (
    id              BIGSERIAL PRIMARY KEY,
    contract_id     BIGINT NOT NULL REFERENCES api_contracts(id) ON DELETE CASCADE,
    org_id          BIGINT NOT NULL,
    method          TEXT NOT NULL,
    path            TEXT NOT NULL,
    operation_id    TEXT,
    summary         TEXT,
    description     TEXT,
    tags            TEXT[] NOT NULL DEFAULT '{{}}',
    deprecated      BOOLEAN NOT NULL DEFAULT FALSE,
    request_schema  JSONB,
    response_schema JSONB,
    UNIQUE (contract_id, method, path)
);

CREATE INDEX IF NOT EXISTS api_endpoints_contract_idx ON api_endpoints (contract_id);
CREATE INDEX IF NOT EXISTS api_endpoints_org_idx ON api_endpoints (org_id);

-- ===== Agent chat: saved conversations + public share links =====
CREATE TABLE IF NOT EXISTS chat_sessions (
    id              BIGSERIAL PRIMARY KEY,
    org_id          BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id         BIGINT REFERENCES admin_users(id) ON DELETE CASCADE,
    title           TEXT NOT NULL DEFAULT 'New chat',
    turns           JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- Public sharing: token grants read access to shared_snapshot (a copy of
    -- turns frozen at share time, so the link shows the chat "up to then").
    share_token     TEXT UNIQUE,
    shared_snapshot JSONB,
    shared_at       TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS chat_sessions_org_user_idx ON chat_sessions (org_id, user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS chat_sessions_share_idx ON chat_sessions (share_token);

-- ===== Web sites: crawl roots that group scraped pages =====
-- A site is a root URL crawled recursively (sitemap + same-scope links). Its
-- discovered pages live in web_pages with site_id set; exclude_patterns is a
-- JSON array of URL patterns skipped during crawling.
CREATE TABLE IF NOT EXISTS web_sites (
    id               BIGSERIAL PRIMARY KEY,
    org_id           BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    root_url         TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending',
    max_pages        INTEGER NOT NULL DEFAULT 200,
    exclude_patterns JSONB NOT NULL DEFAULT '[]'::jsonb,
    pages_found      INTEGER DEFAULT 0,
    error_message    TEXT,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    crawled_at       TIMESTAMPTZ,
    UNIQUE (org_id, root_url)
);

CREATE INDEX IF NOT EXISTS web_sites_org_idx ON web_sites (org_id);
CREATE INDEX IF NOT EXISTS web_sites_status_idx ON web_sites (status);

-- ===== Web pages: scraped URLs + their embedded chunks =====
-- Mirrors the knowledge-base pipeline (fetch → extract readable text → chunk →
-- embed → search) but keyed by URL instead of an uploaded file.
CREATE TABLE IF NOT EXISTS web_pages (
    id            BIGSERIAL PRIMARY KEY,
    org_id        BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    url           TEXT NOT NULL,
    title         TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',
    total_chunks  INTEGER DEFAULT 0,
    char_count    INTEGER DEFAULT 0,
    error_message TEXT,
    content_hash  TEXT,
    metadata      JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    fetched_at    TIMESTAMPTZ,
    UNIQUE (org_id, url)
);

CREATE INDEX IF NOT EXISTS web_pages_org_idx ON web_pages (org_id);
CREATE INDEX IF NOT EXISTS web_pages_status_idx ON web_pages (status);

-- Pages discovered by a site crawl point back to their site (NULL = standalone).
ALTER TABLE web_pages ADD COLUMN IF NOT EXISTS
    site_id BIGINT REFERENCES web_sites(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS web_pages_site_idx ON web_pages (site_id);

CREATE TABLE IF NOT EXISTS web_chunks (
    id            BIGSERIAL PRIMARY KEY,
    org_id        BIGINT NOT NULL,
    page_id       BIGINT NOT NULL REFERENCES web_pages(id) ON DELETE CASCADE,
    chunk_index   INTEGER NOT NULL,
    content       TEXT NOT NULL,
    metadata      JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    embedding     vector({dims}),
    indexed_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (page_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS web_chunks_embedding_idx
    ON web_chunks USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

CREATE INDEX IF NOT EXISTS web_chunks_org_idx ON web_chunks (org_id);
CREATE INDEX IF NOT EXISTS web_chunks_page_idx ON web_chunks (page_id);

ALTER TABLE web_chunks ADD COLUMN IF NOT EXISTS
    content_tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;
CREATE INDEX IF NOT EXISTS web_chunks_tsv_idx ON web_chunks USING GIN (content_tsv);

-- ===== Chunk annotations: persistent notes on code chunks =====
-- Organization members can annotate specific files or line ranges with notes
-- (design decisions, tech debt, review comments). Persists across re-indexing.
CREATE TABLE IF NOT EXISTS chunk_annotations (
    id            BIGSERIAL PRIMARY KEY,
    org_id        BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    repo_name     TEXT NOT NULL,
    file_path     TEXT NOT NULL,
    start_line    INTEGER,
    end_line      INTEGER,
    note          TEXT NOT NULL,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS chunk_annotations_org_repo_idx
    ON chunk_annotations (org_id, repo_name);
CREATE INDEX IF NOT EXISTS chunk_annotations_file_idx
    ON chunk_annotations (org_id, repo_name, file_path);
"""


# Default constraint name Postgres assigns to UNIQUE (repo_name, file_path, chunk_index).
_LEGACY_CHUNK_UNIQUE = "repo_chunks_repo_name_file_path_chunk_index_key"


async def apply_tenant_repo_migration(default_org_id: int) -> None:
    """Migrate repo storage to composite (org_id, name) identity.

    Backfills org_id on repos/chunks/index_requests, then swaps the single-column
    primary/unique keys for tenant-aware composite keys so repository names can be
    reused across organizations. Idempotent.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Backfill org_id from the owning repo where possible, else default org.
            await conn.execute(
                """
                UPDATE repo_chunks c SET org_id = r.org_id
                FROM repos r WHERE c.repo_name = r.name AND c.org_id IS NULL
                """
            )
            await conn.execute(
                "UPDATE repo_chunks SET org_id = $1 WHERE org_id IS NULL", default_org_id
            )
            await conn.execute(
                "UPDATE index_requests SET org_id = $1 WHERE org_id IS NULL", default_org_id
            )
            await conn.execute(
                "UPDATE repos SET org_id = $1 WHERE org_id IS NULL", default_org_id
            )

            # Enforce NOT NULL now that data is backfilled.
            await conn.execute("ALTER TABLE repos ALTER COLUMN org_id SET NOT NULL")
            await conn.execute("ALTER TABLE repo_chunks ALTER COLUMN org_id SET NOT NULL")

            # Swap the repos primary key (name) -> (org_id, name).
            await conn.execute(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = 'repos_org_name_pkey'
                    ) THEN
                        ALTER TABLE repos DROP CONSTRAINT IF EXISTS repos_pkey;
                        ALTER TABLE repos ADD CONSTRAINT repos_org_name_pkey PRIMARY KEY (org_id, name);
                    END IF;
                END $$;
                """
            )

            # Swap the chunk uniqueness to include org_id.
            await conn.execute(
                f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = 'repo_chunks_org_unique'
                    ) THEN
                        ALTER TABLE repo_chunks DROP CONSTRAINT IF EXISTS {_LEGACY_CHUNK_UNIQUE};
                        ALTER TABLE repo_chunks
                            ADD CONSTRAINT repo_chunks_org_unique
                            UNIQUE (org_id, repo_name, file_path, chunk_index);
                    END IF;
                END $$;
                """
            )


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
