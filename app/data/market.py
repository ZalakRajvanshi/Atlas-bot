"""Market data.

Provider strategy: Finnhub when a key is present (real-time, reliable
fundamentals), yfinance otherwise. yfinance is synchronous and occasionally
slow, so every call is pushed to a worker thread.

Everything returns plain dicts with an explicit `as_of` timestamp. Atlas is
required to surface that timestamp to the user — quoting a stale price as if
it were live is the fastest way to lose a finance professional's trust.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.data import http
from app.data.cache import cached

log = logging.getLogger(__name__)

FINNHUB = "https://finnhub.io/api/v1"

QUOTE_TTL = 45.0
PROFILE_TTL = 21_600.0    # company descriptions barely change
FUNDAMENTAL_TTL = 3_600.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def _finnhub_get(path: str, params: dict[str, Any]) -> dict | None:
    if not settings.finnhub_enabled:
        return None
    try:
        client = http.get_client("finnhub", timeout=12.0)
        r = await client.get(
            f"{FINNHUB}{path}", params={**params, "token": settings.finnhub_api_key}
        )
        if r.status_code == 429:
            log.warning("Finnhub rate limited on %s", path)
            return None
        r.raise_for_status()
        return r.json()
    except Exception as exc:  # noqa: BLE001 — provider failure must not break chat
        log.warning("Finnhub %s failed: %s", path, exc)
        return None


# =============================================================================
# yfinance (runs in a thread — it is blocking)
# =============================================================================


def _fi(fast_info, *keys):
    """Read a fast_info field without letting one bad key sink the quote.

    yfinance's FastInfo resolves lazily and *raises* on missing upstream data
    (KeyError, JSONDecodeError, UnknownTimeZoneError) rather than returning
    None — so a single unavailable field like `currency` would otherwise
    discard an otherwise-good price. Each key is isolated.
    """
    for key in keys:
        try:
            value = fast_info.get(key)
        except Exception:  # noqa: BLE001
            continue
        if value is not None:
            return value
    return None


def _yf_close_prices(ticker: str) -> tuple[float, float] | None:
    """Last two closes via history() — the most reliable yfinance path.

    Used when fast_info is unavailable (Yahoo rate-limits aggressively), so a
    throttled fast_info degrades to a slightly staler price instead of no
    price at all.
    """
    import yfinance as yf

    try:
        df = yf.Ticker(ticker).history(period="5d", interval="1d")
        if df is None or df.empty:
            return None
        closes = df["Close"].dropna()
        if closes.empty:
            return None
        last = float(closes.iloc[-1])
        prev = float(closes.iloc[-2]) if len(closes) > 1 else last
        return last, prev
    except Exception:  # noqa: BLE001
        return None


def _yf_quote_sync(ticker: str) -> dict | None:
    import yfinance as yf

    price = prev = None
    currency = market_cap = day_high = day_low = None

    try:
        fi = yf.Ticker(ticker).fast_info
        price = _fi(fi, "lastPrice", "last_price")
        prev = _fi(fi, "previousClose", "previous_close")
        currency = _fi(fi, "currency")
        market_cap = _fi(fi, "marketCap", "market_cap")
        day_high = _fi(fi, "dayHigh", "day_high")
        day_low = _fi(fi, "dayLow", "day_low")
    except Exception as exc:  # noqa: BLE001
        log.warning("yfinance fast_info unavailable for %s: %s", ticker, exc)

    if price is None or prev is None:
        fallback = _yf_close_prices(ticker)
        if fallback:
            price = price if price is not None else fallback[0]
            prev = prev if prev is not None else fallback[1]

    if price is None:
        log.warning("yfinance could not price %s", ticker)
        return None

    change = (price - prev) if prev else None
    pct = (change / prev * 100) if (change is not None and prev) else None
    return {
        "ticker": ticker.upper(),
        "price": round(float(price), 2),
        "previous_close": round(float(prev), 2) if prev else None,
        "change": round(float(change), 2) if change is not None else None,
        "change_pct": round(float(pct), 2) if pct is not None else None,
        "currency": currency or "USD",
        "market_cap": market_cap,
        "day_high": day_high,
        "day_low": day_low,
        "source": "yfinance",
    }


def _yf_profile_sync(ticker: str) -> dict | None:
    import yfinance as yf

    try:
        info = yf.Ticker(ticker).info or {}
        if not info.get("longName") and not info.get("shortName"):
            return None
        return {
            "ticker": ticker.upper(),
            "name": info.get("longName") or info.get("shortName"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "country": info.get("country"),
            "employees": info.get("fullTimeEmployees"),
            "website": info.get("website"),
            "summary": (info.get("longBusinessSummary") or "")[:1500] or None,
            "market_cap": info.get("marketCap"),
            "source": "yfinance",
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("yfinance profile failed for %s: %s", ticker, exc)
        return None


def _yf_fundamentals_sync(ticker: str) -> dict | None:
    import yfinance as yf

    try:
        info = yf.Ticker(ticker).info or {}
        if not info:
            return None
        keys = {
            "trailing_pe": "trailingPE",
            "forward_pe": "forwardPE",
            "peg_ratio": "pegRatio",
            "price_to_book": "priceToBook",
            "price_to_sales": "priceToSalesTrailing12Months",
            "profit_margin": "profitMargins",
            "operating_margin": "operatingMargins",
            "gross_margin": "grossMargins",
            "revenue_growth": "revenueGrowth",
            "earnings_growth": "earningsGrowth",
            "return_on_equity": "returnOnEquity",
            "debt_to_equity": "debtToEquity",
            "current_ratio": "currentRatio",
            "free_cashflow": "freeCashflow",
            "total_revenue": "totalRevenue",
            "ebitda": "ebitda",
            "beta": "beta",
            "dividend_yield": "dividendYield",
            "52w_high": "fiftyTwoWeekHigh",
            "52w_low": "fiftyTwoWeekLow",
            "target_mean_price": "targetMeanPrice",
            "recommendation": "recommendationKey",
            "analyst_count": "numberOfAnalystOpinions",
        }
        out = {k: info.get(v) for k, v in keys.items()}
        out = {k: v for k, v in out.items() if v is not None}
        out["ticker"] = ticker.upper()
        out["source"] = "yfinance"
        return out or None
    except Exception as exc:  # noqa: BLE001
        log.warning("yfinance fundamentals failed for %s: %s", ticker, exc)
        return None


def _yf_history_sync(ticker: str, period: str, interval: str) -> dict | None:
    import yfinance as yf

    try:
        df = yf.Ticker(ticker).history(period=period, interval=interval)
        if df is None or df.empty:
            return None
        closes = df["Close"].dropna()
        if closes.empty:
            return None
        first, last = float(closes.iloc[0]), float(closes.iloc[-1])
        return {
            "ticker": ticker.upper(),
            "period": period,
            "start_price": round(first, 2),
            "end_price": round(last, 2),
            "change_pct": round((last - first) / first * 100, 2) if first else None,
            "high": round(float(closes.max()), 2),
            "low": round(float(closes.min()), 2),
            "points": len(closes),
            "source": "yfinance",
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("yfinance history failed for %s: %s", ticker, exc)
        return None


# =============================================================================
# Public API
# =============================================================================


async def get_quote(ticker: str) -> dict | None:
    ticker = ticker.upper().strip()

    async def produce() -> dict | None:
        fh = await _finnhub_get("/quote", {"symbol": ticker})
        if fh and fh.get("c"):
            price, prev = float(fh["c"]), float(fh.get("pc") or 0)
            return {
                "ticker": ticker,
                "price": round(price, 2),
                "previous_close": round(prev, 2) if prev else None,
                "change": round(float(fh.get("d") or 0), 2),
                "change_pct": round(float(fh.get("dp") or 0), 2),
                "day_high": fh.get("h"),
                "day_low": fh.get("l"),
                "currency": "USD",
                "as_of": _now_iso(),
                "source": "finnhub",
            }
        data = await asyncio.to_thread(_yf_quote_sync, ticker)
        if data:
            data["as_of"] = _now_iso()
        return data

    return await cached(f"quote:{ticker}", QUOTE_TTL, produce)


async def get_profile(ticker: str) -> dict | None:
    ticker = ticker.upper().strip()

    async def produce() -> dict | None:
        fh = await _finnhub_get("/stock/profile2", {"symbol": ticker})
        if fh and fh.get("name"):
            base = {
                "ticker": ticker,
                "name": fh.get("name"),
                "industry": fh.get("finnhubIndustry"),
                "country": fh.get("country"),
                "website": fh.get("weburl"),
                "market_cap": (fh.get("marketCapitalization") or 0) * 1_000_000 or None,
                "ipo": fh.get("ipo"),
                "exchange": fh.get("exchange"),
                "source": "finnhub",
            }
            # Finnhub has no business description; borrow yfinance's.
            yf_data = await asyncio.to_thread(_yf_profile_sync, ticker)
            if yf_data:
                base.setdefault("sector", yf_data.get("sector"))
                base["summary"] = yf_data.get("summary")
                base["employees"] = yf_data.get("employees")
            base["as_of"] = _now_iso()
            return base

        data = await asyncio.to_thread(_yf_profile_sync, ticker)
        if data:
            data["as_of"] = _now_iso()
        return data

    return await cached(f"profile:{ticker}", PROFILE_TTL, produce)


# Finnhub's /stock/metric returns ~100 fields; these are the ones an analyst
# actually reaches for, mapped to the same names the yfinance path produces so
# the model sees one consistent shape regardless of provider.
_FINNHUB_METRICS = {
    "peTTM": "trailing_pe",
    "peBasicExclExtraTTM": "trailing_pe",
    "forwardPE": "forward_pe",
    "pbAnnual": "price_to_book",
    "psTTM": "price_to_sales",
    "netProfitMarginTTM": "profit_margin",
    "operatingMarginTTM": "operating_margin",
    "grossMarginTTM": "gross_margin",
    "revenueGrowthTTMYoy": "revenue_growth",
    "epsGrowthTTMYoy": "earnings_growth",
    "roeTTM": "return_on_equity",
    "totalDebt/totalEquityAnnual": "debt_to_equity",
    "currentRatioAnnual": "current_ratio",
    "revenuePerShareTTM": "revenue_per_share",
    "beta": "beta",
    "dividendYieldIndicatedAnnual": "dividend_yield",
    "52WeekHigh": "52w_high",
    "52WeekLow": "52w_low",
}


async def get_fundamentals(ticker: str) -> dict | None:
    """Valuation multiples, margins and growth.

    Finnhub first when a key is present: Yahoo rate-limits aggressively and
    returns 429 for extended periods, which would otherwise leave the model
    with no multiples at all and force it to say "not pulled" mid-answer.
    """
    ticker = ticker.upper().strip()

    async def produce() -> dict | None:
        fh = await _finnhub_get("/stock/metric", {"symbol": ticker, "metric": "all"})
        metrics = (fh or {}).get("metric") or {}
        if metrics:
            out: dict = {"ticker": ticker, "source": "finnhub"}
            for src, dest in _FINNHUB_METRICS.items():
                value = metrics.get(src)
                if value is not None and dest not in out:
                    out[dest] = round(value, 4) if isinstance(value, float) else value
            if len(out) > 4:
                out["as_of"] = _now_iso()
                return out

        data = await asyncio.to_thread(_yf_fundamentals_sync, ticker)
        if data:
            data["as_of"] = _now_iso()
        return data

    return await cached(f"fund:{ticker}", FUNDAMENTAL_TTL, produce)


async def get_performance(
    ticker: str, period: str = "1mo", interval: str = "1d"
) -> dict | None:
    ticker = ticker.upper().strip()

    async def produce() -> dict | None:
        data = await asyncio.to_thread(_yf_history_sync, ticker, period, interval)
        if data:
            data["as_of"] = _now_iso()
        return data

    return await cached(f"hist:{ticker}:{period}:{interval}", FUNDAMENTAL_TTL, produce)


# --- index / macro context ---------------------------------------------------

INDEXES = {
    "^GSPC": "S&P 500",
    "^IXIC": "Nasdaq Composite",
    "^DJI": "Dow Jones",
    "^VIX": "VIX (volatility)",
    "^TNX": "US 10Y Treasury yield",
}


async def get_market_snapshot() -> list[dict]:
    """Index-level context for briefings and 'how are markets doing' questions."""

    async def produce() -> list[dict]:
        results = await asyncio.gather(
            *[get_quote(sym) for sym in INDEXES], return_exceptions=True
        )
        out = []
        for sym, res in zip(INDEXES, results):
            if isinstance(res, dict) and res:
                out.append({**res, "name": INDEXES[sym]})
        return out

    return await cached("market:snapshot", 120.0, produce)


# --- earnings calendar -------------------------------------------------------


async def get_earnings_calendar(ticker: str) -> dict | None:
    """Next scheduled earnings date, when we can determine it."""
    ticker = ticker.upper().strip()

    async def produce() -> dict | None:
        fh = await _finnhub_get(
            "/calendar/earnings", {"symbol": ticker}
        )
        if fh and fh.get("earningsCalendar"):
            rows = fh["earningsCalendar"]
            today = datetime.now(timezone.utc).date().isoformat()
            upcoming = [r for r in rows if (r.get("date") or "") >= today]
            row = (upcoming or rows)[0]
            return {
                "ticker": ticker,
                "date": row.get("date"),
                "eps_estimate": row.get("epsEstimate"),
                "eps_actual": row.get("epsActual"),
                "revenue_estimate": row.get("revenueEstimate"),
                "revenue_actual": row.get("revenueActual"),
                "hour": row.get("hour"),
                "is_upcoming": bool(upcoming),
                "source": "finnhub",
                "as_of": _now_iso(),
            }

        def _sync() -> dict | None:
            import yfinance as yf

            try:
                cal = yf.Ticker(ticker).calendar
                if cal is None:
                    return None
                dates = None
                if isinstance(cal, dict):
                    dates = cal.get("Earnings Date")
                elif hasattr(cal, "loc") and "Earnings Date" in getattr(cal, "index", []):
                    dates = cal.loc["Earnings Date"].tolist()
                if not dates:
                    return None
                first = dates[0] if isinstance(dates, list) else dates
                return {
                    "ticker": ticker,
                    "date": str(first)[:10],
                    "is_upcoming": True,
                    "source": "yfinance",
                }
            except Exception:  # noqa: BLE001
                return None

        data = await asyncio.to_thread(_sync)
        if data:
            data["as_of"] = _now_iso()
        return data

    return await cached(f"earn:{ticker}", FUNDAMENTAL_TTL, produce)


# --- symbol resolution -------------------------------------------------------


async def resolve_symbol(query: str) -> dict | None:
    """Map 'Nvidia' / 'nvda' / 'the chip company' → a tradable ticker.

    Used when the model has a company name but no symbol. Kept forgiving:
    finance professionals type fast and rarely capitalise.
    """
    q = query.strip()
    if not q:
        return None

    async def produce() -> dict | None:
        fh = await _finnhub_get("/search", {"q": q})
        if fh and fh.get("result"):
            # Prefer plain common stock on a US exchange.
            for row in fh["result"]:
                if row.get("type") in ("Common Stock", "") and "." not in (
                    row.get("symbol") or "."
                ):
                    return {
                        "ticker": row["symbol"],
                        "name": row.get("description"),
                        "source": "finnhub",
                    }
            row = fh["result"][0]
            return {
                "ticker": row.get("symbol"),
                "name": row.get("description"),
                "source": "finnhub",
            }

        # Fallback: treat the input as a symbol and see if it prices.
        candidate = q.upper().replace(" ", "")
        if len(candidate) <= 6:
            quote = await get_quote(candidate)
            if quote:
                prof = await get_profile(candidate)
                return {
                    "ticker": candidate,
                    "name": (prof or {}).get("name"),
                    "source": "inferred",
                }
        return None

    return await cached(f"resolve:{q.lower()}", PROFILE_TTL, produce)
