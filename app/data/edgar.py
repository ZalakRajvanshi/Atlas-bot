"""SEC EDGAR — primary-source filings.

Free, no key, and authoritative. Including EDGAR is a deliberate credibility
choice: when Atlas says "per the 10-K filed 2026-02-14" it is citing the
document itself rather than a journalist's paraphrase, which is exactly the
standard a finance professional applies.

SEC requires a descriptive User-Agent and asks for <10 req/s.
"""

from __future__ import annotations

import logging

from app.config import settings
from app.data import http
from app.data.cache import cached

log = logging.getLogger(__name__)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
FILING_INDEX = "https://www.sec.gov/cgi-bin/browse-edgar"

FORM_MEANING = {
    "10-K": "annual report",
    "10-Q": "quarterly report",
    "8-K": "material event disclosure",
    "DEF 14A": "proxy statement",
    "S-1": "IPO registration",
    "4": "insider transaction",
    "13F-HR": "institutional holdings",
    "SC 13D": "activist stake (>5%)",
    "SC 13G": "passive stake (>5%)",
    "424B4": "prospectus",
    "6-K": "foreign issuer report",
    "20-F": "foreign annual report",
}


def _headers() -> dict[str, str]:
    return {
        "User-Agent": settings.sec_user_agent,
        "Accept-Encoding": "gzip, deflate",
    }


async def _ticker_to_cik_map() -> dict[str, str]:
    async def produce() -> dict[str, str]:
        try:
            client = http.get_client("sec", timeout=20.0, headers=_headers())
            r = await client.get(TICKERS_URL)
            r.raise_for_status()
            data = r.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("EDGAR ticker map failed: %s", exc)
            return {}
        # Shape: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}
        return {
            row["ticker"].upper(): str(row["cik_str"]).zfill(10)
            for row in data.values()
            if row.get("ticker")
        }

    # The map is ~10k entries and changes rarely; cache for a day.
    return await cached("edgar:tickermap", 86_400.0, produce)


async def get_cik(ticker: str) -> str | None:
    return (await _ticker_to_cik_map()).get(ticker.upper().strip())


async def get_recent_filings(
    ticker: str, forms: list[str] | None = None, limit: int = 10
) -> list[dict]:
    """Most recent filings, newest first.

    `forms` filters to specific types, e.g. ["10-K", "10-Q"] or ["8-K"].
    """
    ticker = ticker.upper().strip()
    cik = await get_cik(ticker)
    if not cik:
        return []

    key = f"edgar:filings:{cik}:{','.join(forms or [])}:{limit}"

    async def produce() -> list[dict]:
        try:
            client = http.get_client("sec", timeout=20.0, headers=_headers())
            r = await client.get(SUBMISSIONS_URL.format(cik=cik))
            r.raise_for_status()
            data = r.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("EDGAR submissions failed for %s: %s", ticker, exc)
            return []

        recent = (data.get("filings") or {}).get("recent") or {}
        form_list = recent.get("form", [])
        if not form_list:
            return []

        wanted = {f.upper() for f in forms} if forms else None
        out: list[dict] = []

        for i, form in enumerate(form_list):
            if wanted and form.upper() not in wanted:
                continue
            accession = recent["accessionNumber"][i]
            acc_plain = accession.replace("-", "")
            primary = recent["primaryDocument"][i]
            out.append(
                {
                    "ticker": ticker,
                    "company": data.get("name"),
                    "form": form,
                    "meaning": FORM_MEANING.get(form.upper(), form),
                    "filed_at": recent["filingDate"][i],
                    "report_period": recent.get("reportDate", [None] * len(form_list))[i]
                    or None,
                    "accession": accession,
                    "description": recent.get("primaryDocDescription", [""] * len(form_list))[i]
                    or None,
                    "url": (
                        f"https://www.sec.gov/Archives/edgar/data/"
                        f"{int(cik)}/{acc_plain}/{primary}"
                    ),
                    "source": "SEC EDGAR",
                }
            )
            if len(out) >= limit:
                break
        return out

    return await cached(key, 1_800.0, produce)


async def get_company_facts(ticker: str) -> dict | None:
    """Headline XBRL financials straight from filings.

    Preferred over aggregator fundamentals when a user asks something that
    must be exact — these are the numbers the company actually reported.
    """
    cik = await get_cik(ticker)
    if not cik:
        return None

    async def produce() -> dict | None:
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        try:
            client = http.get_client("sec", timeout=30.0, headers=_headers())
            r = await client.get(url)
            r.raise_for_status()
            data = r.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("EDGAR companyfacts failed for %s: %s", ticker, exc)
            return None

        gaap = (data.get("facts") or {}).get("us-gaap") or {}
        wanted = {
            "Revenues": "revenue",
            "RevenueFromContractWithCustomerExcludingAssessedTax": "revenue",
            "NetIncomeLoss": "net_income",
            "OperatingIncomeLoss": "operating_income",
            "Assets": "total_assets",
            "Liabilities": "total_liabilities",
            "StockholdersEquity": "equity",
            "CashAndCashEquivalentsAtCarryingValue": "cash",
            "ResearchAndDevelopmentExpense": "r_and_d",
        }

        out: dict = {"ticker": ticker.upper(), "company": data.get("entityName")}
        for tag, label in wanted.items():
            node = gaap.get(tag)
            if not node:
                continue
            usd = (node.get("units") or {}).get("USD") or []
            # Annual figures only (10-K), most recent first.
            annual = [
                u for u in usd if u.get("form") == "10-K" and u.get("fp") == "FY"
            ]
            if not annual:
                continue
            annual.sort(key=lambda u: u.get("end", ""), reverse=True)
            if label in out:
                continue
            out[label] = [
                {
                    "period_end": u.get("end"),
                    "fiscal_year": u.get("fy"),
                    "value": u.get("val"),
                }
                for u in annual[:4]
            ]

        out["source"] = "SEC EDGAR XBRL"
        return out if len(out) > 3 else None

    return await cached(f"edgar:facts:{cik}", 86_400.0, produce)
