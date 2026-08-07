"""The daily brief.

Written by the same agent that holds the conversation, with the same cached
persona, so it reads as the analyst the user already knows rather than a
templated digest. It is allowed — encouraged — to say that nothing matters
today.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import agent, prompts
from app.db import repo, session_scope
from app.db.models import EventKind, MessageRole, OnboardingStage, User
from app.telegram import client as tg

log = logging.getLogger(__name__)


def local_now(user: User) -> datetime:
    try:
        return datetime.now(ZoneInfo(user.timezone or "UTC"))
    except (ZoneInfoNotFoundError, ValueError):
        return datetime.now(timezone.utc)


def is_due(user: User, tolerance_minutes: int = 10) -> bool:
    """True when the user's local clock is within the window of their slot."""
    if not user.briefing_enabled or not user.briefing_time:
        return False
    try:
        hour, minute = (int(p) for p in user.briefing_time.split(":"))
    except (ValueError, AttributeError):
        return False

    now = local_now(user)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return abs((now - target).total_seconds()) <= tolerance_minutes * 60


async def build_briefing(db: AsyncSession, user: User) -> str | None:
    watchlist = await repo.get_watchlist(db, user.id)
    theses = await repo.get_theses(db, user.id)
    already = await repo.recently_sent_headlines(db, user.id)

    tickers = ", ".join(w.ticker for w in watchlist) or "none yet"
    thesis_block = (
        "\n".join(f'- {t.subject}: "{t.claim}"' for t in theses) or "none recorded"
    )
    told_block = "\n".join(f"- {h}" for h in already[:15]) or "(nothing recently)"

    prompt = (
        f"Write {user.first_name or 'their'} brief for "
        f"{local_now(user).strftime('%A %d %B')}.\n\n"
        f"Their watchlist: {tickers}\n"
        f"Their stated views:\n{thesis_block}\n\n"
        f"You already told them these in the last 36 hours — do NOT repeat them:\n"
        f"{told_block}\n\n"
        "Gather what you need first: the market snapshot, quotes and news for "
        "their watchlist names, and any earnings dates that are close. Then "
        "write the brief. If there is genuinely nothing worth their attention "
        "today, say exactly that in one line and stop."
    )

    return await agent.generate(
        system_extra=prompts.BRIEFING_PROMPT,
        user_prompt=prompt,
        db=db,
        user=user,
        # Generous: thinking and the brief share this budget.
        max_tokens=5000,
        effort="medium",
    )


async def send_briefing(db: AsyncSession, user: User) -> bool:
    today = local_now(user).date().isoformat()
    dedupe = hashlib.sha256(f"briefing|{today}".encode()).hexdigest()[:24]

    if await repo.already_sent(db, user.id, dedupe):
        return False

    text = await build_briefing(db, user)
    if not text:
        log.warning("Briefing generation returned nothing for user %s", user.id)
        return False

    if not await tg.send_message(user.telegram_id, text):
        return False

    await repo.record_sent(
        db, user.id, EventKind.BRIEFING, dedupe, f"Daily brief {today}", 1.0
    )
    await repo.add_message(db, user.id, MessageRole.ASSISTANT, text, "proactive")
    log.info("Briefing sent to user %s", user.id)
    return True


async def run_briefings() -> None:
    """Scheduled every 10 minutes; sends only to users whose slot is due."""
    try:
        async with session_scope() as db:
            users = await repo.all_active_users(db)
    except Exception:  # noqa: BLE001
        log.exception("Could not load users for briefing")
        return

    due = [
        u
        for u in users
        # Someone who has never actually spoken has nothing to brief on.
        if u.onboarding_stage == OnboardingStage.ACTIVE and is_due(u)
    ]
    if not due:
        return

    log.info("Briefing run: %d user(s) due", len(due))
    for user in due:
        try:
            async with session_scope() as db:
                fresh = await repo.get_user_by_id(db, user.id)
                if fresh:
                    await send_briefing(db, fresh)
        except Exception:  # noqa: BLE001
            log.exception("Briefing failed for user %s", user.id)
        await asyncio.sleep(1.0)
