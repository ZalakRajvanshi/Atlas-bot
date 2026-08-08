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

EXPORT_URL = "https://docs.google.com/spreadsheets/d/{sid}/export?format=csv&gid={gid}"

MAX_ROWS = 200
MAX_COLS = 30
MAX_CELL = 200


def parse_link(text: str) -> tuple[str, str] | None:
    """Pull (sheet_id, gid) out of anything the user pasted."""
    match = _SHEET_ID.search(text)
    if not match:
        # A bare ID, if it looks like one.
        candidate = text.strip()
        if 30 <= len(candidate) <= 60 and re.fullmatch(r"[a-zA-Z0-9-_]+", candidate):
            return candidate, "0"
        return None

    gid_match = _GID.search(text)
    return match.group(1), (gid_match.group(1) if gid_match else "0")


def looks_like_sheet_link(text: str) -> bool:
    return "docs.google.com/spreadsheets" in text


async def fetch_sheet(sheet_id: str, gid: str = "0") -> dict:
    """Download a sheet as CSV and return rows plus a light profile."""
    url = EXPORT_URL.format(sid=sheet_id, gid=gid)

    try:
        client = http.get_client("gsheets", timeout=30.0)
        r = await client.get(url)
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

    return {
        "sheet_id": sheet_id,
        "columns": header,
        "rows": body,
        "row_count": total_rows - 1,
        "rows_shown": len(body),
        "truncated": truncated,
        "numeric_summary": _summarise(header, body),
    }


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
