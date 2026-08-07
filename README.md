# Atlas — AI Financial Assistant

A Telegram assistant that behaves like a buy-side analyst who works for you:
it remembers what you're watching, explains why things matter, and stays quiet
when nothing does.

> **Setup and deployment:** see [SETUP.md](SETUP.md).

---

## What makes this different

Most assistants of this shape are a bot piped into an LLM with a stock-quote
tool. Four decisions separate Atlas from that.

### 1. Memory that is load-bearing, not decorative

Remembering that you like Nvidia is a party trick. Remembering **why you
believe what you believe** — and telling you when that reason stops holding —
is a product.

When you say something directional, Atlas decomposes it into falsifiable
assumptions and stores it as a **thesis**:

```
You:   I'm long Nvidia — hyperscaler capex holds up through 2026.

Atlas stores:
  claim       "Long Nvidia — hyperscaler capex holds through 2026"
  assumptions ["MSFT/GOOGL/AMZN capex guidance stays flat or rises",
               "no credible competing accelerator ships at volume",
               "gross margin stays above 70%"]
```

A background job then checks incoming news against those assumptions. Weeks
later, unprompted:

> *Microsoft guided capex down 8% for next year. That's the assumption your
> Nvidia call rests on — worth a look before earnings.*

That message is the product. Everything else supports it.
See [`app/db/models.py`](app/db/models.py) → `Thesis`, and
[`app/jobs/monitor.py`](app/jobs/monitor.py) → `_check_thesis_divergence`.

### 2. Silence is an implemented feature

The brief says *"if there is nothing important to share, the assistant should
remain silent."* Almost nobody builds that, because silence looks like a bug.

Atlas scores every candidate notification against **your** profile, watchlist
and stated views, and drops anything below threshold — permanently. It also
fingerprints everything it has ever told you, so tomorrow's brief knows what
yesterday's said and never repeats it.

[`app/jobs/relevance.py`](app/jobs/relevance.py) ·
[`SentEvent`](app/db/models.py)

### 3. Onboarding that is a conversation, not a form

There is no questionnaire. Atlas asks **one** opening question, then does real
analytical work on whatever you say and learns the rest from ordinary
conversation. Two paths write to memory: tools the model calls consciously,
and a background extraction pass that catches what it didn't think to save.

[`app/memory/service.py`](app/memory/service.py)

### 4. Analyst reasoning, not summarisation

Every answer leads with the conclusion, gives the two or three things actually
driving it, and — when Atlas takes any kind of position — states **what would
change its mind**. Responses are capped near 120 words because the reader is
on a phone. The persona that enforces this is in
[`app/ai/prompts.py`](app/ai/prompts.py), which is the most product-dense file
in the repo.

---

## Capabilities

| | |
|---|---|
| **Conversation** | Natural language, full context across turns and sessions. No commands, no menus, no buttons. |
| **Research** | Quotes, fundamentals, multiples, margins, growth, analyst targets, performance, earnings dates |
| **Primary sources** | SEC EDGAR filings and as-reported XBRL financials — cited, with links |
| **News** | Synthesised into implications, never relayed as headlines |
| **Documents** | Upload a 10-K, deck or research note; ask questions; get page-cited answers |
| **Cross-reference** | Check an uploaded filing's risk factors against *today's* news |
| **Voice** | Send a voice note; Whisper is primed with finance vocabulary |
| **Images** | Photograph a chart or table and Atlas reads the actual numbers |
| **Daily brief** | Personalised, scheduled to your local time, skips what you already know |
| **Proactive** | Thesis divergence, price moves with causes, earnings, filings, custom alerts |

---

## Architecture

```
Telegram ──webhook──▶ FastAPI ──▶ handlers ──▶ agent loop ──▶ Llama 3.3 70B (Groq)
                         │                        │
                         │                        ├── market / news / EDGAR tools
                         │                        ├── memory / thesis / alert tools
                         │                        └── document tools (BM25)
                         │
                         ├── APScheduler ──▶ briefing · monitor · keep-alive
                         └── Postgres ─────▶ profile · facts · theses · docs · sent-events
```

```
app/
  main.py              FastAPI: webhook, health, admin triggers
  config.py            Settings; optional integrations degrade gracefully
  ai/
    prompts.py         The persona and background-task prompts  ← the product
    agent.py           Async tool-calling loop, parallel execution, rate-limit aware
    client.py          Groq wrapper; backoff, concurrency gate, voice + vision
    tools/             market · memory/thesis · documents
  data/                yfinance · Finnhub · SEC EDGAR · TTL cache
  db/                  models · repositories · async session
  documents/           PDF/DOCX ingest, page-tagged chunking, BM25 retrieval
  memory/              context assembly, fact extraction, summarisation
  telegram/            client, markdown→HTML, update routing
  jobs/                briefing · monitor · relevance gate · scheduler
  voice/               Whisper transcription
```

### Choices worth defending

**Manual agent loop over the SDK tool runner.** Handlers are async, share a DB
session and user context, and run concurrently within a turn. The runner's
per-tool synchronous model doesn't fit that shape.

**BM25 over embeddings for documents.** At single-document scale BM25 is
competitive, needs no API and no key, and matches exact tokens — `10-K`,
`FY2025`, `Item 1A` — that embeddings blur. Financial questions are unusually
keyword-shaped. Documents under 40k characters skip retrieval entirely and go
into context whole, which is strictly better when it fits.

**Runs entirely on free infrastructure.** Groq's free tier covers reasoning,
tool calling, Whisper transcription and vision behind one key; SEC EDGAR and
Yahoo Finance need no key at all. Nothing in this project bills.

**Free-tier limits are engineered around, not ignored.** The binding
constraint is 8,000 tokens per *minute*, and every call re-sends the persona
and all tool schemas — so a naive build spends its whole minute on one turn.
Schemas were cut to ~1,550 tokens, the persona to ~760, tool results capped at
2,200 chars, history to 6 turns, and the loop's final pass withholds tools so
the model must answer rather than fetch more. Measured: 98s → **6-20s** per
turn. Calls are capped at 4 concurrent so a briefing sweep can't starve a live
conversation, and 429s back off on the server's own `retry-after`.

**Honest degradation over silent guessing.** Groq's free tier currently has no
vision model, so Atlas tells you it can't read an image and offers to pull the
numbers itself — rather than inventing figures from a chart it never saw. The
same rule applies to every data source: a failure is reported as a failure.

**HTML over MarkdownV2 for Telegram.** MarkdownV2 needs 18 escape characters
and fails the whole message on one mistake — which, with tickers, percentages
and decimals in every reply, happens constantly.

**Every provider failure is non-fatal.** A dead data source produces an
honest "I couldn't retrieve that", never a crash and never a hallucinated
number.

---

## Accuracy

Financial answers are acted on, so the reliability rules are explicit:

- All market data carries an `as_of` timestamp, and Atlas is required to quote it
- A failed tool call is reported as a failure — never backfilled from training data
- SEC EDGAR is preferred over news when the question is what a company actually disclosed
- Verified facts and inferences are distinguished in the wording
- No price predictions, and no personalised investment advice

---

## Tech

Python 3.12 · FastAPI · SQLAlchemy 2 (async) · Postgres/SQLite · APScheduler ·
yfinance · Finnhub · SEC EDGAR · pypdf · rank-bm25

**All AI on Groq's free tier:** `gpt-oss-120b` (reasoning + tool calling) ·
`gpt-oss-20b` (background extraction) · Whisper v3 Turbo (voice).
**Total running cost: $0.**

Model choice was measured, not assumed: `llama-3.3-70b-versatile` returned
`tool_use_failed` on the simplest query against this project's 17-tool set,
emitting Llama-style function syntax the API rejects. `gpt-oss-120b` passed
every case at 0.6-0.9s. An assistant that cannot reliably fetch a price is
not an assistant.

---

## Licence

MIT
