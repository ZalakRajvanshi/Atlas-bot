"""Update routing.

Every modality — text, voice, photo, document — converges on the same agent
turn. There is no command parser and no menu: `/start` is treated as "the user
just opened the bot", not as a command, because Telegram sends it
automatically and the user never chose to type it.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import agent
from app.ai.client import quick_text, read_image
from app.config import settings
from app.db import repo, session_scope
from app.db.models import MessageRole, OnboardingStage, User
from app.documents import ingest
from app.memory import service as memory
from app.telegram import client as tg
from app.voice import transcribe

log = logging.getLogger(__name__)

MAX_DOC_BYTES = 20 * 1024 * 1024
SUPPORTED_DOC_SUFFIXES = (".pdf", ".docx", ".doc", ".txt", ".md", ".csv")

GREETING = (
    "I'm Atlas — I follow markets so you don't have to read twelve tabs before "
    "your first meeting.\n\n"
    "Tell me what you're watching right now: a company, a sector, a position "
    "you're unsure about. I'll show you what I actually do."
)

VOICE_UNAVAILABLE = (
    "Type it and I'm all yours - voice transcription isn't switched on for me "
    "right now."
)

UNSUPPORTED_DOC = (
    "I can read PDFs, Word documents and plain text. Send me one of those — "
    "annual reports, earnings decks and research notes are what I'm best at."
)


# =============================================================================
# Entry point
# =============================================================================


async def handle_update(update: dict) -> None:
    """Route one Telegram update. Never raises — the webhook must always 200."""
    message = update.get("message") or update.get("edited_message")
    if not message:
        return

    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    chat_id = chat.get("id")
    if not chat_id or sender.get("is_bot"):
        return

    try:
        async with session_scope() as db:
            user, created = await repo.get_or_create_user(
                db,
                telegram_id=sender.get("id"),
                first_name=sender.get("first_name"),
                username=sender.get("username"),
            )
            await _dispatch(db, user, chat_id, message, created)
    except Exception:  # noqa: BLE001
        log.exception("Failed handling update")
        await tg.send_message(
            chat_id,
            "Something broke on my end. Try that again — I'll be here.",
        )


async def _dispatch(
    db: AsyncSession, user: User, chat_id: int, message: dict, created: bool
) -> None:
    text = (message.get("text") or message.get("caption") or "").strip()

    # Telegram sends /start automatically on first open. Treat it as an
    # arrival, not a command.
    if text.startswith("/start"):
        await _handle_start(db, user, chat_id)
        return

    if message.get("voice") or message.get("audio"):
        await _handle_voice(db, user, chat_id, message)
        return

    if message.get("document"):
        await _handle_document(db, user, chat_id, message, text)
        return

    if message.get("photo"):
        await _handle_photo(db, user, chat_id, message, text)
        return

    if text:
        await _converse(db, user, chat_id, text)
        return

    if message.get("sticker") or message.get("video") or message.get("video_note"):
        await tg.send_message(
            chat_id,
            "I work with text, voice notes, images and documents. What are you "
            "looking at?",
        )


# =============================================================================
# Modalities
# =============================================================================


async def _handle_start(db: AsyncSession, user: User, chat_id: int) -> None:
    if user.onboarding_stage == OnboardingStage.NEW:
        await repo.set_onboarding_stage(db, user, OnboardingStage.OPENING_ASKED)
        await repo.add_message(db, user.id, MessageRole.ASSISTANT, GREETING)
        await tg.send_message(chat_id, GREETING)
        return

    # A returning user tapping start should get continuity, not a reset.
    await _converse(
        db,
        user,
        chat_id,
        "[The user just reopened the chat. Greet them briefly, referencing "
        "something specific from what you know about them, and ask what they "
        "need. One or two sentences.]",
        persist_user_message=False,
    )


async def _handle_voice(
    db: AsyncSession, user: User, chat_id: int, message: dict
) -> None:
    if not settings.voice_enabled:
        await tg.send_message(chat_id, VOICE_UNAVAILABLE)
        return

    payload = message.get("voice") or message.get("audio")
    async with tg.TypingIndicator(chat_id):
        audio = await tg.download_file(payload["file_id"])
        if not audio:
            await tg.send_message(chat_id, "That voice note didn't come through.")
            return

        text = await transcribe.transcribe(audio)

    if not text:
        await tg.send_message(
            chat_id, "I couldn't make that out. Try again, or type it?"
        )
        return

    await _converse(db, user, chat_id, text, modality="voice")


async def _handle_photo(
    db: AsyncSession, user: User, chat_id: int, message: dict, caption: str
) -> None:
    """Images take a vision hop, then enter the normal turn as text.

    The reasoning model has no vision, so a vision model transcribes what is
    actually in the image and the analysis happens on the stronger model with
    full tool access — better than trading tool calling for multimodality.
    """
    question = caption or "What am I looking at, and what matters about it?"

    if not settings.vision_enabled:
        # Honest degradation. Atlas can still help — it just can't see, and
        # pretending otherwise would mean guessing at numbers on a chart.
        await tg.send_message(
            chat_id,
            "Tell me the ticker and what you're looking at, and I'll pull the "
            "real numbers myself - or send the underlying document and I'll "
            "read that properly. Image reading isn't switched on for me yet.",
        )
        return

    # Telegram sends several resolutions; the last is the largest.
    photo = (message.get("photo") or [])[-1]

    async with tg.TypingIndicator(chat_id):
        data = await tg.download_file(photo["file_id"], max_bytes=8 * 1024 * 1024)
        if not data:
            await tg.send_message(chat_id, "That image didn't come through.")
            return

        reading = await read_image(data, question)
        if not reading:
            await tg.send_message(
                chat_id,
                "I couldn't read that image. If it's a chart, a clearer crop "
                "usually does it — or just tell me what's on it.",
            )
            return

        prompt = (
            f"[The user sent an image. A vision model transcribed it as "
            f"follows — treat this as what is actually on screen, and verify "
            f"any figure you rely on with your tools before quoting it.]\n\n"
            f"{reading}\n\n"
            f"Their question: {question}"
        )
        reply = await agent.run_turn(db, user, prompt)

    await _finish_turn(
        db,
        user,
        chat_id,
        user_text=f"[sent an image] {caption}".strip(),
        reply=reply,
        modality="photo",
    )


async def _handle_document(
    db: AsyncSession, user: User, chat_id: int, message: dict, caption: str
) -> None:
    doc = message["document"]
    filename = doc.get("file_name") or "document"

    if not filename.lower().endswith(SUPPORTED_DOC_SUFFIXES):
        await tg.send_message(chat_id, UNSUPPORTED_DOC)
        return

    if (doc.get("file_size") or 0) > MAX_DOC_BYTES:
        await tg.send_message(
            chat_id,
            "That file's over 20MB — Telegram won't let me fetch it. Send a "
            "smaller version or the section you care about.",
        )
        return

    async with tg.TypingIndicator(chat_id):
        data = await tg.download_file(doc["file_id"], max_bytes=MAX_DOC_BYTES)
        if not data:
            await tg.send_message(chat_id, "I couldn't download that one.")
            return

        try:
            pages = await asyncio.to_thread(ingest.extract, filename, data)
        except Exception:  # noqa: BLE001
            log.exception("Extraction failed for %s", filename)
            pages = []

        if not pages:
            await tg.send_message(
                chat_id,
                "I couldn't pull any text out of that — it may be a scanned "
                "image rather than a text PDF. If you have a text version, "
                "send that instead.",
            )
            return

        chunks = ingest.chunk_pages(pages)
        sample = "\n".join(text for _, text in pages[:3])[:8000]

        stored = await repo.add_document(
            db,
            user_id=user.id,
            filename=filename,
            chunks=chunks,
            title=filename.rsplit(".", 1)[0].replace("_", " ").strip(),
            doc_type=ingest.guess_doc_type(filename, sample),
            ticker=ingest.guess_ticker(sample),
            page_count=len(pages),
        )

        # A one-line orientation summary, generated once, so future turns and
        # the briefing know what this document is without re-reading it.
        stored.summary = await quick_text(
            system=(
                "Summarise what this financial document IS in one sentence: type, "
                "company, period. No analysis, no preamble."
            ),
            user=f"Filename: {filename}\n\n{sample[:4000]}",
            max_tokens=150,
        )

        prompt = (
            f"[The user just uploaded '{filename}' "
            f"({len(pages)} pages, document id {stored.id}). "
            f"Read the key parts with search_document and open with what "
            f"actually matters in it — not a description of its structure. "
            f"Then invite a specific question.]"
        )
        if caption:
            prompt += f"\n\nThey said: {caption}"

        reply = await agent.run_turn(db, user, prompt)

    await _finish_turn(
        db,
        user,
        chat_id,
        user_text=f"[uploaded {filename}] {caption}".strip(),
        reply=reply,
        modality="document",
    )


# =============================================================================
# Conversation core
# =============================================================================


async def _converse(
    db: AsyncSession,
    user: User,
    chat_id: int,
    text: str,
    *,
    modality: str = "text",
    persist_user_message: bool = True,
) -> None:
    async with tg.TypingIndicator(chat_id):
        reply = await agent.run_turn(db, user, text)

    await _finish_turn(
        db,
        user,
        chat_id,
        user_text=text if persist_user_message else None,
        reply=reply,
        modality=modality,
    )


async def _finish_turn(
    db: AsyncSession,
    user: User,
    chat_id: int,
    *,
    user_text: str | None,
    reply: str,
    modality: str,
) -> None:
    """Persist the exchange, send it, then learn from it."""
    if user_text:
        await repo.add_message(db, user.id, MessageRole.USER, user_text, modality)
    await repo.add_message(db, user.id, MessageRole.ASSISTANT, reply)

    if user.onboarding_stage != OnboardingStage.ACTIVE:
        await repo.set_onboarding_stage(db, user, OnboardingStage.ACTIVE)

    await tg.send_message(chat_id, reply)

    # Learning happens after the user has their answer — it must never add
    # latency to the reply. Committed here because the caller's session_scope
    # closes as soon as this returns.
    #
    # Short acknowledgements ("thanks", "ok", "got it") carry nothing durable,
    # and every extraction pass is a model call against a per-minute quota —
    # so they are skipped rather than spent.
    if user_text and _worth_learning_from(user_text):
        try:
            await memory.learn_from_exchange(db, user, user_text, reply)
            await memory.maybe_summarize(db, user)
        except Exception:  # noqa: BLE001
            log.exception("Post-turn learning failed")


TRIVIAL = {
    "thanks", "thank you", "thanks!", "ty", "ok", "okay", "k", "cool", "nice",
    "got it", "sure", "yes", "no", "yep", "nope", "hi", "hey", "hello", "yo",
    "great", "perfect", "awesome", "np", "sounds good",
}


def _worth_learning_from(text: str) -> bool:
    cleaned = text.strip().lower().rstrip("!.?")
    if cleaned in TRIVIAL:
        return False
    # Anything this short is an acknowledgement, not information.
    return len(cleaned.split()) >= 3
