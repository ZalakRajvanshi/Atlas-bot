"""Repository functions — the only place raw queries live.

Keeping SQL here means the agent tools, jobs and Telegram handlers all speak
in domain terms and stay readable.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Alert,
    Document,
    DocumentChunk,
    EventKind,
    Fact,
    FactKind,
    Message,
    MessageRole,
    OnboardingStage,
    SentEvent,
    Thesis,
    ThesisStatus,
    User,
    WatchlistItem,
    utcnow,
)

# =============================================================================
# Users
# =============================================================================


async def get_or_create_user(
    db: AsyncSession,
    telegram_id: int,
    first_name: str | None = None,
    username: str | None = None,
) -> tuple[User, bool]:
    """Returns (user, was_created)."""
    res = await db.execute(select(User).where(User.telegram_id == telegram_id))
    user = res.scalar_one_or_none()
    if user:
        user.last_seen_at = utcnow()
        # Telegram display names change; keep ours fresh.
        if first_name and user.first_name != first_name:
            user.first_name = first_name
        return user, False

    user = User(telegram_id=telegram_id, first_name=first_name, username=username)
    db.add(user)
    await db.flush()
    return user, True


async def get_user_by_id(db: AsyncSession, user_id: int) -> User | None:
    """Re-load a user into the current session.

    Background jobs open a fresh session per user, so a User loaded in an
    earlier session is detached; re-fetching by id is clearer and safer than
    merging a detached graph with eager-loaded collections.
    """
    res = await db.execute(select(User).where(User.id == user_id))
    return res.scalar_one_or_none()


async def all_active_users(db: AsyncSession) -> list[User]:
    res = await db.execute(select(User).where(User.is_active.is_(True)))
    return list(res.scalars().all())


async def set_onboarding_stage(
    db: AsyncSession, user: User, stage: OnboardingStage
) -> None:
    user.onboarding_stage = stage


# =============================================================================
# Watchlist
# =============================================================================


async def add_to_watchlist(
    db: AsyncSession,
    user: User,
    ticker: str,
    company_name: str | None = None,
    reason: str | None = None,
    priority: int = 1,
) -> WatchlistItem:
    ticker = ticker.upper().strip()
    res = await db.execute(
        select(WatchlistItem).where(
            WatchlistItem.user_id == user.id, WatchlistItem.ticker == ticker
        )
    )
    item = res.scalar_one_or_none()
    if item:
        # Re-adding with better information should enrich, never blank out.
        if company_name:
            item.company_name = company_name
        if reason:
            item.reason = reason
        item.priority = max(item.priority, priority)
        item.last_discussed_at = utcnow()
        return item

    item = WatchlistItem(
        user_id=user.id,
        ticker=ticker,
        company_name=company_name,
        reason=reason,
        priority=priority,
        last_discussed_at=utcnow(),
    )
    db.add(item)
    await db.flush()
    return item


async def remove_from_watchlist(db: AsyncSession, user: User, ticker: str) -> bool:
    res = await db.execute(
        select(WatchlistItem).where(
            WatchlistItem.user_id == user.id,
            WatchlistItem.ticker == ticker.upper().strip(),
        )
    )
    item = res.scalar_one_or_none()
    if not item:
        return False
    await db.delete(item)
    return True


async def get_watchlist(db: AsyncSession, user_id: int) -> list[WatchlistItem]:
    res = await db.execute(
        select(WatchlistItem)
        .where(WatchlistItem.user_id == user_id)
        .order_by(desc(WatchlistItem.priority), WatchlistItem.ticker)
    )
    return list(res.scalars().all())


async def touch_watchlist_discussion(
    db: AsyncSession, user_id: int, tickers: list[str]
) -> None:
    """Mark names as recently talked about — feeds relevance scoring."""
    if not tickers:
        return
    await db.execute(
        update(WatchlistItem)
        .where(
            WatchlistItem.user_id == user_id,
            WatchlistItem.ticker.in_([t.upper() for t in tickers]),
        )
        .values(last_discussed_at=utcnow())
    )


# =============================================================================
# Facts
# =============================================================================


async def add_fact(
    db: AsyncSession,
    user: User,
    content: str,
    kind: FactKind = FactKind.CONTEXT,
    confidence: float = 0.8,
) -> Fact | None:
    """Store a fact, skipping near-duplicates.

    Cheap normalised comparison rather than embeddings — at the volume a
    single user generates this is entirely sufficient and has no latency cost.
    """
    normalized = " ".join(content.lower().split())
    existing = await db.execute(
        select(Fact).where(Fact.user_id == user.id, Fact.is_current.is_(True))
    )
    for f in existing.scalars().all():
        if " ".join(f.content.lower().split()) == normalized:
            f.last_referenced_at = utcnow()
            return None

    fact = Fact(user_id=user.id, content=content, kind=kind, confidence=confidence)
    db.add(fact)
    await db.flush()
    return fact


async def get_facts(db: AsyncSession, user_id: int, limit: int = 40) -> list[Fact]:
    res = await db.execute(
        select(Fact)
        .where(Fact.user_id == user_id, Fact.is_current.is_(True))
        .order_by(desc(Fact.created_at))
        .limit(limit)
    )
    return list(res.scalars().all())


async def supersede_fact(db: AsyncSession, user_id: int, fact_id: int) -> bool:
    res = await db.execute(
        select(Fact).where(Fact.id == fact_id, Fact.user_id == user_id)
    )
    fact = res.scalar_one_or_none()
    if not fact:
        return False
    fact.is_current = False
    return True


# =============================================================================
# Theses — the differentiator
# =============================================================================


async def add_thesis(
    db: AsyncSession,
    user: User,
    subject: str,
    claim: str,
    assumptions: list[str],
    ticker: str | None = None,
    stance: str = "watching",
) -> Thesis:
    from app.db.models import ThesisStance

    ticker_norm = ticker.upper().strip() if ticker else None
    subject_norm = " ".join(subject.lower().split())

    # One active view per subject. Both the `record_thesis` tool and the
    # background extraction pass try to file the same view within a single
    # turn, so the invariant is enforced here rather than at either call site.
    res = await db.execute(
        select(Thesis).where(
            Thesis.user_id == user.id,
            Thesis.status.in_([ThesisStatus.ACTIVE, ThesisStatus.DIVERGING]),
        )
    )
    for existing in res.scalars().all():
        same_ticker = bool(ticker_norm) and (existing.ticker or "") == ticker_norm
        same_subject = " ".join((existing.subject or "").lower().split()) == subject_norm
        if same_ticker or same_subject:
            # Refine in place — a restated view usually carries better
            # assumptions than the first pass did.
            if len(assumptions or []) > len(existing.assumptions or []):
                existing.assumptions = assumptions
                existing.claim = claim
            existing.stated_at = utcnow()
            return existing

    thesis = Thesis(
        user_id=user.id,
        subject=subject,
        claim=claim,
        assumptions=assumptions or [],
        ticker=ticker_norm,
        stance=ThesisStance(stance),
    )
    db.add(thesis)
    await db.flush()
    return thesis


async def get_theses(
    db: AsyncSession, user_id: int, active_only: bool = True
) -> list[Thesis]:
    stmt = select(Thesis).where(Thesis.user_id == user_id)
    if active_only:
        stmt = stmt.where(
            Thesis.status.in_([ThesisStatus.ACTIVE, ThesisStatus.DIVERGING])
        )
    res = await db.execute(stmt.order_by(desc(Thesis.stated_at)))
    return list(res.scalars().all())


async def theses_for_ticker(db: AsyncSession, ticker: str) -> list[Thesis]:
    """Across all users — used by the monitor when an event lands."""
    res = await db.execute(
        select(Thesis).where(
            Thesis.ticker == ticker.upper(),
            Thesis.status.in_([ThesisStatus.ACTIVE, ThesisStatus.DIVERGING]),
        )
    )
    return list(res.scalars().all())


async def mark_thesis_diverging(
    db: AsyncSession, thesis: Thesis, note: str
) -> None:
    thesis.status = ThesisStatus.DIVERGING
    thesis.divergence_note = note
    thesis.last_checked_at = utcnow()


# =============================================================================
# Conversation
# =============================================================================


async def add_message(
    db: AsyncSession,
    user_id: int,
    role: MessageRole,
    content: str,
    modality: str = "text",
) -> Message:
    msg = Message(user_id=user_id, role=role, content=content, modality=modality)
    db.add(msg)
    await db.flush()
    return msg


async def recent_messages(
    db: AsyncSession, user_id: int, limit: int, after_id: int = 0
) -> list[Message]:
    """Most recent `limit` messages, returned oldest-first for the LLM."""
    res = await db.execute(
        select(Message)
        .where(Message.user_id == user_id, Message.id > after_id)
        .order_by(desc(Message.id))
        .limit(limit)
    )
    return list(reversed(res.scalars().all()))


async def messages_to_summarize(
    db: AsyncSession, user_id: int, through_id: int, keep_recent: int
) -> list[Message]:
    """Messages older than the verbatim window that aren't yet summarized."""
    res = await db.execute(
        select(Message)
        .where(Message.user_id == user_id)
        .order_by(desc(Message.id))
        .limit(keep_recent)
    )
    recent = list(res.scalars().all())
    if len(recent) < keep_recent:
        return []
    oldest_kept_id = recent[-1].id

    res2 = await db.execute(
        select(Message)
        .where(
            Message.user_id == user_id,
            Message.id > through_id,
            Message.id < oldest_kept_id,
        )
        .order_by(Message.id)
    )
    return list(res2.scalars().all())


# =============================================================================
# Documents
# =============================================================================


async def add_document(
    db: AsyncSession,
    user_id: int,
    filename: str,
    chunks: list[tuple[int, int | None, str]],
    title: str | None = None,
    doc_type: str | None = None,
    ticker: str | None = None,
    page_count: int = 0,
    summary: str | None = None,
) -> Document:
    doc = Document(
        user_id=user_id,
        filename=filename,
        title=title or filename,
        doc_type=doc_type,
        ticker=ticker.upper() if ticker else None,
        page_count=page_count,
        char_count=sum(len(c[2]) for c in chunks),
        summary=summary,
        last_used_at=utcnow(),
    )
    db.add(doc)
    await db.flush()

    db.add_all(
        [
            DocumentChunk(document_id=doc.id, ordinal=o, page=p, text=t)
            for (o, p, t) in chunks
        ]
    )
    await db.flush()
    return doc


async def get_documents(db: AsyncSession, user_id: int) -> list[Document]:
    res = await db.execute(
        select(Document)
        .where(Document.user_id == user_id)
        .order_by(desc(Document.created_at))
    )
    return list(res.scalars().all())


async def get_document(
    db: AsyncSession, user_id: int, document_id: int
) -> Document | None:
    res = await db.execute(
        select(Document).where(Document.id == document_id, Document.user_id == user_id)
    )
    return res.scalar_one_or_none()


async def get_chunks(db: AsyncSession, document_id: int) -> list[DocumentChunk]:
    res = await db.execute(
        select(DocumentChunk)
        .where(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.ordinal)
    )
    return list(res.scalars().all())


# =============================================================================
# Alerts & proactive dedupe
# =============================================================================


async def add_alert(
    db: AsyncSession,
    user_id: int,
    kind: EventKind,
    description: str,
    ticker: str | None = None,
    params: dict | None = None,
) -> Alert:
    alert = Alert(
        user_id=user_id,
        kind=kind,
        ticker=ticker.upper() if ticker else None,
        description=description,
        params=params or {},
    )
    db.add(alert)
    await db.flush()
    return alert


async def get_alerts(db: AsyncSession, user_id: int) -> list[Alert]:
    res = await db.execute(
        select(Alert).where(Alert.user_id == user_id, Alert.is_active.is_(True))
    )
    return list(res.scalars().all())


async def active_alerts(db: AsyncSession) -> list[Alert]:
    res = await db.execute(select(Alert).where(Alert.is_active.is_(True)))
    return list(res.scalars().all())


async def already_sent(db: AsyncSession, user_id: int, dedupe_key: str) -> bool:
    res = await db.execute(
        select(SentEvent.id).where(
            SentEvent.user_id == user_id, SentEvent.dedupe_key == dedupe_key
        )
    )
    return res.scalar_one_or_none() is not None


async def record_sent(
    db: AsyncSession,
    user_id: int,
    kind: EventKind,
    dedupe_key: str,
    headline: str | None = None,
    score: float = 0.0,
) -> None:
    db.add(
        SentEvent(
            user_id=user_id,
            kind=kind,
            dedupe_key=dedupe_key,
            headline=headline,
            relevance_score=score,
        )
    )
    # Sessions run with autoflush off, and the monitor checks `already_sent`
    # repeatedly within one pass — without this flush it would re-send the
    # same event several times before the transaction commits.
    await db.flush()


async def recently_sent_headlines(
    db: AsyncSession, user_id: int, hours: int = 36
) -> list[str]:
    """What Atlas already told this user — so the brief doesn't repeat itself."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    res = await db.execute(
        select(SentEvent.headline)
        .where(SentEvent.user_id == user_id, SentEvent.created_at >= cutoff)
        .order_by(desc(SentEvent.created_at))
        .limit(40)
    )
    return [h for h in res.scalars().all() if h]
