# Atlas — Demo Video Script

**4 minutes. Portrait. Phone screen recording. One Telegram chat.**

Read the **bold** lines out loud. Do the *italic* bits with your hands.
Type the `code blocks` exactly.

---

## First, what this video is

You're showing someone an analyst you hired. Not a product tour.

The judge is quietly asking one thing the whole time: *"how is this different
from ChatGPT in a Telegram window?"* Every scene answers it.

**What's not in the video:** no dashboard, no slides, no logo, no architecture
diagram, no feature list. The brief asks for a Telegram assistant and says to
avoid menus and buttons — so a dashboard would show you misread it.

**Say things simply.** Don't say "leverages multi-model architecture". Say
"it looks things up before it answers". The judges have read fifty submissions
full of buzzwords today. Plain speech will stand out on its own.

---

## What each scene is buying you

| Scored on | Weight | Scenes |
|---|---|---|
| Is it useful, does it act on its own | **30%** | 2, 6, 7 |
| Did you make smart choices | **25%** | 1, 4, 6, 7, 8 |
| Does it feel like talking to a person | **20%** | 2, 3, 4 |
| Does it know finance properly | **15%** | 2, 5, 6 |
| Is it built well | **10%** | optional closer |

**Scene 7 wins this.** If you run long, cut Scene 5 — never Scene 7.

---

## The day before

1. **Bot is deployed and answering.** Not nearly — answering.
2. **Second Telegram account, used yesterday.** Have a real conversation on it
   about a company. You can't fake memory; the judge will spot it.
3. **A PDF saved on your phone.** Links are in TESTING.md.
4. **Your admin secret** from Render → Environment (`TELEGRAM_WEBHOOK_SECRET`).
5. **Phone on Do Not Disturb.** One notification banner means a re-record.
6. **Dry run the whole thing once, unrecorded.** Find the surprises then.

Record with your phone's built-in screen recorder. Not Loom — Loom shows a
desktop, and a desktop makes this look like a dev tool instead of something
people use.

---

# THE SCRIPT

## Scene 1 — Opening (0:00–0:20)

*Fresh chat with the bot. Nothing typed yet.*

> **"This is Atlas. It's a financial analyst that lives in Telegram."**

> **"Most of these start by asking you to fill in a form. This one asks one
> question, then gets to work."**

*Tap in so its opening message shows.*

> **"That's the whole setup. It picks up the rest from talking to me."**

---

## Scene 2 — The first real answer (0:20–1:00)

*Type:*

```
I'm a VC associate covering semis. I'm long Nvidia because hyperscaler capex holds up, but the valuation worries me.
```

*Send. Let it finish. Don't talk over it.*

*Then point at the screen:*

> **"Three things there."**

> **"It picked up what I do — I never told it in a form."**

> **"Every number came from a live lookup, with the time it was measured. It
> didn't answer from memory, because a model's memory of prices is always out
> of date, and a wrong price is worse than no price."**

> **"And it finished by saying what would change its mind."**

*Pause 2 seconds so they can read it.*

> **"That's what an analyst does. Chatbots never do it."**

---

## Scene 3 — It follows the conversation (1:00–1:25)

*Type:*

```
Compare it to AMD
```

*Wait. Then:*

```
Which one is safer?
```

> **"I didn't repeat myself. I said 'which one is safer', the way you'd say it
> to someone sitting next to you."**

> **"There are no commands. There's no menu to learn."**

---

## Scene 4 — It pushes back (1:25–1:45)

*Type:*

```
Tell me about Apple
```

> **"That's a vague question, so it asked — but look how."**

> **"It's not saying 'please clarify'. It already has an opinion and it's
> telling me where it'd start. That's the difference between a form field and
> a colleague."**

---

## Scene 5 — Where the numbers come from (1:45–2:15)

*Type:*

```
What did Nvidia say in their last 10-K about customer concentration?
```

> **"That's not an article about the filing. That's the filing — the document
> Nvidia sent the SEC, with the date."**

> **"Most tools in this space wrap a price API and a news feed. This one reads
> what the company actually said."**

*Shorten this scene first if you're over time.*

---

## Scene 6 — Documents (2:15–2:55)

*Attach the PDF. No caption. Send.*

> **"That's a hundred-page annual report."**

*Wait for it. Then:*

```
What are the biggest risks in here?
```

*Wait. Then the one that matters:*

```
Are any of those risks actually playing out right now?
```

> **"This is my favourite bit."**

> **"It's taking risks written months ago and checking them against the news
> today. A risk in a filing is hypothetical until one of them starts
> happening — and spotting that is the actual job."**

> **"Summarising a PDF is easy. This is holding the document and today's
> market in its head at the same time."**

---

## Scene 7 — It messages me first (2:55–3:35)
### The most important 40 seconds. Don't rush it.

*Trigger the briefing and monitor off-camera before this scene so the messages
land while you're filming. Don't film the terminal.*

*Show the briefing arriving.*

> **"That's my morning brief. It opens with the thing that matters to me, not
> a market summary. And it leaves out anything it already told me — it keeps
> track, so it can't repeat itself."**

*Now the thesis alert. Slow down.*

> **"And here's the one."**

> **"Earlier I said I'm long Nvidia *because* hyperscaler spending holds up. It
> didn't just save that I like Nvidia. It pulled my reasoning apart into things
> that could later turn out wrong."**

> **"So when one of them starts going the other way, it tells me. I didn't
> ask."**

*Let it sit on screen for 3 seconds.*

> **"It's not sending me news. It's telling me my own thinking might be
> breaking."**

*Then say this — it matters as much as the alert:*

> **"And most of what it checks, it decides isn't worth sending. Everything
> gets weighed against what I actually care about, and if it doesn't clear the
> bar, it says nothing."**

> **"The brief asked for something that stays quiet when nothing's happening.
> That silence is on purpose."**

---

## Scene 8 — It remembers (3:35–4:00)

*Switch to the account you used yesterday.*

```
what was I worried about again?
```

*Let it recall the real conversation.*

> **"That was yesterday, on a different account."**

> **"It remembers why I think what I think. And it tells me when that stops
> holding up."**

> **"That's the difference between a chatbot and an analyst."**

*End on the chat. Stop recording. No outro.*

---

## Optional closer — 15 seconds, buys the last 10%

Only if you're under 4 minutes. Say it plainly over the chat screen:

> **"Underneath: it's a real tool-using agent, not keyword matching. Voice,
> documents and questions all go through the same reasoning. It runs entirely
> on free infrastructure. And when a data source fails, it tells you it failed
> instead of making a number up."**

No code, no diagrams. Say it and stop.

---

# If something goes wrong mid-take

You usually don't need to re-record. Handling it calmly reads as confidence.

| If | Say |
|---|---|
| A reply takes a while | **"That pause is real — it's out fetching data."** |
| A lookup fails | **"And there's the honest answer. It couldn't get that, so it says so. It won't invent a number."** ← *this is a good moment, not a bad one* |
| It says it's rate-limited | **"Free tier, and I've been hammering it. It sorts itself out."** |
| A reply runs long | Don't mention it. Move on. |

**Re-record only if** it crashes, sends nothing, or says something wrong.

---

# Don't

- **Don't say the word "command"** or show `/start` being typed. The brief
  says avoid command-driven bots.
- **Don't show a dashboard or web page.** Telegram is the product.
- **Don't list your tech stack** over the chat. That's 10% of the score and
  the optional closer covers it.
- **Don't say "as you can see".** Say what it means.
- **Don't apologise** for speed, or the free tier, or the model.
- **Don't let it give a buy or sell rating.** If it ever does, that's a bug —
  tell me. A bot saying "STRONG BUY" is the fastest way to lose a finance
  professional, and the brief warns against stating unverified things as fact.

---

# What to post

In the group — https://t.me/+HAws-LR5tpszOGZl

Lead with judgment, not features. A feature list invites a comparison you
don't need to win.

```
Atlas — AI Financial Assistant

Most assistants answer financial questions.
Atlas notices when you're about to be wrong.

Tell it WHY you hold a position and it pulls your reasoning apart into
things that could turn out wrong — then watches for exactly that. Later
it messages you without being asked:
"Microsoft guided capex down. That's the assumption your Nvidia call
rests on."

Deliberately narrow:
- No buy/sell ratings. It gives you the analysis and you decide.
- Stays quiet when nothing matters. Every alert is weighed against what
  you actually care about; below the bar, it says nothing.
- Never repeats itself.
- Reads what companies actually filed with the SEC, not headlines.
- Works on Indian, US, Japanese and crypto markets.

Setup is one question, not a form.
Text and voice. No commands, no menus, no buttons.
Runs entirely on free infrastructure.

Bot: @AtlasAnalysisBot
Video: <link>
```

Notice what's missing: no feature count, no model list, no emoji. That
restraint is the message.

---

# Before you submit

- [ ] Video is under 4:30, portrait
- [ ] Scene 7 is intact and unhurried
- [ ] The bot in the video is the one in the link, and it's awake
- [ ] Someone else watched it and can say what makes it different
- [ ] Bot handle is right in the post
- [ ] You messaged the bot in the last hour so it's warm
