"""Tool plumbing.

A tool is a JSON schema plus an async handler. Handlers receive a `ToolContext`
carrying the DB session and the current user, so they can read and write memory
without the model having to pass identifiers around.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User

log = logging.getLogger(__name__)


@dataclass
class ToolContext:
    db: AsyncSession
    user: User
    # Tickers touched this turn — used afterwards to keep the watchlist's
    # "last discussed" timestamps fresh for relevance scoring.
    touched_tickers: set[str] = field(default_factory=set)


Handler = Callable[[ToolContext, dict[str, Any]], Awaitable[Any]]


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Handler

    def schema(self) -> dict:
        """OpenAI-style function schema, which is what Groq expects."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


def obj(properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
    }


def norm_ticker(value: str | None) -> str:
    return (value or "").upper().strip()
