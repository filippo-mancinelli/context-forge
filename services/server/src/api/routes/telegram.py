"""Webhook endpoint for the Telegram quick-capture channel.

Telegram POSTs here (registered via ``scripts/setup_telegram_webhook.py``) on
every message sent to the bot. Text is fed straight to the capture agent;
photos/documents are routed through the existing knowledge-base pipeline
(``kb.store``, incl. OCR) and the extracted text is then fed to the capture
agent too. See ``.omc/plans/telegram-quick-capture.md`` §3 for the full design.

Authentication mirrors ``api/routes/webhooks.py``'s shared-secret pattern, but
uses Telegram's native mechanism: a ``secret_token`` set via ``setWebhook`` and
verified against the ``X-Telegram-Bot-Api-Secret-Token`` header. The endpoint
is disabled (503) when the channel isn't configured, exempt from the
session-auth guard (see ``api/app.py`` open_paths), and additionally requires
the message's ``chat_id`` to be allowlisted before any action is taken.

Telegram retries the webhook if it doesn't get a fast response, so the route
replies 200 immediately and does the LLM/storage work in a background task,
sending the confirmation message back via ``sendMessage`` once it's done.
"""
from __future__ import annotations

import hmac
import logging
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from ...config import get_settings
from ...db import get_pool
from ...kb import store
from ...kb.extract import is_supported
from ...telegram import client
from ...telegram.capture_agent import run_capture
from ..deps import ActiveOrg, require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telegram", tags=["telegram"])


class RegisterWebhookRequest(BaseModel):
    url: str


def _allowed_chat_ids(raw: str) -> set[int]:
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            logger.warning("Ignoring non-integer entry in TELEGRAM_ALLOWED_CHAT_IDS: %r", part)
    return ids


def _confirmation_text(result: dict[str, Any], *, is_attachment: bool = False) -> str:
    client_part = result.get("client") or "—"
    type_part = result.get("type") or "note"
    summary_part = result.get("summary") or ""
    prefix = "Documento salvato in knowledge base" if is_attachment else "Salvato"
    return f"{prefix}: cliente {client_part}, tag: {type_part}. {summary_part}".strip()


async def _confirm(chat_id: int, bot_token: str, text: str) -> None:
    try:
        await client.send_message(chat_id, text, bot_token)
    except Exception as e:  # noqa: BLE001
        logger.error("Telegram confirmation send failed (chat=%s): %s", chat_id, e)


async def _document_text(doc_id: int) -> str:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT content FROM kb_chunks WHERE document_id=$1 ORDER BY chunk_index", doc_id
        )
    return "\n".join(r["content"] for r in rows)


async def _handle_text(bot_token: str, org_id: int, chat_id: int, text: str, message_id: int) -> None:
    source_meta = {"telegram_message_id": message_id, "telegram_chat_id": chat_id}
    try:
        result = await run_capture(text, org_id, source_meta)
        await _confirm(chat_id, bot_token, _confirmation_text(result))
    except Exception as e:  # noqa: BLE001
        logger.error("Telegram capture failed (chat=%s): %s", chat_id, e)
        await _confirm(chat_id, bot_token, "Errore durante il salvataggio del messaggio.")


async def _handle_attachment(
    bot_token: str, org_id: int, chat_id: int, file_id: str, filename: str, message_id: int
) -> None:
    try:
        if not is_supported(filename):
            await _confirm(chat_id, bot_token, f"Formato allegato non supportato: {filename}")
            return

        file_path = await client.get_file_path(file_id, bot_token)
        data = await client.download_file(file_path, bot_token)

        record = await store.save_upload(org_id, filename, data)
        doc_id = record["id"]
        # Awaited directly (rather than scheduled as its own BackgroundTasks
        # entry like knowledge.py does) because the extracted text is needed
        # right after, to feed the capture agent and compose the confirmation.
        handled = await store.process_document(doc_id)

        extracted_text = await _document_text(doc_id) if handled else ""
        if not extracted_text.strip():
            await _confirm(chat_id, bot_token, "Impossibile estrarre testo dall'allegato.")
            return

        source_meta = {
            "telegram_message_id": message_id,
            "telegram_chat_id": chat_id,
            "kb_document_id": doc_id,
        }
        result = await run_capture(extracted_text, org_id, source_meta)
        await _confirm(chat_id, bot_token, _confirmation_text(result, is_attachment=True))
    except Exception as e:  # noqa: BLE001
        logger.error("Telegram attachment handling failed (chat=%s): %s", chat_id, e)
        await _confirm(chat_id, bot_token, "Errore durante il salvataggio dell'allegato.")


def _extract_message(update: dict[str, Any]) -> Optional[dict[str, Any]]:
    return update.get("message") or update.get("edited_message")


@router.get("/webhook-info")
async def get_webhook_info(org: ActiveOrg = Depends(require_role("admin"))):
    """Return the current webhook URL registered with Telegram, if any."""
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise HTTPException(status_code=400, detail="Bot token not configured")
    try:
        result = await client.get_webhook_info(settings.telegram_bot_token)
    except client.TelegramAPIError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=f"Telegram getWebhookInfo failed: {result}")
    info = result.get("result", {})
    return {"status": "ok", "url": info.get("url", ""), "has_custom_certificate": info.get("has_custom_certificate", False), "pending_update_count": info.get("pending_update_count", 0), "last_error_date": info.get("last_error_date"), "last_error_message": info.get("last_error_message")}


@router.post("/register-webhook")
async def register_webhook(
    req: RegisterWebhookRequest, org: ActiveOrg = Depends(require_role("admin"))
):
    """Register the given URL as the Telegram webhook, using the configured bot token/secret.

    Admin-only: this calls out to Telegram with the bot's credentials, equivalent to running
    ``scripts/setup_telegram_webhook.py`` by hand. Requires TELEGRAM_BOT_TOKEN and
    TELEGRAM_WEBHOOK_SECRET to already be saved (via ``PUT /settings``) before calling.
    """
    settings = get_settings()
    if not settings.telegram_bot_token or not settings.telegram_webhook_secret:
        raise HTTPException(
            status_code=400,
            detail="Save a bot token and webhook secret first, then register the webhook.",
        )
    try:
        result = await client.set_webhook(req.url, settings.telegram_webhook_secret, settings.telegram_bot_token)
    except client.TelegramAPIError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=f"Telegram setWebhook failed: {result}")
    return {"status": "ok", "result": result}


@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_telegram_bot_api_secret_token: Optional[str] = Header(None),
):
    """Receive a Telegram update, verify it, and dispatch capture in the background."""
    settings = get_settings()
    secret = (settings.telegram_webhook_secret or "").strip()
    if not secret or not settings.telegram_bot_token:
        raise HTTPException(
            status_code=503,
            detail=(
                "Telegram channel is disabled (set TELEGRAM_BOT_TOKEN "
                "and TELEGRAM_WEBHOOK_SECRET)"
            ),
        )

    if not x_telegram_bot_api_secret_token or not hmac.compare_digest(
        secret, x_telegram_bot_api_secret_token
    ):
        raise HTTPException(status_code=401, detail="Invalid or missing Telegram secret token")

    update = await request.json()
    message = _extract_message(update)
    if not message:
        return {"status": "ignored", "reason": "no message in update"}

    chat_id = (message.get("chat") or {}).get("id")
    if chat_id is None:
        return {"status": "ignored", "reason": "no chat id"}

    allowed = _allowed_chat_ids(settings.telegram_allowed_chat_ids)
    if chat_id not in allowed:
        logger.info("Telegram message from non-allowlisted chat_id=%s ignored", chat_id)
        return {"status": "ignored", "reason": "chat_id not in allowlist"}

    bot_token = settings.telegram_bot_token
    org_id = settings.telegram_org_id
    message_id = message.get("message_id", 0)

    document = message.get("document")
    photos = message.get("photo")
    text = message.get("text") or message.get("caption")

    if document:
        filename = document.get("file_name") or f"telegram_{message_id}"
        background_tasks.add_task(
            _handle_attachment, bot_token, org_id, chat_id, document["file_id"], filename, message_id
        )
    elif photos:
        # Telegram sends multiple resolutions per photo; the last is the largest.
        largest = photos[-1]
        filename = f"telegram_photo_{message_id}.jpg"
        background_tasks.add_task(
            _handle_attachment, bot_token, org_id, chat_id, largest["file_id"], filename, message_id
        )
    elif text:
        background_tasks.add_task(_handle_text, bot_token, org_id, chat_id, text, message_id)
    else:
        return {"status": "ignored", "reason": "unsupported message type"}

    return {"status": "accepted"}
