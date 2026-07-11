"""LLM capture agent for the Telegram quick-capture channel.

Takes raw forwarded text (a customer chat excerpt, OCR'd screenshot text, or a
free-form note) and turns it into a structured memory: infers a client/project
name and a category, then stores it via ``memory_add``. This is deliberately
NOT a reuse of ``api/routes/chat.py``'s agent — that one is scoped to the
interactive chat UI (streaming, six retrieval tools, DSML fallback, per-request
provider override). This is a short, single-purpose tool loop over exactly two
tools (``memory_search``, ``memory_add``), called in-process rather than over
MCP transport.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from ..config import get_settings
from ..mcp.memory import memory_add, memory_search
from ..tenancy import get_namespace_for_org

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 4

SYSTEM_PROMPT = (
    "You turn a raw note forwarded from Telegram (a customer chat excerpt, "
    "OCR'd screenshot text, or a free-form note) into a structured memory.\n\n"
    "Steps:\n"
    "1. Optionally call memory_search to check whether the client/project "
    "mentioned is already known, and reuse the exact same naming if so.\n"
    "2. Call memory_add exactly once with:\n"
    "   - content: a short summary followed by the relevant original text\n"
    "   - client: the inferred client/project name, or omit it if none is "
    "mentioned\n"
    "   - type: one of \"deadline\", \"request\", \"decision\", \"note\" "
    "(best guess if ambiguous)\n"
    "   - summary: a one-sentence summary for the confirmation message sent "
    "back to the user\n\n"
    "Always call memory_add before finishing — every message must be saved, "
    "even if the content is short or unclear (use type \"note\" as a "
    "fallback). Do not call memory_add more than once."
)

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "memory_search",
            "description": (
                "Search existing memories to check if a client/project is "
                "already known, so the same naming can be reused."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language search query"},
                    "limit": {"type": "integer", "description": "Max results (default 5)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_add",
            "description": "Save the structured memory. Call exactly once per message.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Summary + relevant original text"},
                    "client": {"type": "string", "description": "Inferred client/project name"},
                    "type": {
                        "type": "string",
                        "enum": ["deadline", "request", "decision", "note"],
                        "description": "Category of the note",
                    },
                    "summary": {
                        "type": "string",
                        "description": "One-sentence summary for the confirmation message",
                    },
                },
                "required": ["content", "type", "summary"],
            },
        },
    },
]


def _resolve_llm() -> dict[str, Any]:
    """Resolve the configured default LLM provider/model/key.

    Simplified single-provider version of ``chat.py``'s ``_resolve_llm``: the
    capture agent always uses the org-configured default — there is no
    per-request override coming from a Telegram message.
    """
    settings = get_settings()
    provider = (settings.llm_provider or "openai").lower()
    model = settings.llm_model or "gpt-4o-mini"

    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise RuntimeError("No Anthropic API key configured (Settings -> LLM).")
        return {"family": "anthropic", "model": model, "api_key": settings.anthropic_api_key}

    if provider == "deepseek":
        if not settings.deepseek_api_key:
            raise RuntimeError("No DeepSeek API key configured (Settings -> LLM).")
        return {
            "family": "openai",
            "model": model,
            "api_key": settings.deepseek_api_key,
            "base_url": "https://api.deepseek.com",
        }

    if not settings.openai_api_key:
        raise RuntimeError("No OpenAI API key configured (Settings -> LLM).")
    return {"family": "openai", "model": model, "api_key": settings.openai_api_key}


async def _resolve_namespace(org_id: int) -> str:
    ns = await get_namespace_for_org(org_id)
    if not ns:
        raise RuntimeError(f"No memory namespace found for org_id={org_id}")
    return ns


def _extract_memory_id(add_result: dict[str, Any]) -> Optional[str]:
    """Best-effort extraction of the created memory's id from mem0's add() shape.

    mem0's ``Memory.add`` return shape has varied across versions (a bare list
    of created memories, or ``{"results": [...]}``); ``memory_add`` (mcp/memory.py)
    passes it through unwrapped as ``result["memory"]``, so handle both here.
    """
    memory = add_result.get("memory")
    if isinstance(memory, dict):
        entries = memory.get("results")
        if isinstance(entries, list) and entries:
            return entries[0].get("id")
        if "id" in memory:
            return memory.get("id")
    elif isinstance(memory, list) and memory:
        return memory[0].get("id")
    return None


async def _execute_tool(
    name: str, args: dict[str, Any], namespace: str, source_meta: dict[str, Any]
) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    """Run one tool call. Returns (content fed back to the model, captured result)."""
    if name == "memory_search":
        return await memory_search(
            args.get("query", ""), limit=int(args.get("limit") or 5), user_id=namespace
        ), None

    if name == "memory_add":
        metadata = {
            "source": "telegram",
            "client": args.get("client"),
            "type": args.get("type"),
            **source_meta,
        }
        result = await memory_add(args.get("content", ""), metadata=metadata, user_id=namespace)
        captured = {
            "client": args.get("client"),
            "type": args.get("type"),
            "summary": args.get("summary", ""),
            "memory_id": _extract_memory_id(result) if result.get("status") == "ok" else None,
        }
        return result, captured

    return {"status": "error", "error": f"unknown tool {name}"}, None


async def _run_openai(llm: dict[str, Any], text: str, namespace: str, source_meta: dict[str, Any]) -> dict[str, Any]:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=llm["api_key"], base_url=llm.get("base_url"))
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]

    captured: Optional[dict[str, Any]] = None
    for _ in range(MAX_TOOL_ITERATIONS):
        completion = await client.chat.completions.create(
            model=llm["model"], messages=messages, tools=_TOOLS
        )
        msg = completion.choices[0].message
        tool_calls = msg.tool_calls or []
        if not tool_calls:
            break

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
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            content, tool_captured = await _execute_tool(tc.function.name, args, namespace, source_meta)
            if tool_captured is not None:
                captured = tool_captured
            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": json.dumps(content, ensure_ascii=False, default=str)}
            )
        if captured is not None:
            break

    return captured or {"client": None, "type": "note", "summary": "", "memory_id": None}


async def _run_anthropic(llm: dict[str, Any], text: str, namespace: str, source_meta: dict[str, Any]) -> dict[str, Any]:
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=llm["api_key"])
    tools = [
        {"name": t["function"]["name"], "description": t["function"]["description"], "input_schema": t["function"]["parameters"]}
        for t in _TOOLS
    ]
    messages: list[dict[str, Any]] = [{"role": "user", "content": text}]

    captured: Optional[dict[str, Any]] = None
    for _ in range(MAX_TOOL_ITERATIONS):
        resp = await client.messages.create(
            model=llm["model"], max_tokens=1024, system=SYSTEM_PROMPT, tools=tools, messages=messages
        )
        tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
        if not tool_uses:
            break

        messages.append({"role": "assistant", "content": [b.model_dump() for b in resp.content]})
        tool_results = []
        for tu in tool_uses:
            args = tu.input if isinstance(tu.input, dict) else {}
            content, tool_captured = await _execute_tool(tu.name, args, namespace, source_meta)
            if tool_captured is not None:
                captured = tool_captured
            tool_results.append(
                {"type": "tool_result", "tool_use_id": tu.id, "content": json.dumps(content, ensure_ascii=False, default=str)}
            )
        messages.append({"role": "user", "content": tool_results})
        if captured is not None:
            break

    return captured or {"client": None, "type": "note", "summary": "", "memory_id": None}


async def run_capture(text: str, org_id: int, source_meta: dict[str, Any]) -> dict[str, Any]:
    """Extract structure from ``text`` and save it as a memory for ``org_id``.

    ``source_meta`` is merged into the stored memory's metadata (e.g.
    ``telegram_message_id``, ``ts``) so the origin of the memory is traceable.

    Returns a dict with ``client``, ``type``, ``summary``, and ``memory_id`` —
    used by the webhook route to compose the Telegram confirmation reply.
    """
    llm = _resolve_llm()
    namespace = await _resolve_namespace(org_id)

    if llm["family"] == "anthropic":
        return await _run_anthropic(llm, text, namespace, source_meta)
    return await _run_openai(llm, text, namespace, source_meta)
