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
    embedding     vector,
    indexed_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (repo_name, file_path, chunk_index)
);

CREATE INDEX IF NOT EXISTS repo_chunks_repo_idx ON repo_chunks (repo_name);

-- Le colonne embedding perdono la dimensione fissa: le organizzazioni possono
-- usare modelli con dimensioni diverse. atttypmod = -1 quando la colonna è già
-- senza typmod: il guard evita il rewrite della tabella a ogni bootstrap.
DO $$
BEGIN
    IF (SELECT atttypmod FROM pg_attribute
        WHERE attrelid = 'repo_chunks'::regclass AND attname = 'embedding') <> -1 THEN
        DROP INDEX IF EXISTS repo_chunks_embedding_idx;
        ALTER TABLE repo_chunks ALTER COLUMN embedding TYPE vector;
    END IF;
END $$;

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

-- OAuth client registry: riusata per i client registrati dinamicamente (DCR).
CREATE TABLE IF NOT EXISTS oauth_clients (
    id           TEXT PRIMARY KEY,
    client_id    TEXT UNIQUE NOT NULL,
    client_secret TEXT,
    name         TEXT NOT NULL,
    redirect_uris TEXT[] NOT NULL DEFAULT '{{}}',
    scopes       TEXT NOT NULL DEFAULT 'read,write',
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

-- Marca i client creati via Dynamic Client Registration (RFC 7591).
ALTER TABLE oauth_clients ADD COLUMN IF NOT EXISTS dynamic BOOLEAN DEFAULT FALSE;

-- Le vecchie tabelle dell'OAuth locale sono sostituite dal bridge Keycloak.
DROP TABLE IF EXISTS oauth_authorization_codes;
DROP TABLE IF EXISTS oauth_tokens;

-- Stato di correlazione a vita breve tra client MCP e Keycloak, per il bridge.
-- La riga è cancellata all'uso del bridge_code; TTL applicativo 10 minuti.
CREATE TABLE IF NOT EXISTS oauth_bridge_flows (
    bridge_state          TEXT PRIMARY KEY,
    client_id             TEXT NOT NULL,
    client_redirect_uri   TEXT NOT NULL,
    client_state          TEXT,
    client_code_challenge TEXT NOT NULL,
    kc_pkce_verifier      TEXT NOT NULL,
    bridge_code           TEXT UNIQUE,
    kc_access_token       TEXT,
    kc_refresh_token      TEXT,
    created_at            TIMESTAMPTZ DEFAULT NOW(),
    expires_at            TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS oauth_bridge_flows_code_idx ON oauth_bridge_flows (bridge_code);
CREATE INDEX IF NOT EXISTS oauth_bridge_flows_expires_idx ON oauth_bridge_flows (expires_at);

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

-- I valori ruolo sono vincolati anche a livello schema (la regex API resta la
-- prima linea di validazione). DO/EXCEPTION rende l'ADD CONSTRAINT idempotente.
DO $$
BEGIN
    ALTER TABLE organization_members
        ADD CONSTRAINT organization_members_role_check
        CHECK (role IN ('viewer', 'member', 'admin', 'owner'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

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

-- Matrice permessi MCP per ruolo, personalizzabile per organizzazione.
-- Nessuna riga per una coppia (org, ruolo) = valgono i default hardcoded
-- (DEFAULT_ROLE_PERMISSIONS in src/mcp/permissions.py).
CREATE TABLE IF NOT EXISTS org_role_permissions (
    org_id     BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    role       TEXT   NOT NULL,
    permission TEXT   NOT NULL,
    PRIMARY KEY (org_id, role, permission)
);

-- Idempotent column migrations for tenant scoping on pre-existing tables.
ALTER TABLE admin_users   ADD COLUMN IF NOT EXISTS email   TEXT;
ALTER TABLE admin_users   ADD COLUMN IF NOT EXISTS oidc_sub TEXT UNIQUE;
ALTER TABLE mcp_api_keys  ADD COLUMN IF NOT EXISTS org_id  BIGINT;
ALTER TABLE jobs          ADD COLUMN IF NOT EXISTS org_id  BIGINT;
ALTER TABLE repos         ADD COLUMN IF NOT EXISTS org_id  BIGINT;
-- Git commit a repo was last indexed at; enables diff-based incremental re-indexing.
ALTER TABLE repos         ADD COLUMN IF NOT EXISTS indexed_commit TEXT;

ALTER TABLE mcp_api_keys  ADD COLUMN IF NOT EXISTS permissions TEXT;
ALTER TABLE auth_sessions ADD COLUMN IF NOT EXISTS oidc_id_token TEXT;

-- Backfill one-shot delle key legacy: traduce lo scope nei permessi MCP.
-- Idempotente: tocca solo le righe non ancora migrate.
-- NB: questa CASE deve restare sincronizzata con permissions_from_scope in
-- src/mcp/permissions.py (stessa mappa legacy scope -> permessi). Uno scope
-- vuoto/sconosciuto non deve concedere alcun permesso di default (ELSE '').
UPDATE mcp_api_keys SET permissions = CASE
    WHEN scope LIKE '%admin%' THEN '*'
    WHEN scope LIKE '%write%' THEN 'context-read,context-write'
    WHEN scope LIKE '%read%' THEN 'context-read'
    ELSE ''
END WHERE permissions IS NULL;

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

-- Override di modello/provider/chiavi per organizzazione. La riga globale
-- app_runtime_config.settings_overrides viene copiata una volta in ogni org
-- esistente e poi svuotata: da quel momento fa fede solo il per-org.
ALTER TABLE org_runtime_config ADD COLUMN IF NOT EXISTS settings_overrides JSONB NOT NULL DEFAULT '{{}}'::jsonb;

INSERT INTO org_runtime_config (org_id, settings_overrides)
SELECT o.id, a.settings_overrides FROM organizations o
CROSS JOIN app_runtime_config a
WHERE a.settings_overrides <> '{{}}'::jsonb
ON CONFLICT (org_id) DO UPDATE
SET settings_overrides = EXCLUDED.settings_overrides
WHERE org_runtime_config.settings_overrides = '{{}}'::jsonb;

-- Wipe global overrides only when at least one org exists to receive the copy:
-- on legacy installs without orgs (default born during bootstrap), the global
-- row survives and migration completes on the next boot.
UPDATE app_runtime_config SET settings_overrides = '{{}}'::jsonb
WHERE settings_overrides <> '{{}}'::jsonb
  AND EXISTS (SELECT 1 FROM organizations);

-- Tenant scoping for indexed chunks and index requests.
ALTER TABLE repo_chunks    ADD COLUMN IF NOT EXISTS org_id BIGINT;
ALTER TABLE index_requests ADD COLUMN IF NOT EXISTS org_id BIGINT;

CREATE INDEX IF NOT EXISTS repo_chunks_org_repo_idx ON repo_chunks (org_id, repo_name);

-- Content-hash lookup used to reuse embeddings for identical chunks
-- (e.g. a second branch of the same repo, or force re-indexes).
-- Must come after the ALTER above: on a fresh database repo_chunks is
-- created without org_id, so this index cannot be created earlier.
CREATE INDEX IF NOT EXISTS repo_chunks_org_md5_idx ON repo_chunks (org_id, md5(content));

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
    embedding     vector,
    indexed_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS kb_chunks_org_idx ON kb_chunks (org_id);
CREATE INDEX IF NOT EXISTS kb_chunks_doc_idx ON kb_chunks (document_id);

DO $$
BEGIN
    IF (SELECT atttypmod FROM pg_attribute
        WHERE attrelid = 'kb_chunks'::regclass AND attname = 'embedding') <> -1 THEN
        DROP INDEX IF EXISTS kb_chunks_embedding_idx;
        ALTER TABLE kb_chunks ALTER COLUMN embedding TYPE vector;
    END IF;
END $$;

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
    embedding     vector,
    indexed_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (page_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS web_chunks_org_idx ON web_chunks (org_id);
CREATE INDEX IF NOT EXISTS web_chunks_page_idx ON web_chunks (page_id);

DO $$
BEGIN
    IF (SELECT atttypmod FROM pg_attribute
        WHERE attrelid = 'web_chunks'::regclass AND attname = 'embedding') <> -1 THEN
        DROP INDEX IF EXISTS web_chunks_embedding_idx;
        ALTER TABLE web_chunks ALTER COLUMN embedding TYPE vector;
    END IF;
END $$;

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

-- ===== Environments: deployment targets (staging/production/...) =====
-- A curated reference of where an org's projects actually run: the URL,
-- which managed DB connection (if any) it points to, and which repo/branch
-- deploys there. Free-text notes cover anything a live system can't tell you.
CREATE TABLE IF NOT EXISTS environments (
    id               BIGSERIAL PRIMARY KEY,
    org_id           BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    kind             TEXT NOT NULL DEFAULT 'staging',
    url              TEXT,
    domains          TEXT[] NOT NULL DEFAULT '{{}}',
    db_connection_id BIGINT REFERENCES db_connections(id) ON DELETE SET NULL,
    database_notes   TEXT,
    repo             TEXT,
    branch           TEXT,
    config_notes     TEXT,
    notes            TEXT,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (org_id, name)
);

CREATE INDEX IF NOT EXISTS environments_org_idx ON environments (org_id);
CREATE INDEX IF NOT EXISTS environments_db_connection_idx ON environments (db_connection_id);

-- ===== Progetti: contenitori di risorse sotto l'organizzazione =====
-- Ogni progetto espone un endpoint MCP dedicato /mcp/{{org_slug}}/{{project_slug}}.
-- Il memory_namespace del progetto partiziona Mem0 (il progetto "default"
-- eredita il namespace dell'org, così la memoria esistente resta leggibile).
CREATE TABLE IF NOT EXISTS projects (
    id                BIGSERIAL PRIMARY KEY,
    org_id            BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name              TEXT NOT NULL,
    slug              TEXT NOT NULL,
    memory_namespace  TEXT UNIQUE NOT NULL,
    created_at        TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (org_id, slug)
);

CREATE INDEX IF NOT EXISTS projects_org_idx ON projects (org_id);

-- Scoping per progetto delle tabelle contenuto. Nullable qui: il backfill al
-- progetto default e il SET NOT NULL avvengono in apply_project_migration.
ALTER TABLE repos ADD COLUMN IF NOT EXISTS project_id BIGINT;
ALTER TABLE repo_chunks ADD COLUMN IF NOT EXISTS project_id BIGINT;
ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS project_id BIGINT;
ALTER TABLE kb_chunks ADD COLUMN IF NOT EXISTS project_id BIGINT;
ALTER TABLE web_sites ADD COLUMN IF NOT EXISTS project_id BIGINT;
ALTER TABLE web_pages ADD COLUMN IF NOT EXISTS project_id BIGINT;
ALTER TABLE web_chunks ADD COLUMN IF NOT EXISTS project_id BIGINT;
ALTER TABLE db_connections ADD COLUMN IF NOT EXISTS project_id BIGINT;
ALTER TABLE db_query_log ADD COLUMN IF NOT EXISTS project_id BIGINT;
ALTER TABLE api_contracts ADD COLUMN IF NOT EXISTS project_id BIGINT;
ALTER TABLE api_endpoints ADD COLUMN IF NOT EXISTS project_id BIGINT;
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS project_id BIGINT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS project_id BIGINT;
ALTER TABLE index_requests ADD COLUMN IF NOT EXISTS project_id BIGINT;
ALTER TABLE chunk_annotations ADD COLUMN IF NOT EXISTS project_id BIGINT;
ALTER TABLE mcp_api_keys ADD COLUMN IF NOT EXISTS project_id BIGINT;
-- project_id NULL su una key significa "org-wide" (valida per tutti i progetti
-- dell'org): la colonna deve restare nullable. Un vecchio percorso di migrazione
-- la forzava NOT NULL insieme alle tabelle contenuto; qui la si riporta nullable
-- così i database che l'hanno gia ereditata si auto-correggono al boot.
-- No-op se e gia nullable.
ALTER TABLE mcp_api_keys ALTER COLUMN project_id DROP NOT NULL;

CREATE INDEX IF NOT EXISTS repos_project_idx        ON repos (project_id);
CREATE INDEX IF NOT EXISTS repo_chunks_project_idx  ON repo_chunks (project_id, repo_name);
CREATE INDEX IF NOT EXISTS kb_documents_project_idx ON kb_documents (project_id);
CREATE INDEX IF NOT EXISTS web_pages_project_idx    ON web_pages (project_id);
CREATE INDEX IF NOT EXISTS mcp_api_keys_project_idx ON mcp_api_keys (project_id);

-- Abilitazione di un utente su un progetto, con ruolo per-progetto. Owner/admin
-- dell'org accedono a tutti i progetti senza riga qui; gli altri solo dove
-- esiste un membership esplicito.
CREATE TABLE IF NOT EXISTS project_members (
    project_id BIGINT NOT NULL REFERENCES projects(id)   ON DELETE CASCADE,
    user_id    BIGINT NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    role       TEXT   NOT NULL DEFAULT 'member',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (project_id, user_id)
);

CREATE INDEX IF NOT EXISTS project_members_user_idx ON project_members (user_id);

DO $$
BEGIN
    ALTER TABLE project_members
        ADD CONSTRAINT project_members_role_check
        CHECK (role IN ('viewer', 'member', 'admin'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Progetti su cui una API key MCP è autorizzata (key org-level). Vuoto +
-- mcp_api_keys.project_id NULL = key valida su tutti i progetti dell'org.
CREATE TABLE IF NOT EXISTS mcp_api_key_projects (
    key_id     BIGINT NOT NULL REFERENCES mcp_api_keys(id) ON DELETE CASCADE,
    project_id BIGINT NOT NULL REFERENCES projects(id)     ON DELETE CASCADE,
    PRIMARY KEY (key_id, project_id)
);

-- Sorgenti filesystem SSH: lettura read-only realtime di file (config) su host
-- remoti via SFTP, esposta ai tool MCP. I segreti sono cifrati come per i DB.
CREATE TABLE IF NOT EXISTS ssh_sources (
    id              BIGSERIAL PRIMARY KEY,
    org_id          BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    project_id      BIGINT REFERENCES projects(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    host            TEXT NOT NULL,
    port            INT NOT NULL DEFAULT 22,
    username        TEXT NOT NULL,
    auth_method     TEXT NOT NULL DEFAULT 'password',   -- 'key' | 'password'
    password_enc    TEXT,
    private_key_enc TEXT,
    root_path       TEXT NOT NULL,
    include_globs   TEXT,                                -- CSV di pattern, es. "*.conf,*.yml"
    exclude_globs   TEXT,
    description     TEXT,
    status          TEXT NOT NULL DEFAULT 'unknown',
    error_message   TEXT,
    last_checked_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (project_id, name)
);

CREATE INDEX IF NOT EXISTS ssh_sources_project_idx ON ssh_sources (project_id);

-- Grafo dei simboli: dove un nome è definito e in quali file viene usato. Serve
-- a rispondere "chi chiama X" e "da dove parto" senza rileggere il repository:
-- gli archi file→file nascono dal join fra un riferimento e la sua definizione.
-- Una riga per (file, nome, tipo): le occorrenze ripetute nello stesso file
-- diventano il peso dell'arco, non righe in più.
CREATE TABLE IF NOT EXISTS repo_symbols (
    org_id      BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    project_id  BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    repo_name   TEXT NOT NULL,
    file_path   TEXT NOT NULL,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,          -- 'def' | 'ref'
    node_type   TEXT,                   -- per le definizioni: class_declaration, ...
    line        INT,
    occurrences INT NOT NULL DEFAULT 1,
    PRIMARY KEY (project_id, repo_name, file_path, name, kind)
);

CREATE INDEX IF NOT EXISTS repo_symbols_name_idx ON repo_symbols (project_id, name, kind);
CREATE INDEX IF NOT EXISTS repo_symbols_file_idx ON repo_symbols (project_id, repo_name, file_path);

-- Accesso al datasource attraverso un bastion SSH (tunnel). Assenza = diretto.
ALTER TABLE db_connections ADD COLUMN IF NOT EXISTS ssh_enabled         BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE db_connections ADD COLUMN IF NOT EXISTS ssh_host            TEXT;
ALTER TABLE db_connections ADD COLUMN IF NOT EXISTS ssh_port            INT DEFAULT 22;
ALTER TABLE db_connections ADD COLUMN IF NOT EXISTS ssh_username        TEXT;
ALTER TABLE db_connections ADD COLUMN IF NOT EXISTS ssh_auth_method     TEXT;
ALTER TABLE db_connections ADD COLUMN IF NOT EXISTS ssh_password_enc    TEXT;
ALTER TABLE db_connections ADD COLUMN IF NOT EXISTS ssh_private_key_enc TEXT;

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


async def apply_project_migration() -> None:
    """Backfill dei progetti: ogni org senza progetti riceve un progetto
    "default" che eredita il memory_namespace dell'organizzazione, e tutte le
    righe contenuto senza project_id vengono attribuite a quel progetto.
    Idempotente: sicura a ogni avvio.
    """
    content_tables = (
        "repos", "repo_chunks", "kb_documents", "kb_chunks",
        "web_sites", "web_pages", "web_chunks",
        "db_connections", "db_query_log",
        "api_contracts", "api_endpoints",
        "chat_sessions", "jobs", "index_requests", "chunk_annotations",
    )
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Un progetto default per ogni org che non ne ha.
            # Il namespace del progetto default è quello dell'org: la memoria
            # Mem0 esistente resta leggibile senza re-embedding.
            await conn.execute(
                """
                INSERT INTO projects (org_id, name, slug, memory_namespace)
                SELECT o.id, 'Default', 'default', o.memory_namespace
                FROM organizations o
                WHERE NOT EXISTS (SELECT 1 FROM projects p WHERE p.org_id = o.id)
                """
            )
            # Backfill: ogni riga contenuto senza progetto va al progetto
            # default della sua org (per tabelle senza org_id NOT NULL il
            # fallback è il progetto più vecchio in assoluto).
            for table in content_tables:
                await conn.execute(
                    f"""
                    UPDATE {table} t SET project_id = p.id
                    FROM projects p
                    WHERE t.project_id IS NULL
                      AND p.org_id = t.org_id
                      AND p.slug = 'default'
                    """
                )
                # Righe orfane senza org_id (installazioni pre-tenancy):
                # attribuite al progetto più vecchio.
                await conn.execute(
                    f"""
                    UPDATE {table} t SET project_id = (
                        SELECT id FROM projects ORDER BY created_at, id LIMIT 1
                    )
                    WHERE t.project_id IS NULL
                    """
                )
            # Con tutti i percorsi di scrittura che valorizzano project_id il
            # vincolo diventa strutturale: una riga contenuto senza progetto è
            # un bug, non uno stato transitorio. Il backfill qui sopra, nella
            # stessa transazione, sana le righe pregresse prima dell'ALTER.
            for table in content_tables:
                await conn.execute(
                    f"ALTER TABLE {table} ALTER COLUMN project_id SET NOT NULL"
                )

            # Le chiavi di unicità di pagine e siti passano dal perimetro org
            # al perimetro progetto: lo stesso URL può esistere in progetti
            # diversi della stessa organizzazione con righe indipendenti.
            await conn.execute(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = 'web_pages_project_url_key'
                    ) THEN
                        ALTER TABLE web_pages DROP CONSTRAINT IF EXISTS web_pages_org_id_url_key;
                        ALTER TABLE web_pages
                            ADD CONSTRAINT web_pages_project_url_key UNIQUE (project_id, url);
                    END IF;
                END $$;
                """
            )
            await conn.execute(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = 'web_sites_project_root_url_key'
                    ) THEN
                        ALTER TABLE web_sites DROP CONSTRAINT IF EXISTS web_sites_org_id_root_url_key;
                        ALTER TABLE web_sites
                            ADD CONSTRAINT web_sites_project_root_url_key UNIQUE (project_id, root_url);
                    END IF;
                END $$;
                """
            )
            # Same swap for managed database connections: a connection name can
            # be reused across projects of the same organization.
            await conn.execute(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = 'db_connections_project_name_key'
                    ) THEN
                        ALTER TABLE db_connections DROP CONSTRAINT IF EXISTS db_connections_org_id_name_key;
                        ALTER TABLE db_connections
                            ADD CONSTRAINT db_connections_project_name_key UNIQUE (project_id, name);
                    END IF;
                END $$;
                """
            )
            # Same swap for API contracts: a contract name can be reused
            # across projects of the same organization.
            await conn.execute(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = 'api_contracts_project_name_key'
                    ) THEN
                        ALTER TABLE api_contracts DROP CONSTRAINT IF EXISTS api_contracts_org_id_name_key;
                        ALTER TABLE api_contracts
                            ADD CONSTRAINT api_contracts_project_name_key UNIQUE (project_id, name);
                    END IF;
                END $$;
                """
            )
            # Le API key MCP passano da un singolo progetto a un elenco di
            # progetti consentiti: ogni key con project_id valorizzato porta con
            # sé quel progetto nella tabella ponte, così la key resta valida
            # esattamente sullo stesso perimetro di prima.
            await conn.execute(
                """
                INSERT INTO mcp_api_key_projects (key_id, project_id)
                SELECT id, project_id FROM mcp_api_keys
                WHERE project_id IS NOT NULL
                ON CONFLICT DO NOTHING
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
