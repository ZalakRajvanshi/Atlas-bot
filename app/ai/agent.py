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
# Replies target ~120 words. The ceiling is a latency lever as much as a style
# one, and for two reasons. Generation time scales with tokens produced, and
# the free tier's per-minute budget is reserved against max_tokens rather than
# what actually comes back — so an inflated ceiling burns quota on every call
# and buys 429 backoff later in the conversation. 550 leaves roughly four
# times the target length as headroom, which is plenty for a long document
# summary and still well clear of truncating anything real.
MAX_TOKENS = 550
TOOL_TIMEOUT = 25.0

# Tool results are fed straight back into the context window, and the free
# tier's binding limit is 8,000 tokens per MINUTE — not requests. Every token
# spent on an oversized tool payload is one unavailable to the next turn, so
# results are trimmed hard before they re-enter the prompt.
MAX_TOOL_RESULT_CHARS = 2200

FALLBACK_REPLY = (
    "Try me again in a moment - my data sources didn't come back that time. "
    "If it keeps happening, tell me and I'll work around it."
)

# Even the failure messages lead with what happens next rather than what went
# wrong. "Give me a minute" is actionable; "I'm being rate-limited" is a
# complaint the user can do nothing with.
BUSY_REPLY = (
    "Give me a minute and ask again - I've hit the rate limit on the free tier "
    "I run on. It clears on its own."
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
                # Deliberately high. At 0.4 this model produced near-identical
                # replies to rephrased questions, and every answer converged on
                # the same four-paragraph shape — which is exactly what makes
                # output read as machine-generated.
                temperature=0.75,
            )
        except RateLimited:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Groq call failed")
            # A failure with tools attached is nearly always the tool-calling
            # path itself: a malformed call the API rejects outright, or a
            # request the model can't assemble. The conversation is fine — only
            # this one round trip is broken — so retry it without tools rather
            # than handing back an error. The user gets a real answer built on
            # whatever has already been gathered, and on camera that is the
            # difference between a wobble and a dead end.
            if last_pass:
                return None
            try:
                message = await chat(
                    model=model or settings.atlas_model,
                    messages=[
                        *messages,
                        {
                            "role": "system",
                            "content": (
                                "Tools are unavailable for this reply. Answer "
                                "from what you already have. Never state a "
                                "current figure you were not already given - "
                                "say you couldn't pull it and offer what you "
                                "can do instead."
                            ),
                        },
                    ],
                    tools=None,
                    max_tokens=max_tokens,
                    temperature=0.75,
                )
            except Exception:  # noqa: BLE001
                log.exception("Groq retry without tools also failed")
                return None
            return message_text(message) or final or None

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


# Pure acknowledgements need no tools, no market data and no reasoning model.
# Routing them to the small model turns a ~6s round trip into about 1s, and
# leaves the per-minute token budget for questions that actually need it.
_ACK = {
    "thanks", "thank you", "thankyou", "ty", "thx", "cheers", "ok", "okay",
    "k", "kk", "cool", "nice", "great", "perfect", "awesome", "got it",
    "gotcha", "understood", "sure", "yep", "yup", "yes", "no", "nope", "np",
    "sounds good", "makes sense", "fair enough", "good stuff", "brilliant",
    "lovely", "appreciated", "much appreciated", "ta",
}


def _is_acknowledgement(text: str) -> bool:
    return text.strip().lower().rstrip("!.?").strip() in _ACK


async def _quick_reply(user_text: str) -> str | None:
    """One cheap call for an acknowledgement. Generated, never canned."""
    try:
        message = await chat(
            model=settings.atlas_fast_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are Atlas, a financial analyst. The user sent a short "
                        "acknowledgement. Reply in one warm, natural line under 12 "
                        "words. No emoji, no lists of things you can help with, no "
                        "question unless it reads naturally. Vary your phrasing."
                    ),
                },
                {"role": "user", "content": user_text},
            ],
            max_tokens=40,
            temperature=0.7,
        )
        return message_text(message) or None
    except Exception:  # noqa: BLE001
        return None


async def run_turn(
    db: AsyncSession,
    user: User,
    user_text: str,
) -> str:
    """Run one full conversational turn and return Atlas's reply."""
    if _is_acknowledgement(user_text):
        quick = await _quick_reply(user_text)
        if quick:
            return quick

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
