# Getting Atlas live

Two keys are mandatory. **Both are free, and neither needs a card.**
Everything else is optional and Atlas degrades gracefully without it.

---

## 1. Telegram bot token — 2 minutes, free

1. Open Telegram, search **@BotFather**, send `/newbot`
2. Give it a display name (`Atlas`) and a username ending in `bot`
   (e.g. `atlas_analyst_bot`)
3. BotFather replies with a token like `8123456789:AAF...` — that's
   `TELEGRAM_BOT_TOKEN`

While you're there, make the bot look finished:

```
/setdescription   → Your AI financial analyst. Ask me anything about markets,
                    companies, earnings or your own documents.
/setabouttext     → AI financial analyst that remembers what you're watching.
/setuserpic       → (upload any clean logo image)
```

Do **not** set commands via `/setcommands` — Atlas is deliberately
conversational, and a command menu invites people to use it wrongly.

---

## 2. Groq API key — 2 minutes, free

1. Go to **console.groq.com** → sign in with Google or GitHub
2. **API Keys** → *Create API Key* → copy it into `GROQ_API_KEY`
3. That's it. No card, no billing page, no credit to add.

This one key powers reasoning + tool calling (`gpt-oss-120b`), background
extraction (`gpt-oss-20b`), and voice transcription (Whisper v3 Turbo).

### What "free" actually costs you — measured, not guessed

The binding limit is **8,000 tokens per minute**, not requests (the request
allowance is ~1,000 and never runs out in practice). That is tight, because
every call re-sends the system prompt and all 17 tool schemas.

Atlas is built around that ceiling:

| Lever | Setting |
|---|---|
| Tool schemas | trimmed to ~1,550 tokens (from 2,330) |
| Persona | ~760 tokens (from 1,245) |
| Tool results | capped at 2,200 chars before re-entering the prompt |
| History | 6 verbatim turns, older ones summarised |
| Final loop pass | tools withheld, so it must answer instead of fetching more |
| Concurrency | 4 calls max process-wide |

Result: ~2,300 tokens per call, so a full turn fits inside one minute's
budget. **Measured response time is 6–20 seconds.**

If quota does run out mid-chat, Atlas says so plainly and recovers on its own
— it backs off using the server's `retry-after` rather than failing silently.

### Two honest limitations

- **No image reading.** Groq's free tier currently offers no vision model. Atlas
  says so and offers to pull the numbers itself instead of guessing at a chart.
- **Yahoo Finance rate-limits hard.** That's why `FINNHUB_API_KEY` is worth the
  two minutes — with it, quotes and valuation multiples come from Finnhub and
  Yahoo is only a fallback.

---

## 3. Optional key

| Key | What it buys you | Get it |
|---|---|---|
| `FINNHUB_API_KEY` | Real-time quotes, better company news, earnings calendar | finnhub.io → free tier, instant |

Without it Atlas uses Yahoo Finance — slightly delayed, still fully
functional.

**SEC EDGAR needs no key.** Just put a real contact address in
`SEC_USER_AGENT` — the SEC asks that requests identify themselves.

---

## 4. Run it locally first

```bash
pip install -r requirements.txt
cp .env.example .env      # then fill in the two required keys
python -m app.main
```

Visit `http://localhost:8000/status` — you should see `"database": "ok"` and
your bot's `@username`.

Local runs use SQLite and have no public URL, so Telegram can't reach the
webhook. To talk to the bot locally, expose it:

```bash
ngrok http 8000
# put the https URL into PUBLIC_BASE_URL in .env, then restart
```

---

## 5. Deploy to Render

1. Push this repo to GitHub
2. Render dashboard → **New → Blueprint** → select the repo
3. Render reads `render.yaml` and provisions the web service **and** a
   Postgres database
4. It will prompt for the secrets: paste `TELEGRAM_BOT_TOKEN` and
   `GROQ_API_KEY` (plus `FINNHUB_API_KEY` if you got one)
5. Deploy. `PUBLIC_BASE_URL`, `DATABASE_URL` and `TELEGRAM_WEBHOOK_SECRET`
   are wired automatically

On boot Atlas registers its own Telegram webhook — there is no manual step.
Check the logs for `Webhook registered at ...`.

### The free-tier sleep problem

Render free instances sleep after ~15 minutes idle, which would kill the
morning briefing. Atlas pings its own `/health` every 12 minutes to stay
awake (`app/jobs/scheduler.py`). This works, but if you want guaranteed
uptime for judging, the $7/month Starter plan removes the issue entirely.

---

## 6. Verify end to end

Message your bot on Telegram. It should:

1. Introduce itself and ask **one** question
2. Do real analysis on whatever you answer
3. Remember it next time

Force the proactive features without waiting for a schedule
(`YOUR_SECRET` = `TELEGRAM_WEBHOOK_SECRET`, visible in Render's env vars):

```bash
curl -X POST https://your-app.onrender.com/admin/run-briefings \
  -H "X-Admin-Token: YOUR_SECRET"

curl -X POST https://your-app.onrender.com/admin/run-monitor \
  -H "X-Admin-Token: YOUR_SECRET"
```

Both are essential for recording the demo video — they let you show the
briefing and the thesis alert on demand rather than waiting for 7:30am.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| Bot silent | `PUBLIC_BASE_URL` wrong or missing. Check logs for `Webhook registered`. |
| "cannot reason" in logs | `GROQ_API_KEY` missing or invalid |
| "being rate-limited" reply | 8k tokens/minute hit. Wait ~60s; it recovers on its own. |
| Replies feel slow (>30s) | You're inside the token window from a previous turn. Normal pacing is 6-20s. |
| "I can't read images yet" | Expected — no vision model on Groq's free tier |
| Replies but no market data | Normal on first call — yfinance is slow to warm up. Retry once. |
| No briefing arrived | Instance asleep, or the user's `briefing_time` slot hasn't come round |
| Voice notes ignored | `GROQ_API_KEY` missing — voice uses the same key |
