"""Agent chat endpoint for testing retrieval end-to-end.

Exposes a small tool-using agent that can search the active organization's
indexed repositories, persistent memory, and knowledge base. The purpose is to
let a human verify — in one place — that a coding agent connecting over MCP
would actually surface the right context. Every tool the model invokes is
returned in the response so the retrieval can be inspected, not just trusted.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ...config import get_settings
from ..deps import ActiveOrg, get_active_org

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

# Cap the agent loop so a misbehaving model can't spin forever. Also doubles as
# the budget for auto-continuing a response that got cut off by max_tokens (see
# CONTINUE_NUDGE below) — a few rounds is enough for either case.
MAX_TOOL_ITERATIONS = 6

# Anthropic requires an explicit max_tokens per request. The previous values
# (1024 non-streaming, 2048 streaming) were low enough that a normal answer
# with code blocks/tables/citations would get cut off mid-markdown — breaking
# rendering — and, since nothing checked stop_reason, the turn silently ended
# instead of continuing, leaving the user to type "continue" manually. All
# current Claude models support 8192 output tokens.
ANTHROPIC_MAX_TOKENS = 8192

# Appended as a synthetic user turn when a response was cut off by the token
# limit (not a real stop) so the model keeps generating instead of the turn
# ending prematurely.
CONTINUE_NUDGE = "Continue exactly where you left off. Do not repeat any earlier text."

SYSTEM_PROMPT = (
    "You are the context-forge test agent. You help a developer verify that "
    "their self-hosted context infrastructure returns useful results.\n\n"
    "You have these retrieval tools:\n"
    "- search_repositories: semantic search over indexed code repositories.\n"
    "- search_memory: semantic search over long-term persistent memories.\n"
    "- search_knowledge_base: semantic search over uploaded documents.\n"
    "- search_web: semantic search over scraped web pages the user has added.\n"
    "- get_database_schema: list configured database connections, browse a "
    "database's tables, or describe one table in depth (columns, keys, "
    "curated descriptions).\n"
    "- query_database: run a single read-only SQL query on a configured "
    "database connection.\n"
    "- add_memory: save a memory, fact, or decision so it persists across "
    "sessions. Use this when the user asks you to remember something.\n"
    "- list_memories: list all stored memories for this organization.\n"
    "- delete_memory: delete a memory by its ID.\n\n"
    "For any question that could benefit from stored context, call the "
    "relevant tool(s) before answering — prefer searching over guessing.\n\n"
    "Survey broadly, then answer: a project or topic usually spans several "
    "sources at once — e.g. a project 'acme' may have repositories "
    "'acme-backend' and 'acme-frontend', a database connection, uploaded "
    "documents, and stored memories. For broad or project-level questions, "
    "check EVERY plausibly relevant source (repositories, memory, knowledge "
    "base, web pages, databases) before answering — do not stop at the first source "
    "that returns something. You may request multiple tool calls in a "
    "single turn. Cross-check what you find: reconcile what the code says "
    "with the database schema/data and the documents, and point out "
    "disagreements between sources.\n\n"
    "For database questions, inspect the schema with get_database_schema "
    "before writing SQL, and use the exact table/column names it returns. "
    "When the user asks about a database without naming the connection, "
    "infer it from the conversation: project/repo names usually match a "
    "connection name (e.g. discussing context-forge → connection "
    "context-forge). Use the hint parameter or call get_database_schema "
    "with no arguments to list connections. Never pass placeholder values "
    "like __list__.\n\n"
    "Always invoke tools through the structured tool-calling interface — "
    "never write tool-call markup (XML, DSML, or similar) inside your "
    "reply or reasoning text.\n\n"
    "When you use results, cite where they came from (repo/file, memory, "
    "document title, web page URL, or connection/table) so the developer can confirm "
    "retrieval is working. If a search returns nothing, say so plainly. "
    "Keep answers concise."
)


def _extract_context_hints(messages: list[ChatMessage]) -> list[str]:
    """Pull project/repo-like tokens from recent chat for DB connection matching."""
    hints: list[str] = []
    for message in messages[-8:]:
        text = message.content
        for match in re.finditer(r"\b[a-zA-Z][a-zA-Z0-9]*(?:[-_][a-zA-Z0-9]+)+\b", text):
            token = match.group()
            if token.lower() not in {"gpt-", "http", "https"}:
                hints.append(token)
        for match in re.finditer(r"`([^`]+)`", text):
            segment = match.group(1).strip()
            if "/" in segment:
                segment = segment.split("/")[0]
            if segment and len(segment) > 2:
                hints.append(segment)
    deduped: list[str] = []
    seen: set[str] = set()
    for hint in hints:
        key = hint.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(hint)
    return deduped


async def _build_system_prompt(org: ActiveOrg, messages: list[ChatMessage]) -> str:
    from ...datasources import service

    parts = [SYSTEM_PROMPT]
    try:
        connections = await service.list_connections(org.org_id)
    except Exception:  # noqa: BLE001
        connections = []
    if connections:
        lines = []
        for conn in connections:
            label = conn["name"]
            if conn.get("description"):
                label += f" — {conn['description']}"
            if conn.get("database_name"):
                label += f" ({conn['database_name']})"
            lines.append(f"  - {label}")
        parts.append("\nConfigured database connections:\n" + "\n".join(lines))
    context_hints = _extract_context_hints(messages)
    if context_hints:
        parts.append(
            "\nRecent conversation topics (use as hint when picking a database): "
            + ", ".join(context_hints[:8])
        )
    return "".join(parts)


# --------------------------------------------------------------------------- #
# Retrieval helpers (org-scoped). These mirror the REST search endpoints but
# are callable directly so the agent loop can invoke them as tools. Handlers
# take (org, tool_args) and return a list of result dicts.
# --------------------------------------------------------------------------- #
def _arg_query(args: dict[str, Any]) -> str:
    return str(args.get("query", "")).strip()


def _arg_limit(args: dict[str, Any], default: int = 8, cap: int = 20) -> int:
    return max(1, min(int(args.get("limit") or default), cap))


async def _search_repositories(org: ActiveOrg, args: dict[str, Any]) -> list[dict[str, Any]]:
    from ...search import search_repo_chunks

    results = await search_repo_chunks(org.org_id, _arg_query(args), limit=_arg_limit(args))
    return [
        {
            "repo_name": r["repo_name"],
            "file_path": r["file_path"],
            "chunk_type": r["chunk_type"],
            "content": r["content"],
            "score": r["score"],
        }
        for r in results
    ]


async def _search_memory(org: ActiveOrg, args: dict[str, Any]) -> list[dict[str, Any]]:
    from ...mcp.memory import _get_memory

    mem = _get_memory()
    results = mem.search(_arg_query(args), user_id=org.namespace, limit=_arg_limit(args))
    memories = results.get("results", results) if isinstance(results, dict) else results
    out: list[dict[str, Any]] = []
    for m in memories or []:
        if isinstance(m, dict):
            out.append(
                {
                    "id": m.get("id"),
                    "memory": m.get("memory") or m.get("content"),
                    "score": m.get("score"),
                }
            )
        else:
            out.append({"memory": str(m)})
    return out


async def _search_knowledge_base(org: ActiveOrg, args: dict[str, Any]) -> list[dict[str, Any]]:
    from ...kb import store

    results = await store.search_documents(org.org_id, _arg_query(args), limit=_arg_limit(args))
    return [
        {
            "document_id": r.get("document_id"),
            "title": r.get("title"),
            "filename": r.get("filename"),
            "content": r.get("content"),
            "score": r.get("score"),
        }
        for r in results
    ]


async def _search_web(org: ActiveOrg, args: dict[str, Any]) -> list[dict[str, Any]]:
    from ...web import store

    results = await store.search_pages(org.org_id, _arg_query(args), limit=_arg_limit(args))
    return [
        {
            "page_id": r.get("page_id"),
            "title": r.get("title"),
            "url": r.get("url"),
            "content": r.get("content"),
            "score": r.get("score"),
        }
        for r in results
    ]


async def _get_database_schema(
    org: ActiveOrg, args: dict[str, Any], *, context_hints: list[str] | None = None
) -> list[dict[str, Any]]:
    """Three granularities: no args -> connections; connection -> tables; +table -> columns."""
    from ...datasources import service
    from ...datasources.service import ConnectionAmbiguousError, ConnectionNotFoundError

    connection = str(args.get("connection") or "").strip()
    hint = str(args.get("hint") or "").strip()
    table = str(args.get("table") or "").strip()
    schema = str(args.get("schema") or "").strip() or None

    if service.is_list_sentinel(connection):
        connection = ""

    if not connection and not hint and not table:
        return [
            {
                "connection": c["name"],
                "engine": c["engine"],
                "database": c.get("database_name"),
                "description": c.get("description"),
                "status": c.get("status"),
            }
            for c in await service.list_connections(org.org_id)
        ]

    try:
        if connection:
            try:
                record = await service.get_connection(org.org_id, connection)
            except ConnectionNotFoundError:
                record = await service.resolve_connection(
                    org.org_id,
                    connection,
                    context_hints=context_hints,
                )
        else:
            record = await service.resolve_connection(
                org.org_id,
                hint or None,
                context_hints=context_hints,
            )
        connection_name = record["name"]
    except ConnectionAmbiguousError as e:
        connections = await service.list_connections(org.org_id)
        return [{"error": str(e), "connections": [c["name"] for c in connections]}]
    except ConnectionNotFoundError as e:
        connections = await service.list_connections(org.org_id)
        return [{"error": str(e), "connections": [c["name"] for c in connections]}]

    if not table:
        overview = await service.schema_overview(org.org_id, connection_name, schema=schema)
        return [
            {
                "connection": connection_name,
                "table": t["name"],
                "description": t.get("description") or t.get("comment"),
                "column_count": t["column_count"],
                "estimated_rows": t.get("estimated_rows"),
            }
            for t in overview["tables"]
        ]
    detail = await service.describe_table(org.org_id, connection_name, table, schema=schema)
    return [detail]


async def _query_database(
    org: ActiveOrg, args: dict[str, Any], *, context_hints: list[str] | None = None
) -> list[dict[str, Any]]:
    from ...datasources import service
    from ...datasources.service import ConnectionAmbiguousError, ConnectionNotFoundError

    connection = str(args.get("connection") or "").strip()
    hint = str(args.get("hint") or "").strip()
    sql = str(args.get("sql") or "").strip()
    if service.is_list_sentinel(connection):
        connection = ""
    if not sql:
        raise ValueError("'sql' is required")
    if not connection and not hint:
        connections = await service.list_connections(org.org_id)
        names = [c["name"] for c in connections]
        raise ValueError(
            "Specify connection or hint. "
            + (f"Available: {', '.join(names)}" if names else "No connections configured.")
        )
    try:
        if connection:
            try:
                record = await service.get_connection(org.org_id, connection)
            except ConnectionNotFoundError:
                record = await service.resolve_connection(
                    org.org_id, connection, context_hints=context_hints
                )
        else:
            record = await service.resolve_connection(
                org.org_id, hint, context_hints=context_hints
            )
        connection_name = record["name"]
    except (ConnectionAmbiguousError, ConnectionNotFoundError) as e:
        raise ValueError(str(e)) from e
    result = await service.run_query(
        org.org_id, connection_name, sql, max_rows=_arg_limit(args, default=50, cap=200), source="chat"
    )
    return [result]


async def _add_memory(org: ActiveOrg, args: dict[str, Any]) -> list[dict[str, Any]]:
    from ...mcp.memory import _get_memory

    content = str(args.get("content", "")).strip()
    if not content:
        raise ValueError("'content' is required")
    mem = _get_memory()
    result = mem.add(content, user_id=org.namespace)
    return [{"id": result.get("id") if isinstance(result, dict) else str(result), "status": "ok"}]


async def _list_memories(org: ActiveOrg, args: dict[str, Any]) -> list[dict[str, Any]]:
    from ...mcp.memory import _get_memory

    mem = _get_memory()
    results = mem.get_all(user_id=org.namespace)
    memories = results.get("results", results) if isinstance(results, dict) else results
    out: list[dict[str, Any]] = []
    for m in memories or []:
        if isinstance(m, dict):
            out.append({"id": m.get("id"), "memory": m.get("memory") or m.get("content"), "created_at": m.get("created_at")})
        else:
            out.append({"memory": str(m)})
    return out


async def _delete_memory(org: ActiveOrg, args: dict[str, Any]) -> list[dict[str, Any]]:
    from ...mcp.memory import _get_memory

    memory_id = str(args.get("memory_id", "")).strip()
    if not memory_id:
        raise ValueError("'memory_id' is required")
    mem = _get_memory()
    mem.delete(memory_id)
    return [{"status": "ok", "deleted": memory_id}]


# Map tool name -> (handler, human label). Handlers take (org, tool_args).
_TOOL_HANDLERS = {
    "search_repositories": (_search_repositories, "repositories"),
    "search_memory": (_search_memory, "memory"),
    "search_knowledge_base": (_search_knowledge_base, "knowledge_base"),
    "search_web": (_search_web, "web"),
    "get_database_schema": (_get_database_schema, "databases"),
    "query_database": (_query_database, "databases"),
    "add_memory": (_add_memory, "memory"),
    "list_memories": (_list_memories, "memory"),
    "delete_memory": (_delete_memory, "memory"),
}

# OpenAI-style tool schemas (also reshaped for Anthropic below).
_OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_repositories",
            "description": (
                "Semantic search across the organization's indexed code "
                "repositories. Use for questions about code, APIs, "
                "implementation details, or file contents."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language search query"},
                    "limit": {"type": "integer", "description": "Max results (default 8)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": (
                "Semantic search over long-term persistent memories: prior "
                "decisions, preferences, facts, and notes stored for this "
                "organization."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language search query"},
                    "limit": {"type": "integer", "description": "Max results (default 8)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_knowledge_base",
            "description": (
                "Semantic search over uploaded knowledge-base documents "
                "(PDFs, Word, Excel, PowerPoint, text, etc.)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language search query"},
                    "limit": {"type": "integer", "description": "Max results (default 8)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": (
                "Semantic search over web pages the user has scraped and "
                "indexed (documentation sites, articles, reference pages)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language search query"},
                    "limit": {"type": "integer", "description": "Max results (default 8)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_database_schema",
            "description": (
                "Explore configured external databases. Without arguments, "
                "lists available connections. With 'connection' or 'hint', lists "
                "that database's tables (with curated descriptions and row "
                "estimates). With 'connection'/'hint' and 'table', describes the "
                "table in depth: columns, types, keys, indexes. Use hint when the "
                "user discussed a project but did not name the connection exactly."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "connection": {"type": "string", "description": "Exact connection name"},
                    "hint": {
                        "type": "string",
                        "description": "Project/repo topic to match a connection (e.g. context-forge)",
                    },
                    "table": {"type": "string", "description": "Table to describe in depth"},
                    "schema": {"type": "string", "description": "Schema name (optional)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_database",
            "description": (
                "Run a single read-only SQL query (SELECT/SHOW/EXPLAIN) on a "
                "configured database connection. Inspect the schema with "
                "get_database_schema first and use exact table/column names. "
                "Use hint when inferring the connection from conversation context."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "connection": {"type": "string", "description": "Exact connection name"},
                    "hint": {
                        "type": "string",
                        "description": "Project/repo topic to match a connection",
                    },
                    "sql": {"type": "string", "description": "Read-only SQL statement"},
                    "limit": {"type": "integer", "description": "Max rows (default 50)"},
                },
                "required": ["sql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_memory",
            "description": (
                "Save a memory, fact, decision, or note so it persists across "
                "sessions. Use this when the user explicitly asks you to remember "
                "something, or to store important decisions and context."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "The text to remember (fact, decision, note, etc.)"},
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_memories",
            "description": (
                "List all memories stored for this organization. Useful when "
                "the user asks what you remember or what's been saved."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_memory",
            "description": "Delete a memory by its ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string", "description": "The ID of the memory to delete"},
                },
                "required": ["memory_id"],
            },
        },
    },
]


# --------------------------------------------------------------------------- #
# LLM client resolution
# --------------------------------------------------------------------------- #
# Curated per-provider model catalog offered in the chat UI. Only providers
# whose API key is configured are exposed; the configured default model is
# always included even if it is not listed here.
_MODEL_CATALOG: dict[str, list[dict[str, str]]] = {
    "openai": [
        {"id": "gpt-5", "label": "GPT-5"},
        {"id": "gpt-5-mini", "label": "GPT-5 mini"},
        {"id": "gpt-5-nano", "label": "GPT-5 nano"},
        {"id": "gpt-4.1", "label": "GPT-4.1"},
        {"id": "gpt-4.1-mini", "label": "GPT-4.1 mini"},
        {"id": "gpt-4o", "label": "GPT-4o"},
        {"id": "gpt-4o-mini", "label": "GPT-4o mini"},
    ],
    "anthropic": [
        {"id": "claude-opus-4-5", "label": "Claude Opus 4.5"},
        {"id": "claude-sonnet-4-5", "label": "Claude Sonnet 4.5"},
        {"id": "claude-haiku-4-5", "label": "Claude Haiku 4.5"},
    ],
    "deepseek": [
        {"id": "deepseek-chat", "label": "DeepSeek Chat"},
        {"id": "deepseek-reasoner", "label": "DeepSeek Reasoner"},
    ],
}


def _provider_api_key(settings: Any, provider: str) -> str:
    return {
        "openai": settings.openai_api_key,
        "anthropic": settings.anthropic_api_key,
        "deepseek": settings.deepseek_api_key,
    }.get(provider, settings.openai_api_key)  # unknown providers are OpenAI-compatible


def _supports_temperature(model: str) -> bool:
    # GPT-5 and o-series reasoning models only accept the default temperature.
    return not model.startswith(("gpt-5", "o1", "o3", "o4"))


def _resolve_llm(provider: Optional[str] = None, model: Optional[str] = None) -> dict[str, Any]:
    """Resolve the effective LLM provider config.

    ``provider``/``model`` (e.g. from the request) override the configured
    defaults. Returns a dict with keys: family ("openai"|"anthropic"), model,
    api_key, base_url (optional). Raises HTTPException if no usable key is
    configured.
    """
    settings = get_settings()
    default_provider = (settings.llm_provider or "openai").lower()
    provider = (provider or default_provider).lower()
    if model:
        model = model.strip()
    elif provider == default_provider and settings.llm_model:
        model = settings.llm_model
    else:
        catalog = _MODEL_CATALOG.get(provider) or _MODEL_CATALOG["openai"]
        model = catalog[0]["id"]

    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise HTTPException(status_code=400, detail="No Anthropic API key configured (Settings → LLM).")
        return {"family": "anthropic", "model": model, "api_key": settings.anthropic_api_key}

    if provider == "deepseek":
        if not settings.deepseek_api_key:
            raise HTTPException(status_code=400, detail="No DeepSeek API key configured (Settings → LLM).")
        return {
            "family": "openai",
            "model": model,
            "api_key": settings.deepseek_api_key,
            "base_url": "https://api.deepseek.com",
        }

    # openai and any OpenAI-compatible provider
    if not settings.openai_api_key:
        raise HTTPException(status_code=400, detail="No OpenAI API key configured (Settings → LLM).")
    cfg: dict[str, Any] = {"family": "openai", "model": model, "api_key": settings.openai_api_key}
    if settings.embeddings_base_url and provider not in ("openai",):
        # OpenAI-compatible endpoints may share the base URL with embeddings.
        cfg["base_url"] = settings.embeddings_base_url
    return cfg


# --------------------------------------------------------------------------- #
# Request/response models
# --------------------------------------------------------------------------- #
class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    # Optional override of the configured default LLM (see GET /chat/models).
    provider: Optional[str] = None
    model: Optional[str] = None


class ToolCallTrace(BaseModel):
    tool: str
    source: str
    query: str
    result_count: int
    results: list[dict[str, Any]]
    error: Optional[str] = None


class ChatResponse(BaseModel):
    reply: str
    tool_calls: list[ToolCallTrace]
    sources_used: dict[str, bool]
    model: str


def _trace_query(name: str, args: dict[str, Any]) -> str:
    """Human-readable summary of a tool invocation for the trace panel."""
    if name == "query_database":
        conn = args.get("connection") or args.get("hint") or "?"
        return f"{conn}: {str(args.get('sql', '')).strip()}"
    if name == "get_database_schema":
        parts = [
            str(args[k])
            for k in ("connection", "hint", "table")
            if args.get(k)
        ]
        return " / ".join(parts) if parts else "(list connections)"
    if name == "add_memory":
        content = str(args.get("content", "")).strip()
        return content[:100] + ("…" if len(content) > 100 else "")
    if name == "delete_memory":
        return f"delete {args.get('memory_id', '?')}"
    if name == "list_memories":
        return "(list all)"
    return _arg_query(args)


async def _run_tool(
    org: ActiveOrg,
    name: str,
    args: dict[str, Any],
    *,
    context_hints: list[str] | None = None,
) -> ToolCallTrace:
    handler, source = _TOOL_HANDLERS[name]
    query = _trace_query(name, args)
    try:
        if name in ("get_database_schema", "query_database"):
            results = await handler(org, args, context_hints=context_hints)
        else:
            results = await handler(org, args)
        return ToolCallTrace(
            tool=name, source=source, query=query,
            result_count=len(results), results=results,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("chat tool %s failed: %s", name, e)
        return ToolCallTrace(
            tool=name, source=source, query=query,
            result_count=0, results=[], error=str(e),
        )


def _trace_to_tool_content(trace: ToolCallTrace) -> str:
    """Compact JSON of a tool result for feeding back to the model."""
    payload: dict[str, Any] = {"count": trace.result_count}
    if trace.error:
        payload["error"] = trace.error
    else:
        # Trim long content so we don't blow the context window.
        trimmed = []
        for r in trace.results:
            item = dict(r)
            content = item.get("content")
            if isinstance(content, str) and len(content) > 800:
                item["content"] = content[:800] + "…"
            trimmed.append(item)
        payload["results"] = trimmed
    return json.dumps(payload, ensure_ascii=False, default=str)


async def _chat_openai(llm: dict[str, Any], org: ActiveOrg, req: ChatRequest) -> ChatResponse:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=llm["api_key"], base_url=llm.get("base_url"))
    context_hints = _extract_context_hints(req.messages)
    system_prompt = await _build_system_prompt(org, req.messages)
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for m in req.messages:
        messages.append({"role": m.role, "content": m.content})

    temperature_kwargs: dict[str, Any] = (
        {"temperature": 0.2} if _supports_temperature(llm["model"]) else {}
    )
    traces: list[ToolCallTrace] = []
    reply = ""
    for _ in range(MAX_TOOL_ITERATIONS):
        completion = await client.chat.completions.create(
            model=llm["model"],
            messages=messages,
            tools=_OPENAI_TOOLS,
            **temperature_kwargs,
        )
        choice = completion.choices[0]
        msg = choice.message
        tool_calls = msg.tool_calls or []
        if not tool_calls:
            if choice.finish_reason == "length":
                logger.warning("chat: response truncated by max_tokens, auto-continuing")
                messages.append({"role": "assistant", "content": msg.content or ""})
                messages.append({"role": "user", "content": CONTINUE_NUDGE})
                reply += (msg.content or "")
                continue
            reply += (msg.content or "")
            break

        # Record the assistant turn (with its tool calls) before answering them.
        messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in tool_calls
                ],
            }
        )
        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            if name not in _TOOL_HANDLERS:
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": '{"error": "unknown tool"}'})
                continue
            trace = await _run_tool(org, name, args, context_hints=context_hints)
            traces.append(trace)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": _trace_to_tool_content(trace)})
    else:
        # Loop exhausted without a final text answer — do one last plain call.
        final = await client.chat.completions.create(model=llm["model"], messages=messages, **temperature_kwargs)
        reply = final.choices[0].message.content or ""

    return _build_response(reply, traces, llm["model"])


def _anthropic_messages(req: ChatRequest) -> list[dict[str, Any]]:
    """History for the Anthropic API, merging consecutive same-role messages.

    The UI drops failed (empty) assistant turns from its history, which can
    leave two user messages in a row — Anthropic requires alternating roles.
    """
    out: list[dict[str, Any]] = []
    for m in req.messages:
        if out and out[-1]["role"] == m.role:
            out[-1]["content"] += "\n\n" + m.content
        else:
            out.append({"role": m.role, "content": m.content})
    return out


async def _chat_anthropic(llm: dict[str, Any], org: ActiveOrg, req: ChatRequest) -> ChatResponse:
    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        raise HTTPException(
            status_code=400,
            detail="Anthropic provider selected but the 'anthropic' package is not installed.",
        )

    client = AsyncAnthropic(api_key=llm["api_key"])
    context_hints = _extract_context_hints(req.messages)
    system_prompt = await _build_system_prompt(org, req.messages)
    tools = [
        {
            "name": t["function"]["name"],
            "description": t["function"]["description"],
            "input_schema": t["function"]["parameters"],
        }
        for t in _OPENAI_TOOLS
    ]
    messages: list[dict[str, Any]] = _anthropic_messages(req)

    traces: list[ToolCallTrace] = []
    reply = ""
    for _ in range(MAX_TOOL_ITERATIONS):
        resp = await client.messages.create(
            model=llm["model"],
            max_tokens=ANTHROPIC_MAX_TOKENS,
            system=system_prompt,
            tools=tools,
            messages=messages,
        )
        tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
        text_parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
        if not tool_uses:
            reply += "".join(text_parts)
            if resp.stop_reason == "max_tokens":
                logger.warning("chat: response truncated by max_tokens, auto-continuing")
                messages.append({"role": "assistant", "content": [b.model_dump() for b in resp.content]})
                messages.append({"role": "user", "content": CONTINUE_NUDGE})
                continue
            break

        messages.append({"role": "assistant", "content": [b.model_dump() for b in resp.content]})
        tool_results = []
        for tu in tool_uses:
            name = tu.name
            args = tu.input if isinstance(tu.input, dict) else {}
            if name not in _TOOL_HANDLERS:
                tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": '{"error": "unknown tool"}'})
                continue
            trace = await _run_tool(org, name, args, context_hints=context_hints)
            traces.append(trace)
            tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": _trace_to_tool_content(trace)})
        messages.append({"role": "user", "content": tool_results})
    else:
        reply = "(The agent reached the tool-call limit without a final answer.)"

    return _build_response(reply, traces, llm["model"])


def _build_response(reply: str, traces: list[ToolCallTrace], model: str) -> ChatResponse:
    sources_used = {
        "repositories": any(t.source == "repositories" and not t.error for t in traces),
        "memory": any(t.source == "memory" and not t.error for t in traces),
        "knowledge_base": any(t.source == "knowledge_base" and not t.error for t in traces),
        "web": any(t.source == "web" and not t.error for t in traces),
        "databases": any(t.source == "databases" and not t.error for t in traces),
    }
    return ChatResponse(reply=reply, tool_calls=traces, sources_used=sources_used, model=model)


# --------------------------------------------------------------------------- #
# DSML fallback: DeepSeek's reasoner sometimes writes its tool calls as inline
# markup (<|DSML|>invoke name="..."> ... ) in the text/reasoning stream instead
# of using structured tool_calls, which stalls the agent loop. Detect that
# markup, execute the intended calls, and keep the loop going.
# --------------------------------------------------------------------------- #
# DeepSeek's tokens use the FULLWIDTH vertical bar U+FF5C ("｜"), not ASCII "|";
# accept either so the markup is actually detected. (Matching only ASCII "|"
# silently disabled recovery, stranding the leaked markup as the final answer.)
_PIPE = r"[|｜]"
_DSML_HINT_RE = re.compile(rf"<{_PIPE}DSML{_PIPE}>|<{_PIPE}tool[^|｜>]*{_PIPE}>")
_DSML_INVOKE_RE = re.compile(r'invoke\s+name="([\w.-]+)"')
_DSML_PARAM_RE = re.compile(r'parameter\s+name="([\w.-]+)"[^>]*>([^<]*)')
# Rendered/stored text cleanup: special tokens plus the tag fragment after them.
_DSML_STRIP_RE = re.compile(rf"</?{_PIPE}DSML{_PIPE}>[^<]*|<{_PIPE}[^|｜>]*{_PIPE}>")


def _strip_dsml(text: str) -> str:
    return _DSML_STRIP_RE.sub("", text).strip()


def _parse_dsml_tool_calls(text: str) -> list[tuple[str, dict[str, Any]]]:
    """Extract (tool_name, args) pairs from inline DSML tool-call markup."""
    if not _DSML_HINT_RE.search(text):
        return []
    calls: list[tuple[str, dict[str, Any]]] = []
    parts = _DSML_INVOKE_RE.split(text)
    # split() yields [before, name1, body1, name2, body2, ...]
    for i in range(1, len(parts) - 1, 2):
        name = parts[i]
        body = parts[i + 1]
        if name not in _TOOL_HANDLERS:
            continue
        args = {m.group(1): m.group(2).strip() for m in _DSML_PARAM_RE.finditer(body)}
        calls.append((name, args))
    return calls


# --------------------------------------------------------------------------- #
# Streaming (SSE)
# --------------------------------------------------------------------------- #
def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


def _done_event(traces: list[ToolCallTrace], model: str) -> dict[str, Any]:
    resp = _build_response("", traces, model)
    return {"type": "done", "model": model, "sources_used": resp.sources_used}


async def _stream_openai(llm: dict[str, Any], org: ActiveOrg, req: ChatRequest):
    """Yield chat events ({type: text|reasoning|tool_start|tool_result|done})."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=llm["api_key"], base_url=llm.get("base_url"))
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend({"role": m.role, "content": m.content} for m in req.messages)

    def _kwargs(with_tools: bool) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"model": llm["model"], "messages": messages, "stream": True}
        if with_tools:
            kwargs["tools"] = _OPENAI_TOOLS
        if _supports_temperature(llm["model"]):
            kwargs["temperature"] = 0.2
        return kwargs

    traces: list[ToolCallTrace] = []
    emitted_text = False
    answered = False
    # Set right before an auto-continue so the next round's first delta skips
    # the "\n\n" turn-separator below — a truncation continuation resumes
    # mid-text and must join seamlessly, unlike a genuinely new turn after a
    # tool call.
    suppress_next_spacer = False
    for _ in range(MAX_TOOL_ITERATIONS):
        skip_spacer = suppress_next_spacer
        suppress_next_spacer = False
        stream = await client.chat.completions.create(**_kwargs(with_tools=True))
        calls: dict[int, dict[str, str]] = {}
        round_text = ""
        round_reasoning = ""
        finish_reason: Optional[str] = None
        async for chunk in stream:
            if not chunk.choices:
                continue
            if chunk.choices[0].finish_reason:
                finish_reason = chunk.choices[0].finish_reason
            delta = chunk.choices[0].delta
            # DeepSeek reasoner (and compatible endpoints) stream reasoning here.
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                round_reasoning += reasoning
                yield {"type": "reasoning", "delta": reasoning}
            if delta.content:
                if emitted_text and not round_text and not skip_spacer:
                    yield {"type": "text", "delta": "\n\n"}
                round_text += delta.content
                emitted_text = True
                yield {"type": "text", "delta": delta.content}
            for tc in delta.tool_calls or []:
                acc = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    acc["id"] = tc.id
                if tc.function and tc.function.name:
                    acc["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    acc["arguments"] += tc.function.arguments

        if not calls:
            if finish_reason == "length":
                # Cut off by the token limit, not a real stop — keep the model
                # going instead of silently ending the turn on a half-written
                # answer (broken code fences/tables and no more text).
                logger.warning("chat: response truncated by max_tokens, auto-continuing")
                messages.append({"role": "assistant", "content": round_text})
                messages.append({"role": "user", "content": CONTINUE_NUDGE})
                suppress_next_spacer = True
                continue
            dsml_calls = _parse_dsml_tool_calls(round_text + "\n" + round_reasoning)
            if not dsml_calls:
                answered = True
                break
            # The model wrote its tool calls as inline markup instead of using
            # the structured interface — execute them anyway and keep looping.
            logger.warning("chat: recovered %d DSML inline tool call(s)", len(dsml_calls))
            messages.append(
                {
                    "role": "assistant",
                    "content": _strip_dsml(round_text),
                    "tool_calls": [
                        {
                            "id": f"dsml-{i}",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
                        }
                        for i, (name, args) in enumerate(dsml_calls)
                    ],
                }
            )
            for i, (name, args) in enumerate(dsml_calls):
                _, source = _TOOL_HANDLERS[name]
                yield {"type": "tool_start", "tool": name, "source": source, "query": _trace_query(name, args)}
                trace = await _run_tool(org, name, args)
                traces.append(trace)
                yield {"type": "tool_result", **trace.model_dump()}
                messages.append(
                    {"role": "tool", "tool_call_id": f"dsml-{i}", "content": _trace_to_tool_content(trace)}
                )
            continue

        messages.append(
            {
                "role": "assistant",
                "content": round_text,
                "tool_calls": [
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {"name": c["name"], "arguments": c["arguments"]},
                    }
                    for c in (calls[i] for i in sorted(calls))
                ],
            }
        )
        for idx in sorted(calls):
            c = calls[idx]
            if c["name"] not in _TOOL_HANDLERS:
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": '{"error": "unknown tool"}'})
                continue
            try:
                args = json.loads(c["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            _, source = _TOOL_HANDLERS[c["name"]]
            yield {"type": "tool_start", "tool": c["name"], "source": source, "query": _trace_query(c["name"], args)}
            trace = await _run_tool(org, c["name"], args)
            traces.append(trace)
            yield {"type": "tool_result", **trace.model_dump()}
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": _trace_to_tool_content(trace)})

    if not answered:
        # Loop exhausted — one last plain call so the user still gets an answer.
        stream = await client.chat.completions.create(**_kwargs(with_tools=False))
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                if emitted_text:
                    yield {"type": "text", "delta": "\n\n"}
                    emitted_text = False
                yield {"type": "text", "delta": delta.content}

    yield _done_event(traces, llm["model"])


async def _stream_anthropic(llm: dict[str, Any], org: ActiveOrg, req: ChatRequest):
    """Yield chat events ({type: text|reasoning|tool_start|tool_result|done})."""
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=llm["api_key"])
    tools = [
        {
            "name": t["function"]["name"],
            "description": t["function"]["description"],
            "input_schema": t["function"]["parameters"],
        }
        for t in _OPENAI_TOOLS
    ]
    messages: list[dict[str, Any]] = _anthropic_messages(req)

    traces: list[ToolCallTrace] = []
    emitted_text = False
    answered = False
    # Set right before an auto-continue so the next round's first delta skips
    # the "\n\n" turn-separator below — a truncation continuation resumes
    # mid-text and must join seamlessly, unlike a genuinely new turn after a
    # tool call.
    suppress_next_spacer = False
    for _ in range(MAX_TOOL_ITERATIONS):
        skip_spacer = suppress_next_spacer
        suppress_next_spacer = False
        round_text = False
        async with client.messages.stream(
            model=llm["model"],
            max_tokens=ANTHROPIC_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
        ) as stream:
            async for event in stream:
                if getattr(event, "type", None) != "content_block_delta":
                    continue
                d = event.delta
                if getattr(d, "type", None) == "text_delta" and d.text:
                    if emitted_text and not round_text and not skip_spacer:
                        yield {"type": "text", "delta": "\n\n"}
                    round_text = True
                    emitted_text = True
                    yield {"type": "text", "delta": d.text}
                elif getattr(d, "type", None) == "thinking_delta" and getattr(d, "thinking", None):
                    yield {"type": "reasoning", "delta": d.thinking}
            final = await stream.get_final_message()

        tool_uses = [b for b in final.content if getattr(b, "type", None) == "tool_use"]
        if not tool_uses:
            if final.stop_reason == "max_tokens":
                # Cut off by the token limit, not a real stop — keep the model
                # going instead of silently ending the turn on a half-written
                # answer (broken code fences/tables and no more text).
                logger.warning("chat: response truncated by max_tokens, auto-continuing")
                messages.append({"role": "assistant", "content": [b.model_dump() for b in final.content]})
                messages.append({"role": "user", "content": CONTINUE_NUDGE})
                suppress_next_spacer = True
                continue
            answered = True
            break

        messages.append({"role": "assistant", "content": [b.model_dump() for b in final.content]})
        tool_results = []
        for tu in tool_uses:
            args = tu.input if isinstance(tu.input, dict) else {}
            if tu.name not in _TOOL_HANDLERS:
                tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": '{"error": "unknown tool"}'})
                continue
            _, source = _TOOL_HANDLERS[tu.name]
            yield {"type": "tool_start", "tool": tu.name, "source": source, "query": _trace_query(tu.name, args)}
            trace = await _run_tool(org, tu.name, args)
            traces.append(trace)
            tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": _trace_to_tool_content(trace)})
            yield {"type": "tool_result", **trace.model_dump()}
        messages.append({"role": "user", "content": tool_results})

    if not answered:
        yield {"type": "text", "delta": "\n\n(The agent reached the tool-call limit without a final answer.)"}

    yield _done_event(traces, llm["model"])


@router.get("/models")
async def list_models(org: ActiveOrg = Depends(get_active_org)):
    """List the LLM models the chat can use, based on which API keys are set."""
    settings = get_settings()
    models: list[dict[str, str]] = []
    for provider, entries in _MODEL_CATALOG.items():
        if not _provider_api_key(settings, provider):
            continue
        models.extend({"id": e["id"], "provider": provider, "label": e["label"]} for e in entries)

    # Make sure the configured default is always selectable (custom models or
    # OpenAI-compatible providers not in the catalog).
    default_provider = (settings.llm_provider or "openai").lower()
    default_model = settings.llm_model or ""
    default = None
    if default_model and _provider_api_key(settings, default_provider):
        if not any(m["provider"] == default_provider and m["id"] == default_model for m in models):
            models.insert(0, {"id": default_model, "provider": default_provider, "label": default_model})
        default = {"provider": default_provider, "model": default_model}
    elif models:
        default = {"provider": models[0]["provider"], "model": models[0]["id"]}

    return {"models": models, "default": default}


def _validated_llm(req: ChatRequest) -> dict[str, Any]:
    if not req.messages or req.messages[-1].role != "user":
        raise HTTPException(status_code=400, detail="The last message must be from the user.")
    llm = _resolve_llm(req.provider, req.model)
    if llm["family"] == "anthropic":
        try:
            import anthropic  # noqa: F401
        except ImportError:
            raise HTTPException(
                status_code=400,
                detail="Anthropic provider selected but the 'anthropic' package is not installed.",
            )
    return llm


@router.post("/stream")
async def chat_stream(req: ChatRequest, org: ActiveOrg = Depends(get_active_org)):
    """Streaming variant of the chat endpoint (Server-Sent Events).

    Emits ``data: {json}`` frames with events: ``reasoning`` / ``text``
    (incremental deltas), ``tool_start`` / ``tool_result`` (live retrieval
    trace), ``done`` (model + sources summary) and ``error``.
    """
    llm = _validated_llm(req)

    async def gen():
        try:
            source = (
                _stream_anthropic(llm, org, req)
                if llm["family"] == "anthropic"
                else _stream_openai(llm, org, req)
            )
            async for event in source:
                yield _sse(event)
        except Exception as e:  # noqa: BLE001
            logger.exception("chat stream failed")
            yield _sse({"type": "error", "message": f"Agent chat failed: {e}"})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Belt and braces: disable buffering in nginx-style proxies.
            "X-Accel-Buffering": "no",
        },
    )


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest, org: ActiveOrg = Depends(get_active_org)):
    """Chat with a retrieval agent scoped to the active organization.

    The agent can search indexed repositories, memory, and the knowledge base.
    Every search it performs is returned in ``tool_calls`` so retrieval can be
    inspected directly.
    """
    llm = _validated_llm(req)
    try:
        if llm["family"] == "anthropic":
            return await _chat_anthropic(llm, org, req)
        return await _chat_openai(llm, org, req)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error("chat failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Agent chat failed: {e}")
