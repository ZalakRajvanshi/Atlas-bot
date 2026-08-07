"""News retrieval.

Atlas never forwards headlines verbatim — the agent is instructed to explain
significance. This module's only job is getting clean, deduplicated,
timestamped items into the model's hands.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.data.cache import cached
from app.data.market import _finnhub_get, _now_iso

log = logging.getLogger(__name__)

NEWS_TTL = 600.0


def article_key(item: dict) -> str:
    """Stable fingerprint for dedupe across providers and across days."""
    basis = (item.get("url") or "") or (item.get("headline") or "")
    return hashlib.sha256(basis.strip().lower().encode()).hexdigest()[:24]


def _clean(items: list[dict], limit: int) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for it in items:
        if not it.get("headline"):
            continue
        k = article_key(it)
        if k in seen:
            continue
        seen.add(k)
        it["key"] = k
        out.append(it)
        if len(out) >= limit:
            break
    return out


def _yf_news_sync(ticker: str) -> list[dict]:
    import yfinance as yf

    try:
        raw = yf.Ticker(ticker).news or []
    except Exception as exc:  # noqa: BLE001
        log.warning("yfinance news failed for %s: %s", ticker, exc)
        return []

    items = []
    for n in raw:
        # yfinance changed shape across versions; handle both.
        c = n.get("content") or n
        headline = c.get("title") or n.get("title")
        if not headline:
            continue
        pub = c.get("pubDate") or n.get("providerPublishTime")
        if isinstance(pub, (int, float)):
            pub = datetime.fromtimestamp(pub, tz=timezone.utc).isoformat()
        url = (
            (c.get("canonicalUrl") or {}).get("url")
            if isinstance(c.get("canonicalUrl"), dict)
            else None
        ) or c.get("link") or n.get("link")
        provider = c.get("provider")
        source = (
            provider.get("displayName")
            if isinstance(provider, dict)
            else n.get("publisher")
        )
        items.append(
            {
                "headline": headline,
                "summary": (c.get("summary") or c.get("description") or "")[:600]
                or None,
                "url": url,
                "source": source or "Yahoo Finance",
                "published_at": str(pub) if pub else None,
                "ticker": ticker.upper(),
            }
        )
    return items


async def get_company_news(ticker: str, days: int = 7, limit: int = 8) -> list[dict]:
    ticker = ticker.upper().strip()

    async def produce() -> list[dict]:
        collected: list[dict] = []

        if settings.finnhub_enabled:
            today = datetime.now(timezone.utc).date()
            fh = await _finnhub_get(
                "/company-news",
                {
                    "symbol": ticker,
                    "from": (today - timedelta(days=days)).isoformat(),
                    "to": today.isoformat(),
                },
            )
            for n in (fh or [])[: limit * 2]:
                collected.append(
                    {
                        "headline": n.get("headline"),
                        "summary": (n.get("summary") or "")[:600] or None,
                        "url": n.get("url"),
                        "source": n.get("source"),
                        "published_at": datetime.fromtimestamp(
                            n.get("datetime", 0), tz=timezone.utc
                        ).isoformat()
                        if n.get("datetime")
                        else None,
                        "ticker": ticker,
                    }
                )

        if len(collected) < limit:
            collected.extend(await asyncio.to_thread(_yf_news_sync, ticker))

        return _clean(collected, limit)

    return await cached(f"news:{ticker}:{days}:{limit}", NEWS_TTL, produce)


async def get_market_news(limit: int = 10) -> list[dict]:
    """Broad market news for briefings."""

    async def produce() -> list[dict]:
        collected: list[dict] = []

        if settings.finnhub_enabled:
            fh = await _finnhub_get("/news", {"category": "general"})
            for n in (fh or [])[: limit * 2]:
                collected.append(
                    {
                        "headline": n.get("headline"),
                        "summary": (n.get("summary") or "")[:600] or None,
                        "url": n.get("url"),
                        "source": n.get("source"),
                        "published_at": datetime.fromtimestamp(
                            n.get("datetime", 0), tz=timezone.utc
                        ).isoformat()
                        if n.get("datetime")
                        else None,
                    }
                )

        if len(collected) < limit:
            # ^GSPC carries broad market stories through yfinance.
            for proxy in ("SPY", "^GSPC"):
                collected.extend(await asyncio.to_thread(_yf_news_sync, proxy))
                if len(collected) >= limit:
                    break

        return _clean(collected, limit)

    return await cached(f"news:market:{limit}", NEWS_TTL, produce)


async def get_news_for_tickers(
    tickers: list[str], days: int = 2, per_ticker: int = 3
) -> list[dict]:
    """Watchlist sweep — used by the briefing and monitor jobs."""
    if not tickers:
        return []
    results = await asyncio.gather(
        *[get_company_news(t, days=days, limit=per_ticker) for t in tickers],
        return_exceptions=True,
    )
    out: list[dict] = []
    for res in results:
        if isinstance(res, list):
            out.extend(res)
    return _clean(out, per_ticker * len(tickers))
