"""Integration tests for POST /api/telegram/webhook (src/api/routes/telegram.py).

Exercises the route together with the real capture-agent tool loop
(src/telegram/capture_agent.py), mocking only the true external boundaries:
the Telegram Bot API (client.send_message/get_file_path/download_file), the
LLM (openai.AsyncOpenAI), mem0 (memory_add/memory_search), and the per-org
settings resolution (resolve_org_by_webhook_secret/get_org_settings) — plus
the KB store for the attachment scenario, since there's no test database in
this repo. FastAPI's TestClient runs BackgroundTasks synchronously as part of
sending the response, so side effects can be asserted right after the POST.
"""
from __future__ import annotations

import json

import openai
from fastapi.testclient import TestClient

from src.api.app import api
from src.api.routes import telegram as telegram_route
from src.org_settings import OrgSettings
from src.telegram import capture_agent

WEBHOOK_URL = "/api/telegram/webhook"
SECRET = "test-secret-token"
ALLOWED_CHAT_ID = 123456789


class _FakeToolCallFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, id_, name, arguments):
        self.id = id_
        self.function = _FakeToolCallFunction(name, arguments)


class _FakeMessage:
    def __init__(self, tool_calls):
        self.tool_calls = tool_calls
        self.content = ""


class _FakeChoice:
    def __init__(self, message):
        self.message = message


class _FakeCompletion:
    def __init__(self, message):
        self.choices = [_FakeChoice(message)]


class _FakeCompletions:
    def __init__(self, message):
        self._message = message

    async def create(self, **kwargs):
        return _FakeCompletion(self._message)


class _FakeAsyncOpenAI:
    def __init__(self, *args, **kwargs):
        self.chat = type("Chat", (), {"completions": _FakeCompletions(_MEMORY_ADD_MESSAGE)})()


_MEMORY_ADD_MESSAGE = _FakeMessage(
    tool_calls=[
        _FakeToolCall(
            "call_1",
            "memory_add",
            json.dumps(
                {
                    "content": "Acme ha chiesto di spostare la scadenza a venerdi.",
                    "client": "Acme",
                    "type": "deadline",
                    "summary": "Acme: scadenza spostata a venerdi",
                }
            ),
        )
    ]
)


def _configure_org_settings(monkeypatch, org_id: int = 1, **overrides) -> OrgSettings:
    """Wire the webhook-secret -> org resolution and that org's Telegram settings.

    Patches ``resolve_org_by_webhook_secret``/``get_org_settings`` as used by
    ``api/routes/telegram.py``, standing in for the org_runtime_config lookup
    (no test database in this repo).
    """
    values = {
        "telegram_bot_token": "BOTTOKEN",
        "telegram_webhook_secret": SECRET,
        "telegram_allowed_chat_ids": str(ALLOWED_CHAT_ID),
    }
    values.update(overrides)
    settings = OrgSettings(**values)

    async def _fake_resolve(secret):
        if secret and secret == settings.telegram_webhook_secret:
            return org_id
        return None

    async def _fake_get_org_settings(oid):
        assert oid == org_id
        return settings

    monkeypatch.setattr(telegram_route, "resolve_org_by_webhook_secret", _fake_resolve)
    monkeypatch.setattr(telegram_route, "get_org_settings", _fake_get_org_settings)
    return settings


def _text_update(chat_id: int, text: str, message_id: int = 501) -> dict:
    return {
        "update_id": 100001,
        "message": {
            "message_id": message_id,
            "date": 1752233600,
            "chat": {"id": chat_id, "type": "private", "first_name": "Filippo"},
            "from": {"id": chat_id, "is_bot": False, "first_name": "Filippo"},
            "text": text,
        },
    }


def _document_update(chat_id: int, message_id: int = 502) -> dict:
    return {
        "update_id": 100002,
        "message": {
            "message_id": message_id,
            "date": 1752233700,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": chat_id, "is_bot": False},
            "document": {
                "file_id": "BQACAgEAAxkDfake",
                "file_unique_id": "AgADfake",
                "file_name": "whatsapp_screenshot.jpg",
                "mime_type": "image/jpeg",
                "file_size": 20480,
            },
        },
    }


def _patch_send_message(monkeypatch):
    sent = []

    async def _fake_send_message(chat_id, text, bot_token):
        sent.append({"chat_id": chat_id, "text": text, "bot_token": bot_token})
        return {"ok": True, "result": {"message_id": 1}}

    monkeypatch.setattr(telegram_route.client, "send_message", _fake_send_message)
    return sent


def test_text_message_creates_memory(monkeypatch):
    """1. Realistic text update with a valid secret + allowlisted chat -> memory_add called."""
    _configure_org_settings(monkeypatch)
    sent = _patch_send_message(monkeypatch)

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeAsyncOpenAI)

    async def _fake_get_org_settings(org_id):
        assert org_id == 1
        return OrgSettings()

    monkeypatch.setattr(capture_agent, "get_org_settings", _fake_get_org_settings)
    monkeypatch.setattr(
        capture_agent, "_llm_family", lambda s: {"family": "openai", "model": "gpt-4o-mini", "api_key": "TOKEN"}
    )

    async def _fake_get_namespace(org_id):
        assert org_id == 1
        return "org_default"

    monkeypatch.setattr(capture_agent, "get_namespace_for_org", _fake_get_namespace)

    add_calls = []

    async def _fake_memory_add(content, metadata=None, user_id=None):
        add_calls.append({"content": content, "metadata": metadata, "user_id": user_id})
        return {"status": "ok", "memory": {"results": [{"id": "mem-1"}]}}

    monkeypatch.setattr(capture_agent, "memory_add", _fake_memory_add)

    client = TestClient(api)
    update = _text_update(ALLOWED_CHAT_ID, "Acme ha chiesto di spostare la scadenza a venerdi.")
    resp = client.post(WEBHOOK_URL, json=update, headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})

    assert resp.status_code == 200
    assert resp.json() == {"status": "accepted"}

    assert len(add_calls) == 1
    assert add_calls[0]["metadata"]["source"] == "telegram"
    assert add_calls[0]["metadata"]["client"] == "Acme"
    assert add_calls[0]["metadata"]["type"] == "deadline"
    assert add_calls[0]["metadata"]["telegram_message_id"] == 501
    assert add_calls[0]["metadata"]["telegram_chat_id"] == ALLOWED_CHAT_ID

    # Confirmation was sent back to the same chat once capture finished.
    assert len(sent) == 1
    assert sent[0]["chat_id"] == ALLOWED_CHAT_ID
    assert "Acme" in sent[0]["text"]


def test_missing_or_wrong_secret_returns_403(monkeypatch):
    """2. Missing or incorrect X-Telegram-Bot-Api-Secret-Token -> 403 (no org resolved)."""
    _configure_org_settings(monkeypatch)
    _patch_send_message(monkeypatch)

    client = TestClient(api)
    update = _text_update(ALLOWED_CHAT_ID, "hello")

    resp_missing = client.post(WEBHOOK_URL, json=update)
    assert resp_missing.status_code == 403

    resp_wrong = client.post(
        WEBHOOK_URL, json=update, headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"}
    )
    assert resp_wrong.status_code == 403


def test_non_allowlisted_chat_id_is_ignored(monkeypatch):
    """3. chat_id not in the allowlist -> 200 but no memory_add / kb.store call."""
    _configure_org_settings(monkeypatch)
    sent = _patch_send_message(monkeypatch)

    async def _unexpected_memory_add(*args, **kwargs):
        raise AssertionError("memory_add must not be called for a non-allowlisted chat_id")

    monkeypatch.setattr(capture_agent, "memory_add", _unexpected_memory_add)

    async def _unexpected_save_upload(*args, **kwargs):
        raise AssertionError("kb.store.save_upload must not be called for a non-allowlisted chat_id")

    monkeypatch.setattr(telegram_route.store, "save_upload", _unexpected_save_upload)

    client = TestClient(api)
    other_chat_id = 999999999
    update = _text_update(other_chat_id, "hello")
    resp = client.post(WEBHOOK_URL, json=update, headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})

    assert resp.status_code == 200
    assert resp.json() == {"status": "ignored", "reason": "chat_id not in allowlist"}
    assert sent == []  # no confirmation sent either


def test_document_attachment_saves_to_kb_store(monkeypatch):
    """4. Document attachment -> kb.store.save_upload called with the downloaded bytes."""
    _configure_org_settings(monkeypatch)
    _patch_send_message(monkeypatch)

    async def _fake_get_file_path(file_id, bot_token):
        assert file_id == "BQACAgEAAxkDfake"
        assert bot_token == "BOTTOKEN"
        return "documents/file_1.jpg"

    async def _fake_download_file(file_path, bot_token):
        assert file_path == "documents/file_1.jpg"
        return b"fake-image-bytes"

    monkeypatch.setattr(telegram_route.client, "get_file_path", _fake_get_file_path)
    monkeypatch.setattr(telegram_route.client, "download_file", _fake_download_file)

    async def _fake_default_project_id(org_id):
        assert org_id == 1
        return 7

    import src.projects as projects_mod

    monkeypatch.setattr(projects_mod, "get_default_project_id", _fake_default_project_id)

    save_calls = []

    async def _fake_save_upload(org_id, project_id, filename, data):
        save_calls.append(
            {"org_id": org_id, "project_id": project_id, "filename": filename, "data": data}
        )
        return {"id": 42, "title": "whatsapp_screenshot", "filename": filename, "status": "pending"}

    async def _fake_process_document(doc_id):
        assert doc_id == 42
        return True

    monkeypatch.setattr(telegram_route.store, "save_upload", _fake_save_upload)
    monkeypatch.setattr(telegram_route.store, "process_document", _fake_process_document)
    monkeypatch.setattr(telegram_route, "_document_text", lambda doc_id: _async_text("OCR'd chat excerpt"))

    async def _fake_run_capture(text, org_id, source_meta):
        assert text == "OCR'd chat excerpt"
        assert source_meta["kb_document_id"] == 42
        return {"client": "Acme", "type": "note", "summary": "screenshot saved", "memory_id": "mem-2"}

    monkeypatch.setattr(telegram_route, "run_capture", _fake_run_capture)

    client = TestClient(api)
    update = _document_update(ALLOWED_CHAT_ID)
    resp = client.post(WEBHOOK_URL, json=update, headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})

    assert resp.status_code == 200
    assert resp.json() == {"status": "accepted"}

    assert len(save_calls) == 1
    assert save_calls[0]["org_id"] == 1
    assert save_calls[0]["project_id"] == 7
    assert save_calls[0]["filename"] == "whatsapp_screenshot.jpg"
    assert save_calls[0]["data"] == b"fake-image-bytes"


async def _async_text(text: str) -> str:
    return text
