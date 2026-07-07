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
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...config import get_settings
from ..deps import ActiveOrg, get_active_org

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

# Cap the agent loop so a misbehaving model can't spin forever.
MAX_TOOL_ITERATIONS = 6

SYSTEM_PROMPT = (
    "You are the context-forge test agent. You help a developer verify that "
    "their self-hosted context infrastructure returns useful results.\n\n"
    "You have these retrieval tools:\n"
    "- search_repositories: semantic search over indexed code repositories.\n"
    "- search_memory: semantic search over long-term persistent memories.\n"
    "- search_knowledge_base: semantic search over uploaded documents.\n"
    "- get_database_schema: list configured database connections, browse a "
    "database's tables, or describe one table in depth (columns, keys, "
    "curated descriptions).\n"
    "- query_database: run a single read-only SQL query on a configured "
    "database connection.\n\n"
    "For any question that could benefit from stored context, call the "
    "relevant tool(s) before answering — prefer searching over guessing. For "
    "database questions, inspect the schema with get_database_schema before "
    "writing SQL, and use the exact table/column names it returns. When "
    "you use results, cite where they came from (repo/file, memory, "
    "document title, or connection/table) so the developer can confirm "
    "retrieval is working. If a search returns nothing, say so plainly. "
    "Keep answers concise."
)


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


async def _get_database_schema(org: ActiveOrg, args: dict[str, Any]) -> list[dict[str, Any]]:
    """Three granularities: no args -> connections; connection -> tables; +table -> columns."""
    from ...datasources import service

    connection = str(args.get("connection") or "").strip()
    table = str(args.get("table") or "").strip()
    schema = str(args.get("schema") or "").strip() or None

    if not connection:
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
    if not table:
        overview = await service.schema_overview(org.org_id, connection, schema=schema)
        return [
            {
                "table": t["name"],
                "description": t.get("description") or t.get("comment"),
                "column_count": t["column_count"],
                "estimated_rows": t.get("estimated_rows"),
            }
            for t in overview["tables"]
        ]
    detail = await service.describe_table(org.org_id, connection, table, schema=schema)
    return [detail]


async def _query_database(org: ActiveOrg, args: dict[str, Any]) -> list[dict[str, Any]]:
    from ...datasources import service

    connection = str(args.get("connection") or "").strip()
    sql = str(args.get("sql") or "").strip()
    if not connection or not sql:
        raise ValueError("Both 'connection' and 'sql' are required")
    result = await service.run_query(
        org.org_id, connection, sql, max_rows=_arg_limit(args, default=50, cap=200), source="chat"
    )
    return [result]


# Map tool name -> (handler, human label). Handlers take (org, tool_args).
_TOOL_HANDLERS = {
    "search_repositories": (_search_repositories, "repositories"),
    "search_memory": (_search_memory, "memory"),
    "search_knowledge_base": (_search_knowledge_base, "knowledge_base"),
    "get_database_schema": (_get_database_schema, "databases"),
    "query_database": (_query_database, "databases"),
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
            "name": "get_database_schema",
            "description": (
                "Explore configured external databases. Without arguments, "
                "lists available connections. With 'connection', lists that "
                "database's tables (with curated descriptions and row "
                "estimates). With 'connection' and 'table', describes the "
                "table in depth: columns, types, keys, indexes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "connection": {"type": "string", "description": "Connection name"},
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
                "get_database_schema first and use exact table/column names."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "connection": {"type": "string", "description": "Connection name"},
                    "sql": {"type": "string", "description": "Read-only SQL statement"},
                    "limit": {"type": "integer", "description": "Max rows (default 50)"},
                },
                "required": ["connection", "sql"],
            },
        },
    },
]


# --------------------------------------------------------------------------- #
# LLM client resolution
# --------------------------------------------------------------------------- #
def _resolve_llm() -> dict[str, Any]:
    """Resolve the effective LLM provider config from settings.

    Returns a dict with keys: family ("openai"|"anthropic"), model, api_key,
    base_url (optional). Raises HTTPException if no usable key is configured.
    """
    settings = get_settings()
    provider = (settings.llm_provider or "openai").lower()
    model = settings.llm_model or "gpt-4o-mini"

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
        return f"{args.get('connection', '?')}: {str(args.get('sql', '')).strip()}"
    if name == "get_database_schema":
        parts = [str(args[k]) for k in ("connection", "table") if args.get(k)]
        return " / ".join(parts) if parts else "(list connections)"
    return _arg_query(args)


async def _run_tool(org: ActiveOrg, name: str, args: dict[str, Any]) -> ToolCallTrace:
    handler, source = _TOOL_HANDLERS[name]
    query = _trace_query(name, args)
    try:
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
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in req.messages:
        messages.append({"role": m.role, "content": m.content})

    traces: list[ToolCallTrace] = []
    reply = ""
    for _ in range(MAX_TOOL_ITERATIONS):
        completion = await client.chat.completions.create(
            model=llm["model"],
            messages=messages,
            tools=_OPENAI_TOOLS,
            temperature=0.2,
        )
        choice = completion.choices[0]
        msg = choice.message
        tool_calls = msg.tool_calls or []
        if not tool_calls:
            reply = msg.content or ""
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
            trace = await _run_tool(org, name, args)
            traces.append(trace)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": _trace_to_tool_content(trace)})
    else:
        # Loop exhausted without a final text answer — do one last plain call.
        final = await client.chat.completions.create(model=llm["model"], messages=messages, temperature=0.2)
        reply = final.choices[0].message.content or ""

    return _build_response(reply, traces, llm["model"])


async def _chat_anthropic(llm: dict[str, Any], org: ActiveOrg, req: ChatRequest) -> ChatResponse:
    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        raise HTTPException(
            status_code=400,
            detail="Anthropic provider selected but the 'anthropic' package is not installed.",
        )

    client = AsyncAnthropic(api_key=llm["api_key"])
    tools = [
        {
            "name": t["function"]["name"],
            "description": t["function"]["description"],
            "input_schema": t["function"]["parameters"],
        }
        for t in _OPENAI_TOOLS
    ]
    messages: list[dict[str, Any]] = [{"role": m.role, "content": m.content} for m in req.messages]

    traces: list[ToolCallTrace] = []
    reply = ""
    for _ in range(MAX_TOOL_ITERATIONS):
        resp = await client.messages.create(
            model=llm["model"],
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
        )
        tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
        text_parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
        if not tool_uses:
            reply = "".join(text_parts)
            break

        messages.append({"role": "assistant", "content": [b.model_dump() for b in resp.content]})
        tool_results = []
        for tu in tool_uses:
            name = tu.name
            args = tu.input if isinstance(tu.input, dict) else {}
            if name not in _TOOL_HANDLERS:
                tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": '{"error": "unknown tool"}'})
                continue
            trace = await _run_tool(org, name, args)
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
        "databases": any(t.source == "databases" and not t.error for t in traces),
    }
    return ChatResponse(reply=reply, tool_calls=traces, sources_used=sources_used, model=model)


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest, org: ActiveOrg = Depends(get_active_org)):
    """Chat with a retrieval agent scoped to the active organization.

    The agent can search indexed repositories, memory, and the knowledge base.
    Every search it performs is returned in ``tool_calls`` so retrieval can be
    inspected directly.
    """
    if not req.messages or req.messages[-1].role != "user":
        raise HTTPException(status_code=400, detail="The last message must be from the user.")

    llm = _resolve_llm()
    try:
        if llm["family"] == "anthropic":
            return await _chat_anthropic(llm, org, req)
        return await _chat_openai(llm, org, req)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error("chat failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Agent chat failed: {e}")
