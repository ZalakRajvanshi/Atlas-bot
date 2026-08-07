"""Tool registry.

Order is fixed and deterministic — the tool list renders at position 0 of every
request, so a stable order keeps the prompt cache intact across turns.
"""

from __future__ import annotations

from app.ai.tools import doc_tools, market_tools, memory_tools
from app.ai.tools.base import Tool, ToolContext

ALL_TOOLS: list[Tool] = [
    *market_tools.TOOLS,
    *memory_tools.TOOLS,
    *doc_tools.TOOLS,
]

REGISTRY: dict[str, Tool] = {t.name: t for t in ALL_TOOLS}

TOOL_SCHEMAS: list[dict] = [t.schema() for t in ALL_TOOLS]

__all__ = ["ALL_TOOLS", "REGISTRY", "TOOL_SCHEMAS", "Tool", "ToolContext"]
