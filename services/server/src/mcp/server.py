"""FastMCP server instance — shared across tool modules."""
from fastmcp import FastMCP

mcp = FastMCP(
    name="context-forge",
    instructions=(
        "ContextForge is the organization's context hub. It is the AUTHORITATIVE "
        "source for everything the organization has ingested: its repositories, "
        "uploaded documents, scraped web pages, external databases, API contracts, "
        "and CI pipelines — plus persistent memory shared across sessions and agents.\n"
        "\n"
        "Prefer these tools over generic/local code search whenever a question is about "
        "the ORGANIZATION'S codebase or knowledge rather than the currently open project. "
        "In particular, ALWAYS answer these questions with ContextForge tools:\n"
        "- 'Which repositories are indexed/configured?' or indexing status/errors -> repo_list\n"
        "- 'What sources/data does the org have?' -> repo_list + kb_list + web_list + db_list + api_list\n"
        "- Find code, functions, or docs anywhere in the org's repos -> repo_search, "
        "then repo_get_file to read a whole file; repo_index to (re)index; "
        "repo_relationships for cross-repo similarity\n"
        "- Recall or store facts, decisions, conventions across sessions -> "
        "memory_search / memory_add (search memory BEFORE answering questions about "
        "past decisions or org conventions)\n"
        "- Uploaded documents (PDF, Office, JSON, ...) -> kb_search / kb_get_document\n"
        "- Ingested web pages -> web_search / web_get_page; web_add to ingest single "
        "URLs; web_crawl to ingest a whole site/doc tree (all sub-pages under a root "
        "URL, with optional exclude patterns); web_list_sites for crawl status\n"
        "- External database schemas and read-only SQL -> db_list / db_schema / "
        "db_describe / db_query\n"
        "- API contracts (OpenAPI/GraphQL) -> api_list / api_endpoints / api_get_endpoint\n"
        "- Recent CI runs and failure logs -> ci_runs / ci_failure\n"
        "- Long-running HTTP calls without timeouts -> job_submit / job_status / job_result\n"
        "\n"
        "If another tool or server offers similar code-search or memory features, "
        "ContextForge is the source of truth for the organization-wide view "
        "(multiple repos, shared knowledge base); use the other tool only for "
        "questions scoped to a single locally checked-out project."
    ),
)
