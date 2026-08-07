"""Document tools.

The interesting one is `cross_reference_document`: it connects an uploaded
filing to *today's* news. A 10-K's risk factors are hypothetical until one of
them starts happening — noticing that is analyst work, and it is only possible
because Atlas holds both the document and live data at once.
"""

from __future__ import annotations

import logging

from app.ai.tools.base import Tool, ToolContext, norm_ticker, obj
from app.data import news
from app.db import repo
from app.db.models import utcnow
from app.documents import retrieve

log = logging.getLogger(__name__)


async def _pick_document(ctx: ToolContext, document_id: int | None):
    docs = await repo.get_documents(ctx.db, ctx.user.id)
    if not docs:
        return None, docs
    if document_id:
        match = next((d for d in docs if d.id == int(document_id)), None)
        return match, docs
    # No id given — the most recent upload is almost always what they mean.
    return docs[0], docs


async def h_list_documents(ctx: ToolContext, args: dict) -> dict:
    docs = await repo.get_documents(ctx.db, ctx.user.id)
    return {
        "count": len(docs),
        "documents": [
            {
                "document_id": d.id,
                "title": d.title,
                "type": d.doc_type,
                "ticker": d.ticker,
                "pages": d.page_count,
                "uploaded": d.created_at.strftime("%Y-%m-%d"),
                "summary": d.summary,
            }
            for d in docs
        ],
    }


async def h_search_document(ctx: ToolContext, args: dict) -> dict:
    doc, all_docs = await _pick_document(ctx, args.get("document_id"))
    if not doc:
        return {
            "error": "No documents uploaded yet."
            if not all_docs
            else "That document id does not exist."
        }

    doc.last_used_at = utcnow()
    query = args["query"]

    # Small enough to read in full — strictly better than retrieving fragments.
    if doc.char_count <= retrieve.WHOLE_DOC_CHARS:
        text = await retrieve.whole_document_text(ctx.db, doc)
        return {
            "document_id": doc.id,
            "title": doc.title,
            "mode": "full_document",
            "content": text,
        }

    chunks = await retrieve.search_chunks(
        ctx.db, doc, query, top_k=int(args.get("top_k", 6))
    )
    if not chunks:
        return {"error": "That document has no extractable text."}

    return {
        "document_id": doc.id,
        "title": doc.title,
        "mode": "retrieved_passages",
        "query": query,
        "passages": retrieve.format_passages(doc, chunks),
    }


async def h_cross_reference(ctx: ToolContext, args: dict) -> dict:
    """Pull document passages and current news on the same subject together."""
    doc, _ = await _pick_document(ctx, args.get("document_id"))
    if not doc:
        return {"error": "No documents uploaded yet."}

    topic = args["topic"]
    chunks = await retrieve.search_chunks(ctx.db, doc, topic, top_k=5)

    ticker = norm_ticker(args.get("ticker")) or doc.ticker
    articles = []
    if ticker:
        ctx.touched_tickers.add(ticker)
        articles = await news.get_company_news(ticker, days=30, limit=6)

    return {
        "document_id": doc.id,
        "title": doc.title,
        "topic": topic,
        "document_passages": retrieve.format_passages(doc, chunks) if chunks else None,
        "current_news": articles,
        "instruction": (
            "Compare what the document says against what is happening now. Say "
            "explicitly whether current events support, contradict, or have "
            "already overtaken the document's position."
        ),
    }


TOOLS = [
    Tool(
        name="list_documents",
        description="List uploaded documents with ids and summaries. Use when they say 'the report' and you need to identify it.",
        input_schema=obj({}),
        handler=h_list_documents,
    ),
    Tool(
        name="search_document",
        description='Answer a question from an uploaded document. Omit document_id for the most recent. Cite page numbers when present.',
        input_schema=obj(
            {
                "query": {"type": "string"},
                "document_id": {"type": "integer"},
            },
            ["query"],
        ),
        handler=h_search_document,
    ),
    Tool(
        name="cross_reference_document",
        description="Check an uploaded document against current reality — pulls its passages AND recent news. For 'are these risks playing out?' / 'is this still accurate?'.",
        input_schema=obj(
            {
                "topic": {"type": "string"},
                "document_id": {"type": "integer"},
                "ticker": {"type": "string"},
            },
            ["topic"],
        ),
        handler=h_cross_reference,
    ),
]
