"""Google Sheets — read-only, via the public CSV export.

Deliberately no OAuth. A link-shared sheet is available as CSV at a
predictable URL, so reading one is a fetch rather than a consent flow:

    https://docs.google.com/spreadsheets/d/{id}/export?format=csv&gid={gid}

That choice has a real cost — private sheets don't work — and a real benefit:
a judge or a user can paste a link and get an answer in one step, with no
account linking, no permissions screen, and nothing stored. For a finance
assistant that mostly needs to *read* a model or a holdings list, that trade
is the right way round.

Gmail, Drive and Calendar are a different proposition (genuine OAuth, token
refresh, app verification) and are deliberately not implemented.
"""

from __future__ import annotations

import csv
import io
import logging
import re

from app.data import http

log = logging.getLogger(__name__)

# Accepts the full edit URL, a share link, or a bare ID.
_SHEET_ID = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")
_GID = re.compile(r"[#&?]gid=([0-9]+)")

EXPORT_URL = "https://docs.google.com/spreadsheets/d/{sid}/export?format=csv"

MAX_ROWS = 200
MAX_COLS = 30
MAX_CELL = 200


def parse_link(text: str) -> tuple[str, str | None] | None:
    """Pull (sheet_id, gid) out of anything the user pasted.

    `gid` is None when the link doesn't name a tab. That distinction matters:
    a sheet's first tab is only gid=0 if it has never been recreated,
    renamed or reordered, and requesting a gid that doesn't exist returns
    400. Omitting the parameter entirely gives you the first tab, whatever
    its id happens to be.
    """
    match = _SHEET_ID.search(text)
    if not match:
        # A bare ID, if it looks like one.
        candidate = text.strip()
        if 30 <= len(candidate) <= 60 and re.fullmatch(r"[a-zA-Z0-9-_]+", candidate):
            return candidate, None
        return None

    gid_match = _GID.search(text)
    return match.group(1), (gid_match.group(1) if gid_match else None)


def looks_like_sheet_link(text: str) -> bool:
    return "docs.google.com/spreadsheets" in text


async def fetch_sheet(sheet_id: str, gid: str | None = None) -> dict:
    """Download a sheet as CSV and return rows plus a light profile."""
    base = EXPORT_URL.format(sid=sheet_id)
    # A named tab is tried first, then the default tab — a stale or copied gid
    # 400s, and falling back beats telling the user their sheet is private
    # when it isn't.
    urls = [f"{base}&gid={gid}", base] if gid else [base]

    r = None
    try:
        client = http.get_client("gsheets", timeout=30.0)
        for url in urls:
            r = await client.get(url)
            if r.status_code == 200 and "text/csv" in r.headers.get("content-type", ""):
                break
    except Exception as exc:  # noqa: BLE001
        log.warning("Sheet fetch failed: %s", exc)
        return {"error": "Could not reach Google Sheets just then."}

    # Google answers a permission failure with an HTML sign-in page, not a 403,
    # so the content type is the only reliable signal that access was refused.
    content_type = r.headers.get("content-type", "")
    if r.status_code != 200 or "text/csv" not in content_type:
        return {
            "error": (
                "That sheet isn't publicly readable. In Google Sheets: Share -> "
                "General access -> 'Anyone with the link' -> Viewer, then send "
                "the link again. Nothing is stored and no account is connected."
            )
        }

    try:
        text = r.content.decode("utf-8-sig", errors="replace")
        rows = list(csv.reader(io.StringIO(text)))
    except Exception as exc:  # noqa: BLE001
        log.warning("Sheet parse failed: %s", exc)
        return {"error": "That sheet didn't parse as a table."}

    rows = [r for r in rows if any(cell.strip() for cell in r)]
    if not rows:
        return {"error": "That sheet looks empty."}

    total_rows = len(rows)
    truncated = total_rows > MAX_ROWS
    rows = rows[:MAX_ROWS]
    rows = [[cell[:MAX_CELL] for cell in row[:MAX_COLS]] for row in rows]

    header, *body = rows

    # Real spreadsheets end in a TOTAL / SUM line. Left in the sample it
    # skews every column and gets flagged as an anomaly, which is both wrong
    # and the first thing a reader would notice you got wrong.
    totals = [r for r in body if _is_total_row(r)]
    body = [r for r in body if not _is_total_row(r)]

    result = {
        "sheet_id": sheet_id,
        "columns": header,
        "rows": body,
        "total_row": totals[0] if totals else None,
        "row_count": total_rows - 1,
        "rows_shown": len(body),
        "truncated": truncated,
        "numeric_summary": _summarise(header, body),
    }

    concentration = _concentration(header, body)
    if concentration:
        result["concentration"] = concentration

    return result


_TOTAL_LABELS = {"total", "totals", "sum", "grand total", "net", "overall"}


def _is_total_row(row: list[str]) -> bool:
    """A summary line rather than a data row."""
    for cell in row[:2]:
        label = re.sub(r"[^a-z ]", "", cell.strip().lower()).strip()
        if label in _TOTAL_LABELS:
            return True
    return False


def _to_number(value: str) -> float | None:
    """Parse a spreadsheet cell into a number.

    Handles currency symbols, thousands separators, percentages and
    parenthesised negatives — the shapes finance spreadsheets actually use.
    """
    cleaned = value.strip()
    if not cleaned:
        return None

    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()")
    cleaned = re.sub(r"[₹$€£¥,\s]", "", cleaned)

    percent = cleaned.endswith("%")
    cleaned = cleaned.rstrip("%")

    try:
        number = float(cleaned)
    except ValueError:
        return None

    if negative:
        number = -number
    return number / 100 if percent else number


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _outliers(values: list[float], threshold: float = 3.5) -> list[float]:
    """Flag anomalies using the median absolute deviation.

    Standard deviation is the obvious choice and the wrong one here: a single
    extreme value inflates the spread enough to hide itself. A portfolio of
    6-9% positions with one 62% position — exactly the case worth flagging —
    goes undetected at 2.5 sigma, because that one holding drags the sigma up
    past its own distance from the mean.

    MAD is computed around the median, so outliers barely move it and remain
    visible. This is the standard robust alternative (modified z-score).
    """
    if len(values) < 4:
        return []

    median = _median(values)
    deviations = [abs(v - median) for v in values]
    mad = _median(deviations)

    if mad > 0:
        scores = [(0.6745 * (v - median) / mad, v) for v in values]
    else:
        # Every value identical bar a few: anything off the median is an outlier.
        mean = sum(values) / len(values)
        spread = (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5
        if spread == 0:
            return []
        scores = [((v - mean) / spread, v) for v in values]

    return [round(v, 4) for score, v in scores if abs(score) > threshold]


def _summarise(header: list[str], body: list[list[str]]) -> dict:
    """Per-column min/max/mean plus obvious outliers.

    Computed here rather than left to the model: arithmetic over a hundred
    rows is exactly what a language model does unreliably, and 'spot the
    anomaly' is the question these sheets get asked.
    """
    summary: dict = {}

    for index, name in enumerate(header):
        values = []
        for row in body:
            if index < len(row):
                number = _to_number(row[index])
                if number is not None:
                    values.append(number)

        # A column is numeric only if most of it parses.
        if len(values) < 3 or len(values) < len(body) * 0.6:
            continue

        mean = sum(values) / len(values)

        column: dict = {
            "min": round(min(values), 4),
            "max": round(max(values), 4),
            "mean": round(mean, 4),
            "median": round(_median(values), 4),
            "count": len(values),
        }

        outliers = _outliers(values)
        if outliers:
            column["outliers"] = outliers[:5]
            column["note"] = "far from the median relative to the rest of the column"

        summary[name or f"column_{index + 1}"] = column

    return summary


# =============================================================================
# Concentration — the question these sheets are actually asked
# =============================================================================

# Real holdings sheets label the same sector two ways, because the rows came
# from two brokers. "Technology" and "IT" sitting apart make a 39% exposure
# read as 29% and 10%, which is precisely the concentration someone would
# want flagged and precisely what eyeballing the column misses.
_SECTOR_ALIASES = {
    "technology": "Technology",
    "tech": "Technology",
    "information technology": "Technology",
    "info tech": "Technology",
    "infotech": "Technology",
    "it": "Technology",
    "it services": "Technology",
    "software": "Technology",
    "semiconductors": "Technology",
    "financials": "Financials",
    "financial": "Financials",
    "financial services": "Financials",
    "finance": "Financials",
    "banking": "Financials",
    "banks": "Financials",
    "bfsi": "Financials",
    "insurance": "Financials",
    "energy": "Energy",
    "oil gas": "Energy",
    "oil and gas": "Energy",
    "oil  gas": "Energy",
    "petroleum": "Energy",
    "healthcare": "Healthcare",
    "health care": "Healthcare",
    "pharma": "Healthcare",
    "pharmaceutical": "Healthcare",
    "pharmaceuticals": "Healthcare",
    "consumer discretionary": "Consumer Discretionary",
    "consumer disc": "Consumer Discretionary",
    "consumer cyclical": "Consumer Discretionary",
    "consumer cyclicals": "Consumer Discretionary",
    "discretionary": "Consumer Discretionary",
    "consumer staples": "Consumer Staples",
    "consumer defensive": "Consumer Staples",
    "staples": "Consumer Staples",
    "fmcg": "Consumer Staples",
    "communication services": "Communication Services",
    "communications": "Communication Services",
    "telecom": "Communication Services",
    "telecommunications": "Communication Services",
    "media": "Communication Services",
    "industrials": "Industrials",
    "industrial": "Industrials",
    "capital goods": "Industrials",
    "manufacturing": "Industrials",
    "materials": "Materials",
    "basic materials": "Materials",
    "metals": "Materials",
    "mining": "Materials",
    "chemicals": "Materials",
    "utilities": "Utilities",
    "utility": "Utilities",
    "power": "Utilities",
    "real estate": "Real Estate",
    "realty": "Real Estate",
    "reit": "Real Estate",
    "reits": "Real Estate",
}

_WEIGHT_NAMES = re.compile(r"weight|alloc|share|%|pct|percent", re.I)
_VALUE_NAMES = re.compile(r"value|amount|market|mkt|invested|cost|holding", re.I)


def _canonical(label: str) -> str:
    key = re.sub(r"[^a-z ]", " ", label.strip().lower())
    key = re.sub(r"\s+", " ", key).strip()
    return _SECTOR_ALIASES.get(key, label.strip())


def _label_column(header: list[str], body: list[list[str]]) -> int | None:
    """The column that groups rows — sector, category, bucket.

    Wants repetition: a ticker or name column has one distinct value per row
    and groups nothing.
    """
    best: tuple[int, int] | None = None

    for index in range(len(header)):
        values = [
            row[index].strip() for row in body if index < len(row) and row[index].strip()
        ]
        if len(values) < len(body) * 0.7:
            continue
        if any(_to_number(v) is not None for v in values):
            continue

        distinct = {_canonical(v) for v in values}
        if not 2 <= len(distinct) <= min(12, len(values) * 0.7):
            continue

        # Fewer groups means a stronger grouping column.
        if best is None or len(distinct) < best[1]:
            best = (index, len(distinct))

    return best[0] if best else None


def _weight_column(header: list[str], body: list[list[str]]) -> tuple[int | None, str]:
    """The column to add up per group, and what it represents.

    A column that already sums to 100 is a weighting whatever it is called;
    otherwise fall back to a value column and convert to shares.
    """
    named_weight = named_value = summing_to_one = None

    for index, name in enumerate(header):
        values = [
            _to_number(row[index]) for row in body if index < len(row)
        ]
        values = [v for v in values if v is not None]
        if len(values) < len(body) * 0.6 or not values:
            continue
        if any(v < 0 for v in values):
            continue  # a P/L column is not a weighting

        total = sum(values)
        if 0.95 <= total <= 1.05 or 95 <= total <= 105:
            summing_to_one = summing_to_one if summing_to_one is not None else index
        if _WEIGHT_NAMES.search(name) and named_weight is None:
            named_weight = index
        elif _VALUE_NAMES.search(name) and named_value is None:
            named_value = index

    if summing_to_one is not None:
        return summing_to_one, "weight"
    if named_weight is not None:
        return named_weight, "weight"
    if named_value is not None:
        return named_value, "value"
    return None, "count"


def _concentration(header: list[str], body: list[list[str]]) -> dict | None:
    """Exact exposure per group, with equivalent labels merged.

    Left to the model this is thirteen additions and a judgement call about
    whether two labels mean the same thing — it will usually get the sum
    close and the merge wrong, and report a 39% position as 29%.
    """
    if len(body) < 3:
        return None

    label_index = _label_column(header, body)
    if label_index is None:
        return None

    weight_index, mode = _weight_column(header, body)

    groups: dict[str, float] = {}
    merged: dict[str, list[str]] = {}

    for row in body:
        if label_index >= len(row) or not row[label_index].strip():
            continue
        raw = row[label_index].strip()
        name = _canonical(raw)

        if mode == "count":
            amount = 1.0
        else:
            amount = (
                _to_number(row[weight_index]) if weight_index < len(row) else None
            ) or 0.0

        groups[name] = groups.get(name, 0.0) + amount
        seen = merged.setdefault(name, [])
        if raw not in seen:
            seen.append(raw)

    total = sum(groups.values())
    if not groups or total <= 0:
        return None

    shares = sorted(
        ((name, value / total) for name, value in groups.items()),
        key=lambda pair: pair[1],
        reverse=True,
    )

    result: dict = {
        "grouped_by": header[label_index] or f"column_{label_index + 1}",
        "basis": (
            f"{header[weight_index]} (exact)"
            if weight_index is not None and mode != "count"
            else "position count (no weight column found)"
        ),
        "shares": {name: f"{share * 100:.1f}%" for name, share in shares},
        "largest": f"{shares[0][0]} {shares[0][1] * 100:.1f}%",
    }

    if len(shares) >= 2:
        top_two = (shares[0][1] + shares[1][1]) * 100
        result["top_two_combined"] = f"{top_two:.1f}%"

    # Only interesting when it actually changed the picture.
    collapsed = {k: v for k, v in merged.items() if len(v) > 1}
    if collapsed:
        result["merged_labels"] = {k: " + ".join(v) for k, v in collapsed.items()}
        result["merge_note"] = (
            "These labels are the same sector written two ways and have been "
            "combined. The sheet shows them apart, so this exposure is larger "
            "than it looks on the page - say so."
        )

    return result
