"""Central configuration.

Everything runs on Groq's free tier — chat, tool calling, Whisper
transcription and vision. There is no paid API anywhere in this project.

Optional integrations degrade gracefully: if a key is missing the
corresponding capability switches itself off and Atlas says so in plain
language rather than throwing at the user.
"""

from __future__ import annotations

import re
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# Telegram accepts only these characters in `secret_token`, 1-256 long.
# Render's `generateValue` produces base64-ish strings containing "+/=", which
# Telegram rejects outright — so the value is normalised before use. Both the
# webhook registration and the request-time comparison run through this, so
# the two always agree.
_SECRET_ALLOWED = re.compile(r"[^A-Za-z0-9_-]")


def sanitize_webhook_secret(raw: str) -> str:
    cleaned = _SECRET_ALLOWED.sub("", (raw or "").strip())[:256]
    return cleaned or "atlas-dev-secret"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- required ------------------------------------------------------------
    telegram_bot_token: str = ""
    groq_api_key: str = ""

    # --- deployment ----------------------------------------------------------
    # Usually left blank: Render injects RENDER_EXTERNAL_URL into every web
    # service, and `base_url` below falls back to it. Set this explicitly only
    # when self-hosting or tunnelling (ngrok) locally.
    public_base_url: str = ""
    render_external_url: str = ""
    telegram_webhook_secret: str = "atlas-dev-secret"
    database_url: str = ""
    port: int = 8000

    # --- optional integrations ----------------------------------------------
    finnhub_api_key: str = ""

    # OpenRouter — used ONLY for image reading, because Groq's free tier has
    # no vision model. Free key, free models. Without it, Atlas says plainly
    # that it can't see images rather than guessing at a chart.
    openrouter_api_key: str = ""
    sec_user_agent: str = "Atlas Financial Assistant contact@example.com"

    # --- models (all free on Groq) -------------------------------------------
    # Reasoning + tool calling.
    #
    # Chosen by measurement, not reputation: llama-3.3-70b-versatile failed
    # tool calling on the simplest case in this project's tool set (17 tools),
    # returning `tool_use_failed` with malformed Llama-style function syntax.
    # gpt-oss-120b passed every case at 0.6-0.9s. Tool reliability is
    # load-bearing here — an assistant that can't fetch a price is useless.
    atlas_model: str = "openai/gpt-oss-120b"

    # Background extraction, relevance scoring, summaries. Measured faster and
    # cleaner at JSON than llama-3.1-8b-instant.
    atlas_fast_model: str = "openai/gpt-oss-20b"

    # Vision runs on OpenRouter (see openrouter_api_key above). Free tier.
    atlas_vision_model: str = "meta-llama/llama-3.2-11b-vision-instruct:free"

    # Voice notes.
    atlas_whisper_model: str = "whisper-large-v3-turbo"

    # --- tuning --------------------------------------------------------------
    log_level: str = "INFO"

    # Alerts below this score (0-1) are never sent. Silence is a feature.
    proactive_relevance_threshold: float = 0.62

    # How much raw conversation we keep verbatim before rolling it into a
    # running summary. The binding constraint is Groq's 8,000 tokens/MINUTE
    # free-tier ceiling, not the model's context window — history is re-sent
    # on every call, so a long window quietly throttles the whole bot.
    max_verbatim_turns: int = 6

    # Free-tier rate limits are per-minute. This caps how many Groq calls can
    # be in flight at once across the whole process, so a briefing sweep can't
    # starve a live conversation.
    max_concurrent_llm_calls: int = 4

    # ------------------------------------------------------------------ derived
    @property
    def webhook_secret(self) -> str:
        """The secret as Telegram will actually see it.

        Two failure modes are handled here. An empty value in `.env` would
        override the default with "", and Telegram sends no header at all —
        every update would then 403 and the bot would look offline. And a
        Render-generated value can contain characters Telegram rejects, which
        makes registration fail outright. Normalising in one place keeps
        registration and verification in agreement.
        """
        return sanitize_webhook_secret(self.telegram_webhook_secret)

    @property
    def sqlalchemy_url(self) -> str:
        """Normalise whatever the host gives us into an async driver URL."""
        raw = self.database_url.strip()
        if not raw:
            return "sqlite+aiosqlite:///./atlas.db"
        # Render/Heroku hand out `postgres://`, which SQLAlchemy 2 rejects.
        if raw.startswith("postgres://"):
            raw = raw.replace("postgres://", "postgresql://", 1)
        if raw.startswith("postgresql://"):
            raw = raw.replace("postgresql://", "postgresql+asyncpg://", 1)
        return raw

    @property
    def base_url(self) -> str:
        """Public HTTPS base URL of this service.

        Render sets RENDER_EXTERNAL_URL automatically on every web service.
        Relying on that first is far more robust than a `render.yaml`
        self-reference, which resolves to an empty string — and an empty base
        URL means no webhook is registered and the bot silently never
        receives a single message.
        """
        return (self.public_base_url or self.render_external_url).strip().rstrip("/")

    @property
    def webhook_url(self) -> str:
        return f"{self.base_url}/telegram/webhook"

    @property
    def voice_enabled(self) -> bool:
        # Whisper is on Groq, so voice comes free with the main key.
        return bool(self.groq_api_key)

    @property
    def vision_enabled(self) -> bool:
        # Vision is a separate provider; without its key the feature is off.
        return bool(self.openrouter_api_key and self.atlas_vision_model)

    @property
    def finnhub_enabled(self) -> bool:
        return bool(self.finnhub_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
