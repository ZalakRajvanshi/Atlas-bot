"""Relevance scoring — the gate that keeps Atlas quiet.

The spec's hardest requirement is "if there is nothing important to share, the
assistant should remain silent." That is easy to say and rarely implemented,
because silence looks like a broken feature. It is enforced here: every
candidate push is scored against this specific user's profile, and anything
below threshold is dropped and never mentioned again.
"""

from __future__ import annotations

import logging

from app.ai import prompts
from app.ai.client import quick_json
from app.config import settings
from app.db.models import Thesis, User, WatchlistItem

log = logging.getLogger(__name__)

def _profile_block(
    user: User, watchlist: list[WatchlistItem], theses: list[Thesis]
) -> str:
    lines = [f"Role: {user.role or 'unknown'}"]
    if watchlist:
        lines.append(
            "Watchlist: "
            + ", ".join(
                f"{w.ticker}"
                + (f" ({w.reason})" if w.reason else "")
                + (" [high priority]" if w.priority >= 2 else "")
                for w in watchlist
            )
        )
    if theses:
        lines.append(
            "Stated views:\n"
            + "\n".join(
                f'- {t.subject}: "{t.claim}" — depends on: '
                + "; ".join(t.assumptions or ["(unspecified)"])
                for t in theses
            )
        )
    return "\n".join(lines)


async def score_event(
    *,
    user: User,
    watchlist: list[WatchlistItem],
    theses: list[Thesis],
    event_description: str,
    already_told: list[str],
) -> tuple[float, str | None]:
    """Return (score, why_it_matters). Below-threshold events return (score, None)."""
    payload = (
        f"USER PROFILE\n{_profile_block(user, watchlist, theses)}\n\n"
        f"ALREADY TOLD THEM RECENTLY (treat near-duplicates as score 0):\n"
        + ("\n".join(f"- {h}" for h in already_told[:15]) or "(nothing)")
        + f"\n\nCANDIDATE EVENT\n{event_description}"
    )

    result = await quick_json(
        system=prompts.RELEVANCE_PROMPT, user=payload, max_tokens=400
    )
    if not result:
        # A scorer failure must not become an unfiltered notification.
        return 0.0, None

    try:
        score = float(result.get("score", 0.0))
    except (TypeError, ValueError):
        return 0.0, None

    if score < settings.proactive_relevance_threshold:
        return score, None

    return score, result.get("why_it_matters")
