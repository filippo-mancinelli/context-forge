"""Unit tests for the Telegram Bot API client wrapper (src/telegram/client.py).

No real network calls: httpx.AsyncClient is monkeypatched with a fake that
records the request and returns a canned response.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from src.telegram import client


class _FakeResponse:
    def __init__(self, status_code: int, json_body: dict, content: bytes = b""):
        self.status_code = status_code
        self._json_body = json_body
        self.text = str(json_body)
        self.content = content

    def json(self):
        return self._json_body


class _FakeAsyncClient:
    last_call: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None):
        _FakeAsyncClient.last_call = {"method": "post", "url": url, "json": json}
        return _FakeResponse(200, {"ok": True, "result": {"message_id": 1}})

    async def get(self, url):
        _FakeAsyncClient.last_call = {"method": "get", "url": url}
        return _FakeResponse(200, {}, content=b"file-bytes")


def test_send_message_composes_url_and_payload(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    result = asyncio.run(client.send_message(chat_id=123, text="hello", bot_token="TOKEN"))

    assert _FakeAsyncClient.last_call["url"] == "https://api.telegram.org/botTOKEN/sendMessage"
    assert _FakeAsyncClient.last_call["json"] == {"chat_id": 123, "text": "hello"}
    assert result == {"ok": True, "result": {"message_id": 1}}


def test_send_message_raises_on_non_200(monkeypatch):
    class _FailingAsyncClient(_FakeAsyncClient):
        async def post(self, url, json=None):
            return _FakeResponse(401, {"ok": False, "description": "Unauthorized"})

    monkeypatch.setattr(httpx, "AsyncClient", _FailingAsyncClient)

    with pytest.raises(client.TelegramAPIError):
        asyncio.run(client.send_message(chat_id=123, text="hello", bot_token="BAD"))
