"""The agent loop.

A manual tool-calling loop over Groq's OpenAI-compatible chat API. Handlers
are async, share a DB session and user context, and run concurrently within a
turn — so the loop is written by hand rather than delegated to a helper.

Message shape (OpenAI style):
    system    persona + everything Atlas knows about this user
    user/assistant ... conversation history
    assistant with tool_calls  →  role:"tool" results  →  repeat
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import prompts
from app.ai.client import RateLimited, chat, message_text
from app.ai.tools import REGISTRY, TOOL_SCHEMAS, ToolContext
from app.config import settings
from app.db import repo
from app.db.models import User
from app.memory import service as memory

log = logging.getLogger(__name__)

# Each iteration is a full model call, and free-tier quota is per minute —
# so this is deliberately tighter than it would be on a paid tier.
MAX_ITERATIONS = 3
# Replies target ~100 words. A tight ceiling is a latency lever as much as a
# style one: generation time scales with tokens produced.
MAX_TOKENS = 850
TOOL_TIMEOUT = 25.0

# Tool results are fed straight back into the context window, and the free
# tier's binding limit is 8,000 tokens per MINUTE — not requests. Every token
# spent on an oversized tool payload is one unavailable to the next turn, so
# results are trimmed hard before they re-enter the prompt.
MAX_TOOL_RESULT_CHARS = 2200

FALLBACK_REPLY = (
    "I hit a problem reaching my data sources just then. Try me again in a moment "
    "— and if it keeps happening, tell me and I'll work around it."
)

BUSY_REPLY = (
    "I'm being rate-limited by my model provider right now — free tier, and I've "
    "been busy. Give me a minute and ask again."
)


def _truncate(payload: str) -> str:
    if len(payload) <= MAX_TOOL_RESULT_CHARS:
        return payload
    return (
        payload[:MAX_TOOL_RESULT_CHARS]
        + f"\n\n[truncated — {len(payload) - MAX_TOOL_RESULT_CHARS} more characters]"
    )


async def _run_tool(ctx: ToolContext, name: str, raw_args: str) -> str:
    """Execute one tool and return its result as a JSON string."""
    tool = REGISTRY.get(name)
    if not tool:
        return json.dumps({"error": f"Unknown tool '{name}'."})

    try:
        args = json.loads(raw_args) if raw_args else {}
        if not isinstance(args, dict):
            args = {}
    except json.JSONDecodeError:
        # Smaller models occasionally emit malformed argument JSON; telling
        # the model plainly is better than failing the whole turn.
        return json.dumps({"error": f"Arguments for {name} were not valid JSON."})

    started = time.monotonic()
    try:
        result = await asyncio.wait_for(tool.handler(ctx, args), timeout=TOOL_TIMEOUT)
        log.info("tool %s ok in %.2fs", name, time.monotonic() - started)
        return _truncate(json.dumps(result, default=str))
    except asyncio.TimeoutError:
        log.warning("tool %s timed out", name)
        return json.dumps(
            {"error": f"{name} timed out. Tell the user you could not retrieve this."}
        )
    except Exception as exc:  # noqa: BLE001 — a bad tool must not kill the turn
        log.exception("tool %s failed", name)
        return json.dumps({"error": f"{name} failed: {exc}"})


def _assistant_turn(message) -> dict:
    """Echo the model's tool-call turn back in the shape the API expects."""
    return {
        "role": "assistant",
        "content": message.content or "",
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                },
            }
            for call in message.tool_calls
        ],
    }


async def _loop(
    ctx: ToolContext,
    messages: list[dict],
    *,
    max_tokens: int,
    model: str | None = None,
) -> str | None:
    """Drive tool calls until the model answers. Returns final text."""
    final = ""

    for iteration in range(MAX_ITERATIONS):
        # On the final pass, withhold the tools. The model then has to answer
        # with what it already gathered instead of requesting more — which
        # saves a whole extra round trip against an 8k tokens/minute budget.
        last_pass = iteration == MAX_ITERATIONS - 1
        try:
            message = await chat(
                model=model or settings.atlas_model,
                messages=messages,
                tools=None if last_pass else TOOL_SCHEMAS,
                max_tokens=max_tokens,
            )
        except RateLimited:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Groq call failed")
            return None

        text = message_text(message)
        if text:
            final = text

        tool_calls = getattr(message, "tool_calls", None)
        if not tool_calls:
            break

        messages.append(_assistant_turn(message))

        # Independent calls run concurrently — the model routinely asks for a
        # quote and the news for the same name in one turn.
        results = await asyncio.gather(
            *[
                _run_tool(ctx, call.function.name, call.function.arguments)
                for call in tool_calls
            ]
        )
        for call, payload in zip(tool_calls, results):
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.function.name,
                    "content": payload,
                }
            )

    return final or None


async def run_turn(
    db: AsyncSession,
    user: User,
    user_text: str,
) -> str:
    """Run one full conversational turn and return Atlas's reply."""
    ctx = ToolContext(db=db, user=user)

    history = await memory.load_history(db, user)
    context_block = await memory.build_context(db, user)

    messages: list[dict] = [
        {"role": "system", "content": f"{prompts.ATLAS_PERSONA}\n\n{context_block}"},
        *history,
        {"role": "user", "content": user_text},
    ]

    try:
        reply = await _loop(ctx, messages, max_tokens=MAX_TOKENS)
    except RateLimited:
        return BUSY_REPLY

    if ctx.touched_tickers:
        await repo.touch_watchlist_discussion(db, user.id, list(ctx.touched_tickers))

    return reply or FALLBACK_REPLY


async def generate(
    *,
    system_extra: str,
    user_prompt: str,
    db: AsyncSession,
    user: User,
    max_tokens: int = 1600,
) -> str | None:
    """One-shot generation with tools, used by the proactive jobs.

    Shares the persona so briefings sound like the same analyst the user talks
    to, rather than a different system writing in Atlas's name.
    """
    ctx = ToolContext(db=db, user=user)
    messages: list[dict] = [
        {"role": "system", "content": f"{prompts.ATLAS_PERSONA}\n\n{system_extra}"},
        {"role": "user", "content": user_prompt},
    ]

    try:
        return await _loop(ctx, messages, max_tokens=max_tokens)
    except RateLimited:
        # A background job must never spend the quota a live user needs.
        log.warning("Skipping proactive generation — rate limited")
        return None
