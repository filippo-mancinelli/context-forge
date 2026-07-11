"""Thin async wrapper over the Telegram Bot API.

Used by the webhook route (``api/routes/telegram.py``) and the one-off
``scripts/setup_telegram_webhook.py``. The bot token is always passed in by
the caller rather than read from settings here, so this module stays
testable without importing global config.
"""
from __future__ import annotations

import httpx

_API_BASE = "https://api.telegram.org"
_TIMEOUT_SECONDS = 30.0


class TelegramAPIError(RuntimeError):
    """Raised when a Telegram Bot API call returns a non-2xx response."""

    def __init__(self, method: str, status_code: int, body: str):
        super().__init__(f"Telegram API {method} failed: {status_code} {body}")
        self.method = method
        self.status_code = status_code
        self.body = body


async def _call(method: str, bot_token: str, payload: dict) -> dict:
    url = f"{_API_BASE}/bot{bot_token}/{method}"
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as http_client:
        response = await http_client.post(url, json=payload)
    if response.status_code != 200:
        raise TelegramAPIError(method, response.status_code, response.text)
    return response.json()


async def send_message(chat_id: int, text: str, bot_token: str) -> dict:
    """POST sendMessage; returns the parsed Telegram API response."""
    return await _call("sendMessage", bot_token, {"chat_id": chat_id, "text": text})


async def get_file_path(file_id: str, bot_token: str) -> str:
    """Resolve a Telegram ``file_id`` to its download ``file_path`` via getFile."""
    result = await _call("getFile", bot_token, {"file_id": file_id})
    return result["result"]["file_path"]


async def download_file(file_path: str, bot_token: str) -> bytes:
    """Download file content given the ``file_path`` returned by ``get_file_path``."""
    url = f"{_API_BASE}/file/bot{bot_token}/{file_path}"
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as http_client:
        response = await http_client.get(url)
    if response.status_code != 200:
        raise TelegramAPIError("downloadFile", response.status_code, response.text)
    return response.content


async def set_webhook(url: str, secret_token: str, bot_token: str) -> dict:
    """Register the webhook URL and secret token with Telegram."""
    return await _call("setWebhook", bot_token, {"url": url, "secret_token": secret_token})


async def get_webhook_info(bot_token: str) -> dict:
    """Return current webhook status from Telegram."""
    return await _call("getWebhookInfo", bot_token, {})
