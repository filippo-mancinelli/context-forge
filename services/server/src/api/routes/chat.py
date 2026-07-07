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
from fastapi.responses import StreamingResponse
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
    for _ in range(MAX_TOOL_ITERATIONS):
        stream = await client.chat.completions.create(**_kwargs(with_tools=True))
        calls: dict[int, dict[str, str]] = {}
        round_text = ""
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            # DeepSeek reasoner (and compatible endpoints) stream reasoning here.
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                yield {"type": "reasoning", "delta": reasoning}
            if delta.content:
                if emitted_text and not round_text:
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
            answered = True
            break

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
    for _ in range(MAX_TOOL_ITERATIONS):
        round_text = False
        async with client.messages.stream(
            model=llm["model"],
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
        ) as stream:
            async for event in stream:
                if getattr(event, "type", None) != "content_block_delta":
                    continue
                d = event.delta
                if getattr(d, "type", None) == "text_delta" and d.text:
                    if emitted_text and not round_text:
                        yield {"type": "text", "delta": "\n\n"}
                    round_text = True
                    emitted_text = True
                    yield {"type": "text", "delta": d.text}
                elif getattr(d, "type", None) == "thinking_delta" and getattr(d, "thinking", None):
                    yield {"type": "reasoning", "delta": d.thinking}
            final = await stream.get_final_message()

        tool_uses = [b for b in final.content if getattr(b, "type", None) == "tool_use"]
        if not tool_uses:
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
