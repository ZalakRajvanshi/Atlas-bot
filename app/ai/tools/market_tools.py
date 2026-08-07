"""Market, news and filings tools.

Handlers return plain dicts. Every one that carries market data also carries
`as_of`, because the persona requires Atlas to timestamp anything it quotes.
"""

from __future__ import annotations

import asyncio

from app.ai.tools.base import Tool, ToolContext, norm_ticker, obj
from app.data import edgar, market, news


async def _resolve(ctx: ToolContext, raw: str) -> str | None:
    """Accept a ticker or a company name and return a usable ticker."""
    candidate = norm_ticker(raw)
    if not candidate:
        return None
    # Short all-caps input is almost certainly already a symbol.
    if len(candidate) <= 5 and candidate.isalpha():
        ctx.touched_tickers.add(candidate)
        return candidate
    hit = await market.resolve_symbol(raw)
    if hit and hit.get("ticker"):
        ctx.touched_tickers.add(hit["ticker"].upper())
        return hit["ticker"].upper()
    return None


# =============================================================================
# Handlers
# =============================================================================


async def h_resolve_company(ctx: ToolContext, args: dict) -> dict:
    hit = await market.resolve_symbol(args["query"])
    if not hit:
        return {"found": False, "query": args["query"]}
    ctx.touched_tickers.add(hit["ticker"].upper())
    return {"found": True, **hit}


async def h_get_quote(ctx: ToolContext, args: dict) -> dict:
    ticker = await _resolve(ctx, args["ticker"])
    if not ticker:
        return {"error": f"Could not resolve '{args['ticker']}' to a ticker."}
    quote = await market.get_quote(ticker)
    if not quote:
        return {"error": f"No price data available for {ticker}."}
    return quote


async def h_get_company(ctx: ToolContext, args: dict) -> dict:
    """Profile + fundamentals together — the model almost always wants both."""
    ticker = await _resolve(ctx, args["ticker"])
    if not ticker:
        return {"error": f"Could not resolve '{args['ticker']}' to a ticker."}

    profile, fundamentals, quote = await asyncio.gather(
        market.get_profile(ticker),
        market.get_fundamentals(ticker),
        market.get_quote(ticker),
        return_exceptions=True,
    )
    out: dict = {"ticker": ticker}
    if isinstance(profile, dict) and profile:
        out["profile"] = profile
    if isinstance(fundamentals, dict) and fundamentals:
        out["fundamentals"] = fundamentals
    if isinstance(quote, dict) and quote:
        out["quote"] = quote
    if len(out) == 1:
        return {"error": f"No company data available for {ticker}."}
    return out


async def h_get_news(ctx: ToolContext, args: dict) -> dict:
    scope = (args.get("ticker") or "").strip()
    days = int(args.get("days", 7))
    limit = int(args.get("limit", 6))

    if not scope or scope.lower() in ("market", "general", "macro"):
        items = await news.get_market_news(limit=limit)
        return {"scope": "market", "articles": items, "count": len(items)}

    ticker = await _resolve(ctx, scope)
    if not ticker:
        return {"error": f"Could not resolve '{scope}' to a ticker."}
    items = await news.get_company_news(ticker, days=days, limit=limit)
    return {"scope": ticker, "articles": items, "count": len(items)}


async def h_get_performance(ctx: ToolContext, args: dict) -> dict:
    ticker = await _resolve(ctx, args["ticker"])
    if not ticker:
        return {"error": f"Could not resolve '{args['ticker']}' to a ticker."}
    data = await market.get_performance(ticker, period=args.get("period", "1mo"))
    return data or {"error": f"No history available for {ticker}."}


async def h_market_snapshot(ctx: ToolContext, args: dict) -> dict:
    indexes = await market.get_market_snapshot()
    return {"indexes": indexes, "count": len(indexes)}


async def h_get_earnings(ctx: ToolContext, args: dict) -> dict:
    ticker = await _resolve(ctx, args["ticker"])
    if not ticker:
        return {"error": f"Could not resolve '{args['ticker']}' to a ticker."}
    data = await market.get_earnings_calendar(ticker)
    return data or {"error": f"No earnings date found for {ticker}."}


async def h_get_filings(ctx: ToolContext, args: dict) -> dict:
    ticker = await _resolve(ctx, args["ticker"])
    if not ticker:
        return {"error": f"Could not resolve '{args['ticker']}' to a ticker."}
    forms = args.get("forms") or None
    filings = await edgar.get_recent_filings(ticker, forms=forms, limit=int(args.get("limit", 8)))
    if not filings:
        return {"error": f"No SEC filings found for {ticker} (may be non-US listed)."}
    return {"ticker": ticker, "filings": filings, "count": len(filings)}


async def h_get_reported_financials(ctx: ToolContext, args: dict) -> dict:
    ticker = await _resolve(ctx, args["ticker"])
    if not ticker:
        return {"error": f"Could not resolve '{args['ticker']}' to a ticker."}
    facts = await edgar.get_company_facts(ticker)
    return facts or {
        "error": f"No XBRL financial data on EDGAR for {ticker}."
    }


# =============================================================================
# Definitions
# =============================================================================

TOOLS = [
    Tool(
        name="resolve_company",
        description='Map a company name to its ticker. Use before fetching data when given a name.',
        input_schema=obj(
            {"query": {"type": "string"}},
            ["query"],
        ),
        handler=h_resolve_company,
    ),
    Tool(
        name="get_quote",
        description='Current price, day change and range. Accepts ticker or company name. Always cite the returned as_of time.',
        input_schema=obj(
            {"ticker": {"type": "string"}},
            ["ticker"],
        ),
        handler=h_get_quote,
    ),
    Tool(
        name="get_company",
        description="Profile + valuation multiples + margins + growth + analyst targets + quote, in one call. Default for 'tell me about X' and any comparison.",
        input_schema=obj(
            {"ticker": {"type": "string"}},
            ["ticker"],
        ),
        handler=h_get_company,
    ),
    Tool(
        name="get_news",
        description="Recent news. Pass a ticker/company, or 'market' for broad news. Synthesise; never list headlines.",
        input_schema=obj(
            {
                "ticker": {"type": "string"},
                "days": {"type": "integer"},
                "limit": {"type": "integer"},
            }
        ),
        handler=h_get_news,
    ),
    Tool(
        name="get_performance",
        description="Price change over a period (start, end, %, high, low). For 'how has X done since...'.",
        input_schema=obj(
            {
                "ticker": {"type": "string"},
                "period": {
                    "type": "string",
                    "enum": ["5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "ytd"],
                    },
            },
            ["ticker"],
        ),
        handler=h_get_performance,
    ),
    Tool(
        name="get_market_snapshot",
        description='S&P 500, Nasdaq, Dow, VIX and US 10Y levels. For market-wide context and briefings.',
        input_schema=obj({}),
        handler=h_market_snapshot,
    ),
    Tool(
        name="get_earnings",
        description='Next earnings date, plus consensus and actual EPS/revenue when available.',
        input_schema=obj({"ticker": {"type": "string"}}, ["ticker"]),
        handler=h_get_earnings,
    ),
    Tool(
        name="get_sec_filings",
        description="Recent SEC filings with links. Authoritative — prefer over news for what a company actually disclosed. Filter e.g. ['10-K'], ['8-K'], ['4'].",
        input_schema=obj(
            {
                "ticker": {"type": "string"},
                "forms": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer"},
            },
            ["ticker"],
        ),
        handler=h_get_filings,
    ),
    Tool(
        name="get_reported_financials",
        description='Multi-year revenue, income, assets, equity, cash as reported in SEC filings (XBRL). Use when exact reported figures matter.',
        input_schema=obj({"ticker": {"type": "string"}}, ["ticker"]),
        handler=h_get_reported_financials,
    ),
]
