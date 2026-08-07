"""Voice note transcription.

Telegram delivers voice messages as OGG/Opus, which Whisper accepts directly —
no ffmpeg step, which keeps the Render image small and the deploy simple.

Whisper runs on Groq under the same free key as everything else, so voice
support costs nothing and needs no second provider.
"""

from __future__ import annotations

import logging

from app.ai.client import transcribe_audio

log = logging.getLogger(__name__)


async def transcribe(audio: bytes, filename: str = "voice.ogg") -> str | None:
    return await transcribe_audio(audio, filename)
