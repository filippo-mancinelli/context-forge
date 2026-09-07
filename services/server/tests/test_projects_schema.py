"""Il DDL deve creare la tabella projects e le colonne project_id."""
from src.db import DDL

CONTENT_TABLES = (
    "repos", "repo_chunks", "kb_documents", "kb_chunks",
    "web_sites", "web_pages", "web_chunks",
    "db_connections", "db_query_log",
    "api_contracts", "api_endpoints",
    "chat_sessions", "jobs", "index_requests", "chunk_annotations",
    "mcp_api_keys",
)


def test_projects_table_created():
    assert "CREATE TABLE IF NOT EXISTS projects" in DDL
    assert "UNIQUE (org_id, slug)" in DDL


def test_project_id_column_on_content_tables():
    for table in CONTENT_TABLES:
        stmt = f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS project_id BIGINT"
        assert stmt in DDL, f"manca project_id su {table}"
