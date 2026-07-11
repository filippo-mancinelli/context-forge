#!/usr/bin/env python3
# One-off script: registers the Telegram webhook against this deployment.
# Run once after deploy (and again whenever the public URL or secret changes):
#   python scripts/setup_telegram_webhook.py https://contextapi.example.com/api/telegram/webhook
# Reads TELEGRAM_BOT_TOKEN / TELEGRAM_WEBHOOK_SECRET from the server's settings (.env).
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "server"))

from src.config import get_settings  # noqa: E402
from src.telegram import client  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser(description="Register the Telegram webhook URL with Telegram.")
    parser.add_argument("url", help="Public webhook URL, e.g. https://host/api/telegram/webhook")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.telegram_bot_token:
        print("[ERROR] TELEGRAM_BOT_TOKEN is not set.")
        sys.exit(1)
    if not settings.telegram_webhook_secret:
        print("[ERROR] TELEGRAM_WEBHOOK_SECRET is not set.")
        sys.exit(1)

    result = await client.set_webhook(args.url, settings.telegram_webhook_secret, settings.telegram_bot_token)
    if not result.get("ok"):
        print(f"[ERROR] setWebhook failed: {result}")
        sys.exit(1)

    print(f"[OK] Webhook registered: {args.url}")


if __name__ == "__main__":
    asyncio.run(main())
