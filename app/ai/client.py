"""Groq client wrapper.

Everything Atlas needs is on Groq's free tier, reached through one key:

  chat + tool calling   gpt-oss-120b          (Groq)
  cheap background work gpt-oss-20b           (Groq)
  speech-to-text        whisper-large-v3      (Groq)
  vision                llama-3.2-11b-vision  (OpenRouter — Groq has none)

Model choice was measured, not assumed: llama-3.3-70b-versatile returned
`tool_use_failed` on the simplest query against this project's 17-tool set,
emitting Llama-style function syntax the API rejects. gpt-oss-120b passed
every case. No vision model is currently offered on the free tier, so image
support reports that honestly rather than guessing at a chart.

The free tier is rate-limited per minute rather than billed, so the shape of
the problem is throughput, not cost. Two mechanisms handle that: a global
semaphore so a background sweep can never starve a live conversation, and
backoff that honours the server's `retry-after`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any

import groq
from groq import AsyncGroq

from app.config import settings

log = logging.getLogger(__name__)

_client: AsyncGroq | None = None

# Caps in-flight Groq calls process-wide. A briefing sweep fans out across
# users; without this it would burn the per-minute quota and 429 the person
# actually typing.
_gate = asyncio.Semaphore(settings.max_concurrent_llm_calls)

MAX_ATTEMPTS = 4


def get_client() -> AsyncGroq:
    global _client
    if _client is None:
        if not settings.groq_api_key:
            raise RuntimeError(
                "GROQ_API_KEY is not set — Atlas cannot reason without it."
            )
        _client = AsyncGroq(api_key=settings.groq_api_key, max_retries=0, timeout=90.0)
    return _client


class RateLimited(RuntimeError):
    """Raised when Groq's free-tier quota is exhausted after retries."""


def _retry_after(exc: Exception, attempt: int) -> float:
    """Honour the server's retry hint, falling back to jittered backoff."""
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    for key in ("retry-after", "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens"):
        raw = headers.get(key)
        if not raw:
            continue
        try:
            # Groq returns either seconds or a duration like "7.66s"/"2m59s".
            text = str(raw).strip()
            if text.endswith("s") and "m" not in text:
                return min(float(text[:-1]), 30.0)
            return min(float(text), 30.0)
        except ValueError:
            continue
    return min(2**attempt + random.uniform(0, 1), 30.0)


async def _call(**kwargs: Any) -> Any:
    """One chat completion, with concurrency gating and 429 backoff."""
    last: Exception | None = None

    for attempt in range(MAX_ATTEMPTS):
        try:
            async with _gate:
                return await get_client().chat.completions.create(**kwargs)
        except groq.RateLimitError as exc:
            last = exc
            delay = _retry_after(exc, attempt)
            log.warning(
                "Groq rate limited (attempt %d/%d) — waiting %.1fs",
                attempt + 1,
                MAX_ATTEMPTS,
                delay,
            )
            await asyncio.sleep(delay)
        except (groq.APIConnectionError, groq.InternalServerError) as exc:
            last = exc
            await asyncio.sleep(min(2**attempt, 8))
        except groq.BadRequestError:
            raise  # a malformed request will never succeed on retry
        except Exception as exc:  # noqa: BLE001
            last = exc
            log.warning("Groq call failed: %s", exc)
            break

    if isinstance(last, groq.RateLimitError):
        raise RateLimited("Groq free-tier quota exhausted") from last
    raise last if last else RuntimeError("Groq call failed")


async def chat(
    *,
    model: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    max_tokens: int = 2048,
    temperature: float = 0.4,
    response_format: dict | None = None,
) -> Any:
    """Chat completion. Returns the raw Groq message object."""
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    if response_format:
        kwargs["response_format"] = response_format

    try:
        response = await _call(**kwargs)
    except groq.BadRequestError as exc:
        # `tool_use_failed`: the model emitted a function call in the wrong
        # syntax and Groq's parser rejected it with a 400. It is recoverable —
        # one nudge to use the proper format almost always fixes it — and
        # letting it through would abort an otherwise healthy turn.
        if tools and "tool_use_failed" in str(exc):
            log.warning("Malformed tool call; retrying with a format nudge")
            nudged = [
                *messages,
                {
                    "role": "system",
                    "content": (
                        "Your last tool call was malformed. Emit tool calls "
                        "using the standard function-calling format only — "
                        "never write function syntax inside your message text. "
                        "If you cannot, answer in plain language instead."
                    ),
                },
            ]
            response = await _call(**{**kwargs, "messages": nudged})
        else:
            raise

    return response.choices[0].message


def message_text(message: Any) -> str:
    return (getattr(message, "content", None) or "").strip()


# =============================================================================
# Background helpers — must never take the conversation down with them
# =============================================================================


def _parse_json(text: str) -> dict | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Smaller models sometimes wrap JSON in prose or a fence; recover it.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    log.warning("Unparseable JSON from model: %.200s", text)
    return None


async def quick_json(
    *, system: str, user: str, max_tokens: int = 1200
) -> dict | None:
    """Cheap structured extraction on the fast model.

    Uses Groq's JSON mode, which guarantees syntactically valid JSON but not
    adherence to a schema — so every caller still validates the fields it
    reads rather than trusting the shape.
    """
    try:
        message = await chat(
            model=settings.atlas_fast_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        return _parse_json(message_text(message))
    except Exception as exc:  # noqa: BLE001
        log.warning("quick_json failed: %s", exc)
        return None


async def quick_text(*, system: str, user: str, max_tokens: int = 900) -> str | None:
    """Cheap prose generation on the fast model (summaries, orientation lines)."""
    try:
        message = await chat(
            model=settings.atlas_fast_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=0.3,
        )
        return message_text(message) or None
    except Exception as exc:  # noqa: BLE001
        log.warning("quick_text failed: %s", exc)
        return None


# =============================================================================
# Voice and vision — same key, no extra provider
# =============================================================================


async def transcribe_audio(audio: bytes, filename: str = "voice.ogg") -> str | None:
    """Whisper transcription on Groq."""
    if not settings.voice_enabled:
        return None
    try:
        async with _gate:
            result = await get_client().audio.transcriptions.create(
                file=(filename, audio),
                model=settings.atlas_whisper_model,
                # Steering the decoder toward finance vocabulary measurably
                # reduces ticker and jargon errors ("NVDA" not "in video").
                prompt=(
                    "Financial discussion. Expect stock tickers (NVDA, AAPL, "
                    "MSFT, TSLA, GOOGL, AMZN) and terms like earnings, EBITDA, "
                    "guidance, margins, valuation, P/E, basis points, 10-K."
                ),
                response_format="text",
            )
        text = (result if isinstance(result, str) else getattr(result, "text", "")) or ""
        return text.strip() or None
    except Exception as exc:  # noqa: BLE001
        log.warning("Transcription failed: %s", exc)
        return None


# A phone screenshot is often 1200-1500px wide and a megabyte or more. Sent
# raw, base64 inflates it by a third, the upload dominates the request, and
# the call can take a minute or simply time out. Vision models gain nothing
# from that resolution for reading a table, so it is downscaled first — this
# is the difference between images feeling broken and feeling instant.
VISION_MAX_EDGE = 1100
VISION_QUALITY = 78


def _shrink_for_vision(data: bytes) -> bytes:
    try:
        from PIL import Image

        import io as _io

        img = Image.open(_io.BytesIO(data))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        longest = max(img.size)
        if longest > VISION_MAX_EDGE:
            scale = VISION_MAX_EDGE / longest
            img = img.resize(
                (int(img.width * scale), int(img.height * scale)),
                Image.LANCZOS,
            )

        buf = _io.BytesIO()
        img.save(buf, format="JPEG", quality=VISION_QUALITY, optimize=True)
        shrunk = buf.getvalue()
        return shrunk if len(shrunk) < len(data) else data
    except Exception as exc:  # noqa: BLE001 — never block on the optimisation
        log.warning("Image downscale skipped: %s", exc)
        return data


async def read_image(image: bytes, question: str) -> str | None:
    """Transcribe a chart, table or screenshot into text.

    Runs on OpenRouter rather than Groq, whose free tier has no vision model.
    The reading then enters the normal turn as text, so the analysis still
    happens on the stronger reasoning model with full tool access — better
    than trading tool calling away for multimodality.

    The prompt asks for transcription, not interpretation: the vision model
    reports what is on screen, and Atlas verifies any figure it relies on
    against live data before quoting it.
    """
    if not settings.vision_enabled:
        return None

    import base64

    from app.data import http as http_pool

    image = _shrink_for_vision(image)
    encoded = base64.standard_b64encode(image).decode()
    log.info("vision payload %.0f KB", len(encoded) / 1024)
    payload = {
        "model": settings.atlas_vision_model,
        "max_tokens": 900,
        "temperature": 0.1,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                    },
                    {
                        "type": "text",
                        "text": (
                            "Read this image for a financial analyst. Transcribe "
                            "every number, axis label, ticker, date and series "
                            "name exactly as shown. Say what kind of chart or "
                            "table it is. Do not interpret or give opinions - "
                            "report only what is visibly there. If something is "
                            "unreadable, say so rather than guessing.\n\n"
                            f"The user asked: {question}"
                        ),
                    },
                ],
            }
        ],
    }

    try:
        async with _gate:
            client = http_pool.get_client(
                "openrouter",
                timeout=60.0,
                headers={
                    "Authorization": f"Bearer {settings.openrouter_api_key}",
                    "Content-Type": "application/json",
                },
            )
            r = await client.post(
                "https://openrouter.ai/api/v1/chat/completions", json=payload
            )
        if r.status_code != 200:
            log.warning("OpenRouter vision %s: %.200s", r.status_code, r.text)
            return None
        text = (r.json()["choices"][0]["message"].get("content") or "").strip()
        return text or None
    except Exception as exc:  # noqa: BLE001
        log.warning("Image reading failed: %s", exc)
        return None
