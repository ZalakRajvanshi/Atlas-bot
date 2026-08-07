"""Memory services: context assembly, background fact extraction, summarization.

Two paths write to memory, deliberately:

1. The model calls `remember` / `record_thesis` mid-conversation when it
   consciously notices something worth keeping.
2. This module runs a cheap extraction pass *after* each exchange, catching
   what the model didn't think to save.

The second path is why Atlas keeps learning even when it isn't paying
attention — most of what makes an assistant feel like it knows you is picked
up incidentally, not declared.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import prompts
from app.ai.client import quick_json, quick_text
from app.config import settings
from app.db import repo
from app.db.models import FactKind, MessageRole, User

log = logging.getLogger(__name__)


# =============================================================================
# Context assembly — what Atlas knows, going into a turn
# =============================================================================


async def build_context(db: AsyncSession, user: User) -> str:
    watchlist = await repo.get_watchlist(db, user.id)
    facts = await repo.get_facts(db, user.id)
    theses = await repo.get_theses(db, user.id)
    documents = await repo.get_documents(db, user.id)
    recently_told = await repo.recently_sent_headlines(db, user.id)

    profile_lines: list[str] = []
    if user.first_name:
        profile_lines.append(f"- Name: {user.first_name}")
    if user.role:
        profile_lines.append(f"- Role: {user.role}")
    if user.timezone and user.timezone != "UTC":
        profile_lines.append(f"- Timezone: {user.timezone}")
    if user.response_style:
        profile_lines.append(f"- Style preference: {user.response_style}")
    profile_lines += [f"- {f.content}" for f in facts]

    watchlist_lines = []
    for item in watchlist:
        label = f"{item.ticker}"
        if item.company_name:
            label += f" ({item.company_name})"
        if item.reason:
            label += f" — {item.reason}"
        if item.priority >= 2:
            label += " [high priority]"
        watchlist_lines.append(f"- {label}")

    thesis_lines = []
    for t in theses:
        assumptions = "; ".join(t.assumptions or []) or "no assumptions recorded"
        line = (
            f"- {t.subject} [{t.stance.value}] \"{t.claim}\" "
            f"(rests on: {assumptions})"
        )
        if t.divergence_note:
            line += f" ⚠ DIVERGING: {t.divergence_note}"
        thesis_lines.append(line)

    document_lines = [
        f"- id={d.id} \"{d.title}\""
        + (f" [{d.doc_type}]" if d.doc_type else "")
        + (f" about {d.ticker}" if d.ticker else "")
        + (f", {d.page_count}pp" if d.page_count else "")
        for d in documents
    ]

    return prompts.build_context_block(
        profile_lines=profile_lines,
        watchlist_lines=watchlist_lines,
        thesis_lines=thesis_lines,
        document_lines=document_lines,
        conversation_summary=user.conversation_summary,
        recently_told=recently_told,
        onboarding_stage=user.onboarding_stage.value,
    )


async def load_history(db: AsyncSession, user: User) -> list[dict]:
    """Verbatim recent turns, formatted for the Messages API."""
    messages = await repo.recent_messages(db, user.id, limit=settings.max_verbatim_turns)
    history: list[dict] = []
    for msg in messages:
        role = "user" if msg.role == MessageRole.USER else "assistant"
        content = msg.content.strip()
        if not content:
            continue
        # The API rejects consecutive same-role turns in some shapes; merging
        # keeps the transcript valid when proactive pushes interleave.
        if history and history[-1]["role"] == role:
            history[-1]["content"] += f"\n\n{content}"
        else:
            history.append({"role": role, "content": content})

    # Conversations must open on a user turn.
    while history and history[0]["role"] != "user":
        history.pop(0)
    return history


# =============================================================================
# Background learning
# =============================================================================

async def learn_from_exchange(
    db: AsyncSession, user: User, user_message: str, assistant_reply: str
) -> None:
    """Extract durable facts and any stated thesis. Never raises."""
    known = await repo.get_facts(db, user.id)
    known_block = "\n".join(f"- {f.content}" for f in known) or "(nothing yet)"

    payload = (
        f"Already known about this user:\n{known_block}\n\n"
        f"--- New exchange ---\n"
        f"User: {user_message[:4000]}\n\n"
        f"Assistant: {assistant_reply[:2500]}"
    )

    result = await quick_json(
        system=prompts.FACT_EXTRACTION_PROMPT, user=payload
    )
    if not result:
        return

    try:
        for raw in (result.get("facts") or [])[:3]:
            content = (raw.get("content") or "").strip()
            # Small models return bare nouns ("Nvidia", "associate") as
            # "facts". A memory full of keyword fragments is worse than an
            # empty one — it pollutes every future prompt.
            if not _is_real_fact(content):
                continue
            try:
                kind = FactKind(raw.get("kind", "context"))
            except ValueError:
                kind = FactKind.CONTEXT
            await repo.add_fact(
                db,
                user,
                content=content,
                kind=kind,
                confidence=float(raw.get("confidence", 0.7)),
            )
            # Promote a confidently-stated role onto the profile itself.
            if kind == FactKind.ROLE and not user.role:
                user.role = content[:128]

        tickers = [t.upper() for t in (result.get("tickers_discussed") or []) if t]
        await repo.touch_watchlist_discussion(db, user.id, tickers)

        thesis = result.get("thesis")
        if thesis and thesis.get("claim") and thesis.get("assumptions"):
            existing = await repo.get_theses(db, user.id)
            # The model usually already filed this via `record_thesis` during
            # the turn. Exact text comparison misses it ("I am long" vs "I'm
            # long"), so match on subject/ticker recency instead — one view
            # per subject per conversation is the right granularity.
            if not _thesis_already_filed(thesis, existing):
                await repo.add_thesis(
                    db,
                    user,
                    subject=thesis["subject"],
                    claim=thesis["claim"],
                    assumptions=thesis["assumptions"],
                    ticker=thesis.get("ticker"),
                    stance=thesis.get("stance", "watching"),
                )
    except Exception as exc:  # noqa: BLE001 — learning must never break chat
        log.warning("Fact extraction post-processing failed: %s", exc)


MIN_FACT_WORDS = 4


def _is_real_fact(content: str) -> bool:
    """Reject keyword fragments masquerading as facts."""
    if not content:
        return False
    words = content.split()
    if len(words) < MIN_FACT_WORDS:
        return False
    # A real fact says something about the person, so it needs a verb-ish
    # word. Cheap heuristic, but it removes essentially all the noise.
    # Padded so a verb at the start ("Works as a…") still matches.
    lowered = f" {content.lower().strip()} "
    return any(
        marker in lowered
        for marker in (
            " is ", " was ", " has ", " have ", " works", " covers", " holds",
            " follows", " wants", " prefers", " trades", " invests", " owns",
            " based ", " focuses", " manages", " runs ", " likes", " uses",
            " concerned", " worried", " long ", " short ", " looking",
        )
    )


def _thesis_already_filed(candidate: dict, existing: list) -> bool:
    """True when this view is already on file for the same subject."""
    ticker = (candidate.get("ticker") or "").upper().strip()
    subject = " ".join((candidate.get("subject") or "").lower().split())
    for t in existing:
        if ticker and (t.ticker or "").upper() == ticker:
            return True
        if subject and " ".join((t.subject or "").lower().split()) == subject:
            return True
    return False


async def maybe_summarize(db: AsyncSession, user: User) -> None:
    """Roll older turns into the running summary once they fall out of the window."""
    stale = await repo.messages_to_summarize(
        db,
        user.id,
        through_id=user.summarized_through_id or 0,
        keep_recent=settings.max_verbatim_turns,
    )
    if len(stale) < 6:
        return

    transcript = "\n\n".join(
        f"{'User' if m.role == MessageRole.USER else 'Atlas'}: {m.content[:1500]}"
        for m in stale
    )
    payload = (
        f"Existing summary:\n{user.conversation_summary or '(none)'}\n\n"
        f"New messages to fold in:\n{transcript}"
    )

    summary = await quick_text(
        system=prompts.SUMMARIZATION_PROMPT, user=payload, max_tokens=600
    )
    if summary:
        user.conversation_summary = summary
        user.summarized_through_id = stale[-1].id
        log.info("Summarized %d messages for user %s", len(stale), user.id)
