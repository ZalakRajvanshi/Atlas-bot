"""Atlas persistence model.

Design notes
------------
Two tables carry the product's differentiation and deserve explanation:

`Thesis` — Atlas records *why* a user believes what they believe, decomposed
into falsifiable assumptions. Background jobs then watch reality for
divergence from those assumptions. This is what turns memory from a party
trick ("I remember you like Nvidia") into something load-bearing ("Microsoft
guided capex down, which is the assumption your Nvidia call rests on").

`SentEvent` — every proactive push is fingerprinted. Atlas will not tell you
the same thing twice, and tomorrow's briefing knows what yesterday's said.
Non-repetition is most of what separates an assistant from a news feed.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# =============================================================================
# Enums
# =============================================================================


class OnboardingStage(str, enum.Enum):
    """Deliberately shallow.

    Atlas does not run a wizard. It asks one opening question, starts working,
    and keeps learning from ordinary conversation. `ACTIVE` means "just talk".
    """

    NEW = "new"
    OPENING_ASKED = "opening_asked"
    ACTIVE = "active"


class FactKind(str, enum.Enum):
    ROLE = "role"              # "VC associate covering semis"
    INTEREST = "interest"      # "follows AI infrastructure"
    PREFERENCE = "preference"  # "wants numbers, hates hedging"
    GOAL = "goal"              # "raising a seed round in Q1"
    CONTEXT = "context"        # "based in Bangalore, trades US markets"
    CONSTRAINT = "constraint"  # "cannot hold single names, funds only"


class ThesisStance(str, enum.Enum):
    LONG = "long"
    SHORT = "short"
    WATCHING = "watching"
    CONCERNED = "concerned"


class ThesisStatus(str, enum.Enum):
    ACTIVE = "active"
    DIVERGING = "diverging"      # evidence is cutting against an assumption
    INVALIDATED = "invalidated"  # user acknowledged the break
    CLOSED = "closed"            # user exited / stopped caring


class MessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"


class EventKind(str, enum.Enum):
    BRIEFING = "briefing"
    EARNINGS = "earnings"
    PRICE_MOVE = "price_move"
    NEWS = "news"
    FILING = "filing"
    THESIS_DIVERGENCE = "thesis_divergence"
    REMINDER = "reminder"


# =============================================================================
# Core user
# =============================================================================


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # BigInteger: modern Telegram user IDs exceed the 32-bit INTEGER range.
    telegram_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, index=True, nullable=False
    )
    first_name: Mapped[str | None] = mapped_column(String(128))
    username: Mapped[str | None] = mapped_column(String(128))

    # Learned conversationally, never asked as a form.
    role: Mapped[str | None] = mapped_column(String(128))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")

    # Local clock time for the morning brief, e.g. "07:30". None = no briefing.
    briefing_time: Mapped[str | None] = mapped_column(String(5), default="07:30")
    briefing_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    # Free-text style instruction Atlas has inferred, e.g. "terse, numbers-first".
    response_style: Mapped[str | None] = mapped_column(Text)

    onboarding_stage: Mapped[OnboardingStage] = mapped_column(
        Enum(OnboardingStage), default=OnboardingStage.NEW
    )

    # Rolling narrative summary of everything older than the verbatim window.
    conversation_summary: Mapped[str | None] = mapped_column(Text)
    summarized_through_id: Mapped[int | None] = mapped_column(Integer, default=0)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    watchlist: Mapped[list["WatchlistItem"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )
    facts: Mapped[list["Fact"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )
    theses: Mapped[list["Thesis"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )


# =============================================================================
# Memory
# =============================================================================


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (UniqueConstraint("user_id", "ticker", name="uq_user_ticker"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    company_name: Mapped[str | None] = mapped_column(String(255))

    # *Why* it is on the list. Drives relevance scoring for alerts.
    reason: Mapped[str | None] = mapped_column(Text)

    # Users care about some names far more than others; alerts respect this.
    priority: Mapped[int] = mapped_column(Integer, default=1)  # 0 low, 1 normal, 2 high

    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_discussed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(back_populates="watchlist")


class Fact(Base):
    """An atomic, durable thing Atlas knows about the user."""

    __tablename__ = "facts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    kind: Mapped[FactKind] = mapped_column(Enum(FactKind), default=FactKind.CONTEXT)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Superseded facts are kept (people change jobs) but excluded from context.
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.8)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_referenced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(back_populates="facts")


class Thesis(Base):
    """A user's stated investment view, broken into checkable assumptions.

    Example:
        ticker      NVDA
        stance      long
        claim       "Long Nvidia — hyperscaler capex holds through 2026"
        assumptions ["MSFT/GOOGL/AMZN capex guidance stays flat or up",
                     "No credible competing accelerator ships at volume",
                     "Gross margin stays above 70%"]

    The monitor job checks incoming events against `assumptions` and flags
    divergence. This is Atlas's signature behaviour.
    """

    __tablename__ = "theses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    ticker: Mapped[str | None] = mapped_column(String(16), index=True)
    subject: Mapped[str] = mapped_column(String(255))  # company/sector/macro theme

    stance: Mapped[ThesisStance] = mapped_column(
        Enum(ThesisStance), default=ThesisStance.WATCHING
    )
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    assumptions: Mapped[list] = mapped_column(JSON, default=list)

    status: Mapped[ThesisStatus] = mapped_column(
        Enum(ThesisStatus), default=ThesisStatus.ACTIVE
    )

    # Populated when the monitor detects a break, so Atlas can explain itself.
    divergence_note: Mapped[str | None] = mapped_column(Text)

    stated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(back_populates="theses")


# =============================================================================
# Conversation
# =============================================================================


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_user_created", "user_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    role: Mapped[MessageRole] = mapped_column(Enum(MessageRole))
    content: Mapped[str] = mapped_column(Text)

    # "text" | "voice" | "photo" | "document" | "proactive"
    modality: Mapped[str] = mapped_column(String(24), default="text")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# =============================================================================
# Documents
# =============================================================================


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    filename: Mapped[str] = mapped_column(String(512))
    title: Mapped[str | None] = mapped_column(String(512))

    # Inferred: "10-K" | "10-Q" | "earnings_deck" | "research" | "other"
    doc_type: Mapped[str | None] = mapped_column(String(64))

    # Ticker this document is about, when Atlas can determine it. Lets the
    # monitor cross-reference an uploaded 10-K's risk factors against live news.
    ticker: Mapped[str | None] = mapped_column(String(16), index=True)

    page_count: Mapped[int] = mapped_column(Integer, default=0)
    char_count: Mapped[int] = mapped_column(Integer, default=0)

    # One-paragraph orientation summary, generated once at ingest.
    summary: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (Index("ix_chunks_doc", "document_id", "ordinal"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )

    ordinal: Mapped[int] = mapped_column(Integer)
    page: Mapped[int | None] = mapped_column(Integer)  # enables page citations
    text: Mapped[str] = mapped_column(Text)

    document: Mapped["Document"] = relationship(back_populates="chunks")


# =============================================================================
# Proactive layer
# =============================================================================


class Alert(Base):
    """A standing user request: 'tell me if Tesla moves more than 5% in a day'."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    kind: Mapped[EventKind] = mapped_column(Enum(EventKind))
    ticker: Mapped[str | None] = mapped_column(String(16), index=True)

    # Natural-language restatement, so Atlas can confirm it back to the user.
    description: Mapped[str] = mapped_column(Text)

    # Machine-checkable parameters, e.g. {"pct_move": 5.0, "direction": "any"}
    params: Mapped[dict] = mapped_column(JSON, default=dict)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SentEvent(Base):
    """Fingerprint of everything Atlas has proactively said.

    Prevents the single most assistant-destroying failure mode: telling
    someone something they already know.
    """

    __tablename__ = "sent_events"
    __table_args__ = (
        UniqueConstraint("user_id", "dedupe_key", name="uq_user_dedupe"),
        Index("ix_sent_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    kind: Mapped[EventKind] = mapped_column(Enum(EventKind))

    # Stable hash of the underlying real-world event (headline URL, filing
    # accession number, "NVDA-2026-Q1-earnings", etc).
    dedupe_key: Mapped[str] = mapped_column(String(255))

    # What we actually sent, so the briefing can avoid re-treading ground.
    headline: Mapped[str | None] = mapped_column(Text)
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
