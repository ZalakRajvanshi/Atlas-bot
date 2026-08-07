"""Telegram Bot API client.

Thin httpx wrapper rather than a bot framework: Atlas receives updates through
FastAPI's webhook endpoint, so a framework's dispatcher and event loop would be
redundant weight.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.config import settings
from app.data import http
from app.telegram.formatting import split_message, to_telegram_html

log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"

# Telegram permits ~30 messages/second overall; proactive fan-out is the only
# place we approach that, so a small global gate is enough.
_send_gate = asyncio.Semaphore(20)


def _url(method: str) -> str:
    return f"{API_BASE}/bot{settings.telegram_bot_token}/{method}"


def _file_url(path: str) -> str:
    return f"{API_BASE}/file/bot{settings.telegram_bot_token}/{path}"


async def _call(method: str, payload: dict[str, Any] | None = None) -> dict | None:
    try:
        client = http.get_client("telegram", timeout=30.0)
        response = await client.post(_url(method), json=payload or {})
        data = response.json()
        if not data.get("ok"):
            log.warning("Telegram %s failed: %s", method, data.get("description"))
            return None
        return data.get("result")
    except Exception as exc:  # noqa: BLE001
        log.warning("Telegram %s error: %s", method, exc)
        return None


# =============================================================================
# Sending
# =============================================================================


async def send_message(chat_id: int, text: str, *, preview: bool = False) -> bool:
    """Send text, converting markdown and splitting when over the length limit."""
    if not text or not text.strip():
        return False

    formatted = to_telegram_html(text)
    ok = True

    async with _send_gate:
        for i, chunk in enumerate(split_message(formatted)):
            if i:
                # Brief pause so multi-part messages arrive in order.
                await asyncio.sleep(0.35)
            result = await _call(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": chunk,
                    "parse_mode": "HTML",
                    "link_preview_options": {"is_disabled": not preview},
                },
            )
            if result is None:
                # Malformed HTML is the usual cause; retry as plain text so the
                # user still gets the content.
                result = await _call(
                    "sendMessage",
                    {
                        "chat_id": chat_id,
                        "text": chunk,
                        "link_preview_options": {"is_disabled": True},
                    },
                )
                ok = ok and result is not None
    return ok


async def send_typing(chat_id: int) -> None:
    await _call("sendChatAction", {"chat_id": chat_id, "action": "typing"})


class TypingIndicator:
    """Keeps the typing indicator alive for the length of a turn.

    Telegram clears it after ~5 seconds, and Atlas's research turns routinely
    run longer than that; without this the bot looks dead mid-thought.
    """

    def __init__(self, chat_id: int):
        self.chat_id = chat_id
        self._task: asyncio.Task | None = None

    async def _loop(self) -> None:
        try:
            while True:
                await send_typing(self.chat_id)
                await asyncio.sleep(4.0)
        except asyncio.CancelledError:
            pass

    async def __aenter__(self) -> "TypingIndicator":
        self._task = asyncio.create_task(self._loop())
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


# =============================================================================
# Files
# =============================================================================


async def download_file(file_id: str, max_bytes: int = 25 * 1024 * 1024) -> bytes | None:
    """Resolve a file_id and download its contents."""
    info = await _call("getFile", {"file_id": file_id})
    if not info or not info.get("file_path"):
        return None

    size = info.get("file_size") or 0
    if size > max_bytes:
        log.warning("File too large: %s bytes", size)
        return None

    try:
        client = http.get_client("telegram-files", timeout=120.0)
        response = await client.get(_file_url(info["file_path"]))
        response.raise_for_status()
        return response.content
    except Exception as exc:  # noqa: BLE001
        log.warning("File download failed: %s", exc)
        return None


# =============================================================================
# Webhook management
# =============================================================================


async def set_webhook() -> bool:
    if not settings.base_url:
        log.warning("PUBLIC_BASE_URL not set — skipping webhook registration.")
        return False

    result = await _call(
        "setWebhook",
        {
            "url": settings.webhook_url,
            "secret_token": settings.webhook_secret,
            "allowed_updates": ["message", "edited_message"],
            # A stale queue after a redeploy would replay old conversations.
            "drop_pending_updates": True,
            "max_connections": 40,
        },
    )
    if result:
        log.info("Webhook registered at %s", settings.webhook_url)
        return True
    return False


async def delete_webhook() -> None:
    await _call("deleteWebhook", {"drop_pending_updates": False})


async def get_me() -> dict | None:
    return await _call("getMe")
