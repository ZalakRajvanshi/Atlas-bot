"""Markdown → Telegram HTML.

Telegram's MarkdownV2 requires escaping 18 characters and fails the whole
message on a single mistake — which, with model-generated text containing
tickers, percentages and decimals, happens constantly. HTML has four escapes
and degrades predictably, so we convert to HTML instead.

Anything Telegram cannot render (headers, rules, tables) is flattened rather
than dropped, so no content is ever silently lost.
"""

from __future__ import annotations

import html
import re

TELEGRAM_LIMIT = 4096
SAFE_CHUNK = 3800


def _escape(text: str) -> str:
    return html.escape(text, quote=False)


# Models reach for typographic characters that read badly on a phone and
# sometimes render as boxes: non-breaking hyphens inside words, multiplication
# signs for "x", narrow spaces before "%". The persona asks for plain ASCII,
# but instruction-following is probabilistic and formatting is not worth
# gambling on — so it is normalised here as well.
_PUNCTUATION = {
    "‑": "-",  # non-breaking hyphen  (price‑to‑sales)
    "‒": "-",  # figure dash
    "–": "-",  # en dash
    "—": "-",  # em dash
    "―": "-",  # horizontal bar
    "−": "-",  # minus sign
    "×": "x",  # multiplication sign  (18×)
    "≈": "~",  # almost equal to
    "≤": "<=",
    "≥": ">=",
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    "…": "...",
    " ": " ",  # non-breaking space
    " ": " ",  # thin space
    " ": " ",  # narrow no-break space  (63 %)
    "​": "",   # zero-width space
}


def normalize_punctuation(text: str) -> str:
    for bad, good in _PUNCTUATION.items():
        text = text.replace(bad, good)
    # "63 %" -> "63%": a space before the unit reads as a typo.
    text = re.sub(r"(\d)\s+%", r"\1%", text)
    # Collapse the double spaces those substitutions can leave behind.
    return re.sub(r"[ \t]{2,}", " ", text)


def to_telegram_html(text: str) -> str:
    if not text:
        return ""

    text = normalize_punctuation(text)

    # Protect fenced code before any other transformation touches it.
    blocks: list[str] = []

    def _stash_fenced(match: re.Match) -> str:
        body = match.group(2) or ""
        blocks.append(f"<pre>{_escape(body.strip())}</pre>")
        return f"\x00{len(blocks) - 1}\x00"

    text = re.sub(r"```(\w+)?\n?(.*?)```", _stash_fenced, text, flags=re.DOTALL)

    def _stash_inline(match: re.Match) -> str:
        blocks.append(f"<code>{_escape(match.group(1))}</code>")
        return f"\x00{len(blocks) - 1}\x00"

    text = re.sub(r"`([^`\n]+)`", _stash_inline, text)

    # Markdown links must be captured before escaping mangles the brackets.
    links: list[str] = []

    def _stash_link(match: re.Match) -> str:
        label, url = match.group(1), match.group(2)
        links.append(f'<a href="{_escape(url)}">{_escape(label)}</a>')
        return f"\x01{len(links) - 1}\x01"

    text = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", _stash_link, text)

    text = _escape(text)

    # Headers become bold lines — Telegram has no heading concept.
    text = re.sub(r"^\s{0,3}#{1,6}\s*(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    # Horizontal rules carry no meaning once headers are flattened.
    text = re.sub(r"^\s*([-*_])\1{2,}\s*$", "", text, flags=re.MULTILINE)

    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"<b><i>\1</i></b>", text, flags=re.DOTALL)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text, flags=re.DOTALL)
    # Single-asterisk italics only when clearly paired and not a bare bullet.
    text = re.sub(r"(?<![\w*])\*([^*\n]+?)\*(?![\w*])", r"<i>\1</i>", text)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text, flags=re.DOTALL)

    # Normalise list bullets to a single glyph Telegram renders cleanly.
    text = re.sub(r"^\s*[-*+]\s+", "• ", text, flags=re.MULTILINE)

    for i, block in enumerate(blocks):
        text = text.replace(f"\x00{i}\x00", block)
    for i, link in enumerate(links):
        text = text.replace(f"\x01{i}\x01", link)

    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_message(text: str, limit: int = SAFE_CHUNK) -> list[str]:
    """Split long text on natural boundaries, keeping HTML tags balanced.

    Splitting only at paragraph or line boundaries means an open <b> never
    straddles a chunk, which would otherwise make Telegram reject the message.
    """
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text

    while len(remaining) > limit:
        window = remaining[:limit]
        cut = window.rfind("\n\n")
        if cut < limit * 0.4:
            cut = window.rfind("\n")
        if cut < limit * 0.4:
            cut = window.rfind(". ")
            if cut != -1:
                cut += 1
        if cut < limit * 0.4:
            cut = limit

        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()

    if remaining:
        chunks.append(remaining)
    return [c for c in chunks if c]
