"""Unit tests for the agent-chat streaming truncation fix (src/api/routes/chat.py).

Reproduces the reported bug: a response cut off by max_tokens used to end the
turn silently (broken markdown, no more text, user has to type "continue").
No real LLM calls: anthropic.AsyncAnthropic / openai.AsyncOpenAI are
monkeypatched with fakes that return a truncated round followed by a normal
one.
"""
from __future__ import annotations

import asyncio

from src.api.deps import ActiveOrg
from src.api.routes import chat


# --------------------------------------------------------------------------- #
# Anthropic fakes
# --------------------------------------------------------------------------- #
class _FakeContentBlock:
    def __init__(self, type_: str, text: str | None = None):
        self.type = type_
        self.text = text

    def model_dump(self):
        return {"type": self.type, "text": self.text}


class _FakeFinalMessage:
    def __init__(self, content, stop_reason: str):
        self.content = content
        self.stop_reason = stop_reason


class _FakeDelta:
    def __init__(self, type_: str, text: str | None = None):
        self.type = type_
        self.text = text


class _FakeStreamEvent:
    def __init__(self, delta):
        self.type = "content_block_delta"
        self.delta = delta


class _FakeMessageStream:
    def __init__(self, events, final):
        self._events = events
        self._final = final

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for e in self._events:
            yield e

    async def get_final_message(self):
        return self._final


class _FakeAnthropicMessagesAPI:
    """Returns queued (events, final) rounds in order, one per ``stream`` call."""

    def __init__(self, rounds):
        self._rounds = list(rounds)

    def stream(self, **kwargs):
        events, final = self._rounds.pop(0)
        return _FakeMessageStream(events, final)


def _make_fake_async_anthropic(rounds):
    api = _FakeAnthropicMessagesAPI(rounds)

    class _FakeAsyncAnthropic:
        def __init__(self, *args, **kwargs):
            self.messages = api

    return _FakeAsyncAnthropic


async def _collect(agen):
    return [ev async for ev in agen]


def _org():
    return ActiveOrg(org_id=1, role="member", namespace="org_test", name="Test Org")


def test_stream_anthropic_auto_continues_past_max_tokens(monkeypatch):
    """A max_tokens cutoff with no tool call must resume, not end the turn."""
    round_1 = (
        [_FakeStreamEvent(_FakeDelta("text_delta", "Hello wor"))],
        _FakeFinalMessage([_FakeContentBlock("text", "Hello wor")], stop_reason="max_tokens"),
    )
    round_2 = (
        [_FakeStreamEvent(_FakeDelta("text_delta", "ld!"))],
        _FakeFinalMessage([_FakeContentBlock("text", "ld!")], stop_reason="end_turn"),
    )

    import anthropic

    monkeypatch.setattr(anthropic, "AsyncAnthropic", _make_fake_async_anthropic([round_1, round_2]))

    llm = {"family": "anthropic", "model": "claude-sonnet-4-5", "api_key": "TOKEN"}
    req = chat.ChatRequest(messages=[chat.ChatMessage(role="user", content="say hello world")])

    events = asyncio.run(_collect(chat._stream_anthropic(llm, _org(), req)))

    text_events = [e for e in events if e["type"] == "text"]
    # No spurious "\n\n" spacer between the truncated round and its continuation —
    # the two deltas must concatenate into one seamless word.
    assert "".join(e["delta"] for e in text_events) == "Hello world!"
    assert events[-1]["type"] == "done"


def test_stream_anthropic_still_separates_rounds_after_tool_call(monkeypatch):
    """A genuinely new turn after a tool call still gets its paragraph spacer."""
    tool_use = _FakeContentBlock("tool_use", None)
    tool_use.id = "tu_1"
    tool_use.name = "list_memories"
    tool_use.input = {}

    round_1 = (
        [_FakeStreamEvent(_FakeDelta("text_delta", "Thinking..."))],
        _FakeFinalMessage(
            [_FakeContentBlock("text", "Thinking..."), tool_use], stop_reason="tool_use"
        ),
    )
    round_2 = (
        [_FakeStreamEvent(_FakeDelta("text_delta", "Done."))],
        _FakeFinalMessage([_FakeContentBlock("text", "Done.")], stop_reason="end_turn"),
    )

    import anthropic

    monkeypatch.setattr(anthropic, "AsyncAnthropic", _make_fake_async_anthropic([round_1, round_2]))

    async def _fake_list_memories(org, args):
        return []

    monkeypatch.setitem(chat._TOOL_HANDLERS, "list_memories", (_fake_list_memories, "memory"))

    llm = {"family": "anthropic", "model": "claude-sonnet-4-5", "api_key": "TOKEN"}
    req = chat.ChatRequest(messages=[chat.ChatMessage(role="user", content="what do you remember?")])

    events = asyncio.run(_collect(chat._stream_anthropic(llm, _org(), req)))

    text_events = [e for e in events if e["type"] == "text"]
    # A genuine new turn after a tool call keeps the "\n\n" paragraph spacer.
    assert [e["delta"] for e in text_events] == ["Thinking...", "\n\n", "Done."]
