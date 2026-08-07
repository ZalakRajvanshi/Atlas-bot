"""The proactive monitor.

Runs on a cadence and asks, per user: has anything happened that this person
would want to be interrupted for? Four checks, in descending order of value:

1. Thesis divergence — evidence cutting against a view they stated.
2. Explicit alerts they asked for.
3. Material moves on watchlist names.
4. Significant news on watchlist names.

Everything passes through relevance scoring and the sent-event fingerprint, so
the same fact is never delivered twice and marginal facts are never delivered
at all.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import agent
from app.ai.client import quick_json
from app.data import market, news
from app.db import repo, session_scope
from app.db.models import EventKind, MessageRole, Thesis, User
from app.jobs import relevance
from app.telegram import client as tg

log = logging.getLogger(__name__)

# A move this large is newsworthy on its own; below it we rely on news flow.
PRICE_MOVE_THRESHOLD = 4.0

DIVERGENCE_PROMPT = """You check whether new evidence contradicts an investment thesis.

You are looking for genuine divergence from a stated assumption — not merely bad news, and not price movement on its own. A stock falling does not contradict a thesis; a change in the *conditions the thesis depends on* does.

Be conservative. False alarms destroy the value of this feature entirely. If the evidence is ambiguous, or only loosely related, answer false.

Return strict JSON:
{"diverges": bool, "assumption": "the specific assumption now in question, or null", "explanation": "one sentence on what changed and why it cuts against the view, or null"}"""


def _key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


async def _deliver(
    db: AsyncSession,
    user: User,
    kind: EventKind,
    dedupe_key: str,
    headline: str,
    score: float,
    body: str,
) -> bool:
    """Send a proactive message and record it so it is never repeated."""
    if await repo.already_sent(db, user.id, dedupe_key):
        return False

    sent = await tg.send_message(user.telegram_id, body)
    if not sent:
        return False

    await repo.record_sent(db, user.id, kind, dedupe_key, headline, score)
    await repo.add_message(db, user.id, MessageRole.ASSISTANT, body, "proactive")
    log.info("Proactive %s to user %s (score %.2f)", kind.value, user.id, score)
    return True


# =============================================================================
# Checks
# =============================================================================


async def _check_thesis_divergence(
    db: AsyncSession, user: User, thesis: Thesis
) -> bool:
    """The signature check: has reality moved against a stated view?"""
    if not thesis.ticker or not thesis.assumptions:
        return False

    articles = await news.get_company_news(thesis.ticker, days=3, limit=6)
    if not articles:
        return False

    evidence = "\n".join(
        f"- {a['headline']}" + (f" — {a['summary'][:200]}" if a.get("summary") else "")
        for a in articles
    )
    payload = (
        f'THESIS: "{thesis.claim}"\n'
        f"ASSUMPTIONS IT DEPENDS ON:\n"
        + "\n".join(f"- {a}" for a in thesis.assumptions)
        + f"\n\nRECENT NEWS ON {thesis.ticker}:\n{evidence}"
    )

    verdict = await quick_json(
        system=DIVERGENCE_PROMPT, user=payload, max_tokens=500
    )
    thesis.last_checked_at = datetime.now(timezone.utc)

    if not verdict or not verdict.get("diverges"):
        return False

    explanation = verdict.get("explanation") or ""
    dedupe = _key("thesis", str(thesis.id), verdict.get("assumption") or explanation)
    if await repo.already_sent(db, user.id, dedupe):
        return False

    body = await agent.generate(
        system_extra=(
            "You are sending an unprompted message because evidence has moved "
            "against a view this person told you they hold. This is the most "
            "valuable message you send — make it count.\n\n"
            "Open by naming their view back to them in their own framing. Say "
            "what changed. Say precisely which assumption it undercuts. Be "
            "honest about how strong the signal is — if it is early or partial, "
            "say so. Do not tell them what to do. Under 90 words, no headers."
        ),
        user_prompt=(
            f'Their view: "{thesis.claim}"\n'
            f"Assumption now in question: {verdict.get('assumption')}\n"
            f"What changed: {explanation}\n"
            f"Ticker: {thesis.ticker}\n\n"
            "Verify the current price and any relevant detail with your tools "
            "before writing, then write the message."
        ),
        db=db,
        user=user,
        max_tokens=700,
    )
    if not body:
        return False

    if await _deliver(
        db, user, EventKind.THESIS_DIVERGENCE, dedupe, explanation[:200], 1.0, body
    ):
        await repo.mark_thesis_diverging(db, thesis, explanation)
        return True
    return False


async def _check_price_moves(db: AsyncSession, user: User, watchlist, theses) -> int:
    sent = 0
    alerts = await repo.get_alerts(db, user.id)
    custom = {
        a.ticker: a for a in alerts if a.kind == EventKind.PRICE_MOVE and a.ticker
    }

    for item in watchlist:
        quote = await market.get_quote(item.ticker)
        if not quote or quote.get("change_pct") is None:
            continue

        pct = float(quote["change_pct"])
        alert = custom.get(item.ticker)
        threshold = (
            float(alert.params.get("pct_move", PRICE_MOVE_THRESHOLD))
            if alert
            else PRICE_MOVE_THRESHOLD
        )
        if abs(pct) < threshold:
            continue

        today = datetime.now(timezone.utc).date().isoformat()
        dedupe = _key("move", item.ticker, today, f"{int(abs(pct))}")
        if await repo.already_sent(db, user.id, dedupe):
            continue

        # A move alone is not a story — find the cause before writing.
        articles = await news.get_company_news(item.ticker, days=2, limit=4)
        description = (
            f"{item.ticker} moved {pct:+.1f}% today "
            f"(now {quote.get('price')} {quote.get('currency', 'USD')}). "
            f"Recent headlines: "
            + ("; ".join(a["headline"] for a in articles[:3]) or "none found")
        )

        already = await repo.recently_sent_headlines(db, user.id)
        score, why = await relevance.score_event(
            user=user,
            watchlist=watchlist,
            theses=theses,
            event_description=description,
            already_told=already,
        )
        if not why:
            continue

        body = await agent.generate(
            system_extra=(
                "You are messaging unprompted about a move on a name this person "
                "follows. Lead with what caused it, not the percentage. If you "
                "cannot establish a cause, say the move has no clear catalyst — "
                "that is genuinely useful information. Under 70 words."
            ),
            user_prompt=(
                f"{description}\n\nWhy this matters to them: {why}\n\n"
                "Check the news and confirm the price before writing."
            ),
            db=db,
            user=user,
            max_tokens=600,
        )
        if body and await _deliver(
            db,
            user,
            EventKind.PRICE_MOVE,
            dedupe,
            f"{item.ticker} {pct:+.1f}%",
            score,
            body,
        ):
            sent += 1
            if alert:
                alert.last_fired_at = datetime.now(timezone.utc)

    return sent


async def _check_watchlist_news(db: AsyncSession, user: User, watchlist, theses) -> int:
    tickers = [w.ticker for w in watchlist]
    if not tickers:
        return 0

    articles = await news.get_news_for_tickers(tickers[:12], days=1, per_ticker=2)
    if not articles:
        return 0

    already = await repo.recently_sent_headlines(db, user.id)
    sent = 0

    for article in articles[:10]:
        dedupe = _key("news", article["key"])
        if await repo.already_sent(db, user.id, dedupe):
            continue

        description = (
            f"[{article.get('ticker', '?')}] {article['headline']}\n"
            f"{(article.get('summary') or '')[:400]}\n"
            f"Source: {article.get('source')}"
        )
        score, why = await relevance.score_event(
            user=user,
            watchlist=watchlist,
            theses=theses,
            event_description=description,
            already_told=already,
        )
        if not why:
            # Record the miss so we never re-score this article for this user.
            await repo.record_sent(
                db, user.id, EventKind.NEWS, dedupe, None, score
            )
            continue

        body = await agent.generate(
            system_extra=(
                "You are messaging unprompted about a development on a name this "
                "person follows. Do not summarise the article — explain what it "
                "changes for them. Under 80 words, no headers, no link dump."
            ),
            user_prompt=(
                f"{description}\n\nWhy it matters to them: {why}\n\n"
                "Pull any market data you need, then write the message."
            ),
            db=db,
            user=user,
            max_tokens=600,
        )
        if body and await _deliver(
            db, user, EventKind.NEWS, dedupe, article["headline"], score, body
        ):
            sent += 1
            already.append(article["headline"])
            # At most two unprompted news pushes per sweep, however good.
            if sent >= 2:
                break

    return sent


# =============================================================================
# Orchestration
# =============================================================================


async def check_user(db: AsyncSession, user: User) -> int:
    watchlist = await repo.get_watchlist(db, user.id)
    theses = await repo.get_theses(db, user.id)

    if not watchlist and not theses:
        return 0

    sent = 0
    for thesis in theses:
        try:
            if await _check_thesis_divergence(db, user, thesis):
                sent += 1
        except Exception:  # noqa: BLE001
            log.exception("Thesis check failed for thesis %s", thesis.id)

    try:
        sent += await _check_price_moves(db, user, watchlist, theses)
    except Exception:  # noqa: BLE001
        log.exception("Price check failed for user %s", user.id)

    try:
        sent += await _check_watchlist_news(db, user, watchlist, theses)
    except Exception:  # noqa: BLE001
        log.exception("News check failed for user %s", user.id)

    return sent


async def run_monitor() -> None:
    """Sweep every active user. Scheduled; must never raise."""
    log.info("Monitor sweep starting")
    total = 0
    try:
        async with session_scope() as db:
            users = await repo.all_active_users(db)

        for user in users:
            try:
                # A session per user keeps one failure from rolling back others.
                async with session_scope() as db:
                    fresh = await repo.get_user_by_id(db, user.id)
                    if fresh:
                        total += await check_user(db, fresh)
            except Exception:  # noqa: BLE001
                log.exception("Monitor failed for user %s", user.id)
            await asyncio.sleep(0.5)  # stay polite to the data providers
    except Exception:  # noqa: BLE001
        log.exception("Monitor sweep failed")

    log.info("Monitor sweep complete — %d notifications sent", total)
