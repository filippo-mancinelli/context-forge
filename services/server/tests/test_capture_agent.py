"""Unit tests for the Telegram capture agent (src/telegram/capture_agent.py).

No real LLM calls: openai.AsyncOpenAI is monkeypatched with a fake client that
returns canned tool-call responses. memory_search/memory_add and namespace
resolution are monkeypatched too, so these tests exercise only the tool loop
and metadata composition in capture_agent.py.
"""
from __future__ import annotations

import asyncio
import json

import openai

from src.telegram import capture_agent


class _FakeToolCallFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, id_, name, arguments):
        self.id = id_
        self.function = _FakeToolCallFunction(name, arguments)


class _FakeMessage:
    def __init__(self, tool_calls=None, content=None):
        self.tool_calls = tool_calls
        self.content = content


class _FakeChoice:
    def __init__(self, message):
        self.message = message


class _FakeCompletion:
    def __init__(self, message):
        self.choices = [_FakeChoice(message)]


class _FakeCompletions:
    """Returns queued messages in order, one per ``create`` call."""

    def __init__(self, messages):
        self._messages = list(messages)

    async def create(self, **kwargs):
        return _FakeCompletion(self._messages.pop(0))


class _FakeChat:
    def __init__(self, messages):
        self.completions = _FakeCompletions(messages)


def _make_fake_async_openai(messages):
    class _FakeAsyncOpenAI:
        def __init__(self, *args, **kwargs):
            self.chat = _FakeChat(messages)

    return _FakeAsyncOpenAI


def _tool_call_message(name, arguments: dict, call_id: str = "call_1") -> _FakeMessage:
    return _FakeMessage(tool_calls=[_FakeToolCall(call_id, name, json.dumps(arguments))])


def _patch_common(monkeypatch, *, namespace="org_acme", org_id=1, memory_add=None, memory_search=None):
    monkeypatch.setattr(
        capture_agent,
        "_resolve_llm",
        lambda: {"family": "openai", "model": "gpt-4o-mini", "api_key": "TOKEN"},
    )

    async def _fake_get_namespace(resolved_org_id):
        assert resolved_org_id == org_id
        return namespace

    monkeypatch.setattr(capture_agent, "get_namespace_for_org", _fake_get_namespace)

    if memory_add is not None:
        monkeypatch.setattr(capture_agent, "memory_add", memory_add)
    if memory_search is not None:
        monkeypatch.setattr(capture_agent, "memory_search", memory_search)


def test_run_capture_calls_memory_add_with_expected_metadata(monkeypatch):
    message = _tool_call_message(
        "memory_add",
        {
            "content": "Acme asked to move the deadline to Friday.",
            "client": "Acme",
            "type": "deadline",
            "summary": "Acme deadline moved to Friday",
        },
    )
    monkeypatch.setattr(openai, "AsyncOpenAI", _make_fake_async_openai([message]))

    add_calls = []

    async def _fake_memory_add(content, metadata=None, user_id=None):
        add_calls.append({"content": content, "metadata": metadata, "user_id": user_id})
        return {"status": "ok", "memory": {"results": [{"id": "mem-123"}]}}

    async def _fake_memory_search(query, limit=10, user_id=None):
        raise AssertionError("memory_search should not be called in this scenario")

    _patch_common(monkeypatch, memory_add=_fake_memory_add, memory_search=_fake_memory_search)

    source_meta = {"telegram_message_id": 42, "ts": "2026-07-11T00:00:00Z"}
    result = asyncio.run(
        capture_agent.run_capture(
            "Acme asked to move the deadline to Friday.", org_id=1, source_meta=source_meta
        )
    )

    assert len(add_calls) == 1
    call = add_calls[0]
    assert call["content"] == "Acme asked to move the deadline to Friday."
    assert call["user_id"] == "org_acme"
    assert call["metadata"] == {
        "source": "telegram",
        "client": "Acme",
        "type": "deadline",
        "telegram_message_id": 42,
        "ts": "2026-07-11T00:00:00Z",
    }
    assert result == {
        "client": "Acme",
        "type": "deadline",
        "summary": "Acme deadline moved to Friday",
        "memory_id": "mem-123",
    }


def test_run_capture_searches_memory_before_adding(monkeypatch):
    search_message = _tool_call_message("memory_search", {"query": "Acme"}, call_id="call_1")
    add_message = _tool_call_message(
        "memory_add",
        {
            "content": "Follow-up note about Acme.",
            "client": "Acme",
            "type": "note",
            "summary": "Follow-up note about Acme",
        },
        call_id="call_2",
    )
    monkeypatch.setattr(openai, "AsyncOpenAI", _make_fake_async_openai([search_message, add_message]))

    search_calls = []
    add_calls = []

    async def _fake_memory_search(query, limit=10, user_id=None):
        search_calls.append({"query": query, "user_id": user_id})
        return {"status": "ok", "memories": [], "count": 0}

    async def _fake_memory_add(content, metadata=None, user_id=None):
        add_calls.append({"content": content, "metadata": metadata, "user_id": user_id})
        return {"status": "ok", "memory": {"results": [{"id": "mem-456"}]}}

    _patch_common(monkeypatch, memory_add=_fake_memory_add, memory_search=_fake_memory_search)

    result = asyncio.run(
        capture_agent.run_capture("Follow-up note about Acme.", org_id=1, source_meta={})
    )

    assert len(search_calls) == 1
    assert search_calls[0]["user_id"] == "org_acme"
    assert len(add_calls) == 1
    assert add_calls[0]["metadata"]["client"] == "Acme"
    assert result["memory_id"] == "mem-456"


def test_run_capture_omits_client_metadata_when_not_mentioned(monkeypatch):
    message = _tool_call_message(
        "memory_add",
        {
            "content": "Remember to renew the domain next month.",
            "type": "note",
            "summary": "Renew domain next month",
        },
    )
    monkeypatch.setattr(openai, "AsyncOpenAI", _make_fake_async_openai([message]))

    add_calls = []

    async def _fake_memory_add(content, metadata=None, user_id=None):
        add_calls.append({"content": content, "metadata": metadata, "user_id": user_id})
        return {"status": "ok", "memory": {"results": [{"id": "mem-789"}]}}

    _patch_common(monkeypatch, memory_add=_fake_memory_add)

    result = asyncio.run(
        capture_agent.run_capture(
            "Remember to renew the domain next month.", org_id=1, source_meta={}
        )
    )

    assert add_calls[0]["metadata"]["client"] is None
    assert result["client"] is None
    assert result["memory_id"] == "mem-789"
