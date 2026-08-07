"""Register (or inspect) the Telegram webhook by hand.

Atlas registers its own webhook at startup, so this is not normally needed.
It exists for the case where the deployed service cannot determine its own
public URL — the bot then runs healthily but never receives a message, which
looks identical to being offline.

The bot token is read from `.env`; the webhook secret is passed on the command
line so it is never written to a file that could be committed.

    python scripts/set_webhook.py --check
    python scripts/set_webhook.py https://your-app.onrender.com <SECRET>
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app.config import sanitize_webhook_secret, settings  # noqa: E402

API = "https://api.telegram.org"


async def check() -> None:
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{API}/bot{settings.telegram_bot_token}/getWebhookInfo")
        info = r.json().get("result", {})

    print("Current webhook")
    print("  url             :", info.get("url") or "*** NOT SET ***")
    print("  pending updates :", info.get("pending_update_count", 0))
    print("  last error      :", info.get("last_error_message") or "(none)")
    if not info.get("url"):
        print("\nNo webhook registered — Telegram has nowhere to deliver messages.")
        print("Fix: python scripts/set_webhook.py <BASE_URL> <SECRET>")


async def register(base_url: str, secret: str) -> None:
    base_url = base_url.strip().rstrip("/")
    if not base_url.startswith("https://"):
        sys.exit("Base URL must start with https:// — Telegram requires TLS.")

    webhook_url = f"{base_url}/telegram/webhook"

    # Telegram allows only A-Z a-z 0-9 _ - in the secret. Apply the same
    # normalisation the running app uses, so both sides agree on the value.
    clean = sanitize_webhook_secret(secret)
    if clean != secret.strip():
        print(f"Secret normalised for Telegram ({len(clean)} usable chars).")

    async with httpx.AsyncClient(timeout=30.0) as client:
        health = await client.get(f"{base_url}/health")
        if health.status_code != 200:
            sys.exit(f"{base_url}/health returned {health.status_code} — is it live?")
        print(f"Service healthy: {health.json()}")

        r = await client.post(
            f"{API}/bot{settings.telegram_bot_token}/setWebhook",
            json={
                "url": webhook_url,
                "secret_token": clean,
                "allowed_updates": ["message", "edited_message"],
                "drop_pending_updates": True,
                "max_connections": 40,
            },
        )
        data = r.json()

    if not data.get("ok"):
        sys.exit(f"Telegram rejected it: {data.get('description')}")

    print(f"Webhook registered: {webhook_url}")
    print("\nMessage your bot on Telegram now — it should reply.")


def main() -> None:
    args = [a for a in sys.argv[1:] if a]

    if not settings.telegram_bot_token:
        sys.exit("TELEGRAM_BOT_TOKEN missing from .env")

    if not args or args[0] in ("--check", "-c"):
        asyncio.run(check())
        return

    if len(args) != 2:
        sys.exit(
            "Usage:\n"
            "  python scripts/set_webhook.py --check\n"
            "  python scripts/set_webhook.py <BASE_URL> <SECRET>"
        )

    asyncio.run(register(args[0], args[1]))


if __name__ == "__main__":
    main()
