"""Retrieval over uploaded documents.

Deliberately BM25 rather than embeddings. For single-document Q&A at this
scale BM25 is competitive, costs nothing, adds no API dependency, and — most
usefully for finance — matches exact tokens like "10-K", "FY2025", "Item 1A"
that embeddings tend to blur. Financial questions are unusually keyword-shaped.

Short documents skip retrieval entirely and are passed whole to the model,
which is strictly better when it fits.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repo
from app.db.models import Document, DocumentChunk

log = logging.getLogger(__name__)

# Below this, the whole document goes into context — no retrieval needed.
WHOLE_DOC_CHARS = 40_000

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "for", "on", "at",
    "by", "with", "from", "is", "are", "was", "were", "be", "been", "it", "its",
    "this", "that", "these", "those", "as", "what", "which", "who", "how", "do",
    "does", "did", "can", "could", "would", "should", "about", "me", "my", "i",
}


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokens, keeping finance-shaped ones intact.

    Hyphens and periods inside tokens are preserved so "10-K", "FY2025" and
    "1A" survive as single terms.
    """
    raw = re.findall(r"[a-zA-Z0-9][a-zA-Z0-9\-\.]*", text.lower())
    return [t.strip(".-") for t in raw if len(t.strip(".-")) > 1 and t not in STOPWORDS]


async def search_chunks(
    db: AsyncSession, document: Document, query: str, top_k: int = 6
) -> list[DocumentChunk]:
    from rank_bm25 import BM25Okapi

    chunks = await repo.get_chunks(db, document.id)
    if not chunks:
        return []
    if len(chunks) <= top_k:
        return chunks

    corpus = [tokenize(c.text) for c in chunks]
    query_tokens = tokenize(query)
    if not query_tokens:
        return chunks[:top_k]

    bm25 = BM25Okapi(corpus)
    scores = bm25.get_scores(query_tokens)

    ranked = sorted(zip(chunks, scores), key=lambda p: p[1], reverse=True)[:top_k]
    # Nothing matched — fall back to the opening chunks, which in filings and
    # decks carry the summary and are a reasonable default.
    if all(score <= 0 for _, score in ranked):
        return chunks[:top_k]

    # Re-order by position so the model reads the document in document order.
    return [c for c, _ in sorted(ranked, key=lambda p: p[0].ordinal)]


def format_passages(document: Document, chunks: list[DocumentChunk]) -> str:
    lines = [f'From "{document.title or document.filename}":']
    for chunk in chunks:
        label = f"[page {chunk.page}]" if chunk.page else f"[section {chunk.ordinal + 1}]"
        lines.append(f"\n{label}\n{chunk.text}")
    return "\n".join(lines)


async def whole_document_text(db: AsyncSession, document: Document) -> str:
    chunks = await repo.get_chunks(db, document.id)
    return "\n\n".join(c.text for c in chunks)
