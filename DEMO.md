# Atlas — Demo Video Shooting Script

**Runtime: 4:00 · Portrait · Phone screen recording · One continuous Telegram chat**

Read the **bold** lines aloud. Do the *italic* lines with your hands.
Type the `code blocks` exactly as written.

---

## Before you read further: what this video is NOT

- **Not a dashboard.** The brief says *"the primary interface should be Telegram"* and
  *"avoid inline buttons, menus, quick replies, command-based navigation."* A dashboard
  is the opposite of the ask. Building one signals you misread the brief.
- **Not a feature tour.** Feature lists invite feature-count comparison, and someone
  always has a longer list. You win on judgment, not quantity.
- **Not slides, logos, architecture diagrams, or an outro card.**

**It is one thing:** a phone, a Telegram chat, and proof that this thing *thinks*.

The judge is silently asking one question the whole time:

> *"Is this different from ChatGPT in Telegram?"*

Every scene must answer **yes**.

---

## Scoring map — what each scene is buying you

| Scored on | Weight | Scenes that earn it |
|---|---|---|
| Usefulness, proactivity, user value | **30%** | 2, 6, 7 |
| Product thinking, judgment, feature selection | **25%** | 1, 4, 6, 7, 8 |
| AI experience, conversational quality | **20%** | 2, 3, 4 |
| Depth of the finance vertical | **15%** | 2, 5, 6 |
| Engineering quality | **10%** | 6, 7 (stated, not shown) |

Scene 7 is the one that wins. Do not cut it for time — cut Scene 5 instead.

---

# PREP — the day before

Skipping any of these will cost you the video.

1. **Bot is deployed and answering.** Not "mostly working". Answering.
2. **Second Telegram account, used yesterday.** Have a real 10-minute conversation on it
   about a company. You cannot fake memory — the judge will see the difference between
   real recall and a scripted line.
3. **A 10-K PDF on your phone.** Download Nvidia's or Apple's from `sec.gov`.
   Rename it something clean like `nvidia-10k.pdf`.
4. **Know your admin secret** — the value of `TELEGRAM_WEBHOOK_SECRET` in Render's
   environment variables. You'll need it for Scene 7.
5. **Phone on Do Not Disturb.** A notification banner mid-take means re-recording.
6. **Dry run the whole thing once without recording.** Find out then, not on tape,
   that a name resolves oddly or a reply runs long.

**Recording:** your phone's built-in screen recorder (iOS Control Centre / Android
Quick Settings). Not Loom — Loom records a desktop, and a desktop window makes this
look like a dev tool instead of a product.

**Editing:** CapCut, or your phone's built-in trimmer. You only need to trim the ends
and cut dead air.

---

# THE SCRIPT

## Scene 1 — Cold open (0:00–0:20)
*Buys: product thinking*

*Open Telegram. Fresh chat with the bot. Nothing typed.*

> **"This is Atlas. It's a financial analyst that lives in Telegram."**

> **"Most assistants start with a setup form — role, interests, watchlist, notification
> preferences. Watch what this one does instead."**

*Tap into the chat so its opening message shows.*

> **"One question. That's the entire onboarding."**

> **"Everything else it learns from talking to me — the way a new colleague would."**

---

## Scene 2 — The first real answer (0:20–1:00)
*Buys: usefulness · conversational quality · finance depth*

*Type this exactly:*

```
I'm a VC associate covering semis. I'm long Nvidia because
hyperscaler capex holds up, but the valuation worries me.
```

*Send. Let the reply finish completely. Do not talk over it.*

*Now point at the screen and go through it:*

> **"Three things just happened there."**

> **"It learned my job — I never filled in a field."**

> **"Every number in that reply came from a live tool call, with a timestamp. It didn't
> answer from memory, because a model's memory of prices is always stale and stale
> prices are worse than no prices."**

> **"And it ended by telling me what would change its mind."**

*Pause 2 seconds on the reply.*

> **"That last part is what a real analyst says. Chatbots never do it."**

---

## Scene 3 — It holds the thread (1:00–1:25)
*Buys: conversational quality*

*Type:*

```
Compare it to AMD
```

*Wait for the reply. Then type:*

```
Which one is safer?
```

*Wait. Then:*

> **"I never repeated myself. I didn't say 'compare Nvidia and AMD on risk'. I said
> 'which one is safer' — like you would to a person sitting next to you."**

> **"No commands. No slash. No menu. There isn't one to learn."**

---

## Scene 4 — It asks better questions than it answers (1:25–1:45)
*Buys: conversational quality · product thinking*

*Type:*

```
Tell me about Apple
```

*Wait for it to ask a clarifying question.*

> **"That question was vague, so it pushed back — but notice how."**

> **"It's not saying 'please clarify what you'd like to know'. It already has a view and
> it's telling me where it would start. That's the difference between a form field and
> a colleague."**

---

## Scene 5 — Primary sources (1:45–2:15)
*Buys: finance depth*

*Type:*

```
What did Nvidia actually say in their last 10-K about
customer concentration?
```

*Wait for the reply with the filing reference.*

> **"That is not a news article about the filing. That's the filing — the actual document
> Nvidia submitted to the SEC, with the date it was filed."**

> **"Most tools in this space wrap a stock price API and a news feed. This reads the
> primary source, because that's the standard a finance professional actually applies."**

*If you need to cut for time, this is the scene to shorten — but keep one sentence of it.*

---

## Scene 6 — Documents that stay alive (2:15–2:55)
*Buys: usefulness · finance depth · product thinking*

*Attach the 10-K PDF. No caption. Send.*

> **"That's a hundred-page annual report."**

*Wait for it to ingest and respond. Then type:*

```
What are the biggest risks in here?
```

*Wait for the reply. Then type the important one:*

```
Are any of those risks actually playing out right now?
```

*Wait. Then:*

> **"This is the part I care about."**

> **"It's taking risk factors written months ago and checking them against today's news.
> A risk factor is hypothetical until one of them starts happening — and noticing that
> is analyst work, not document search."**

> **"Everyone can summarise a PDF. This is holding the document and live data at the
> same time."**

---

## Scene 7 — It messages you first (2:55–3:35)
*Buys: usefulness · proactivity · product thinking — **the highest-value 40 seconds in the video***

*Trigger the briefing and the monitor off-camera before this scene, so the messages land
while you're filming. Do not film the terminal.*

*Show the briefing arriving.*

> **"That's my morning brief. It leads with the thing that matters to me, not a market
> summary. And it skips anything it already told me — it fingerprints every message it
> sends, so it physically cannot repeat itself."**

*Now the thesis alert arrives. Slow down here.*

> **"And this is the one that makes it different."**

> **"Earlier I told it I'm long Nvidia *because* hyperscaler capex holds up. It didn't
> just store that I like Nvidia. It broke my reasoning into assumptions that could later
> be proven wrong."**

> **"So when the evidence turns against one of them, it tells me. Without being asked."**

*Let the alert sit on screen for 3 seconds.*

> **"It's not sending me news. It's telling me my own thinking might be breaking."**

*Then — this line matters as much as the alert:*

> **"And most of what it checks, it decides isn't worth sending. Every candidate gets
> scored against my profile, and below the bar it stays silent."**

> **"The brief asked for an assistant that stays quiet when nothing matters. Silence is
> a feature here — not a missing one."**

---

## Scene 8 — Memory that earns its keep (3:35–4:00)
*Buys: product thinking · usefulness*

*Switch to your second account — the one you used yesterday.*

*Type:*

```
what was I worried about again?
```

*Let it recall the real prior conversation.*

> **"That's a conversation from yesterday, on a different account."**

> **"It remembers why I believe what I believe. And it tells me when that stops being
> true."**

> **"That's the difference between a chatbot and an analyst."**

*End on the chat screen. Stop recording. No outro.*

---

# OPTIONAL — the 15 seconds that buy the last 10%

If you land under 4:00 and want the engineering-quality mark, add this over the final
chat screen. Say it plainly, don't labour it:

> **"Under it: FastAPI, Postgres, a real tool-calling agent — not keyword routing. Voice,
> images and documents all converge on the same reasoning loop. It runs entirely on free
> infrastructure, and every data source failing produces an honest 'I couldn't retrieve
> that', never a made-up number."**

Do not show code. Do not show a diagram. Say it and end.

---

# THINGS TO SAY IF SOMETHING GOES WRONG ON TAPE

You do not need to re-record for these. Handling them well reads as confidence.

| If | Say |
|---|---|
| A reply takes 5+ seconds | **"It's making live calls right now — that pause is real data being fetched, not a loading screen."** |
| A data source fails | **"And that's the honest answer — it couldn't get that, so it says so. It never fills the gap with a guess."** ← *this is genuinely a good look* |
| Rate limit message appears | **"Free tier, and I've been hammering it. It backs off and recovers on its own."** |
| A reply is longer than you wanted | Just don't draw attention to it. Move on. |

**Re-record only if:** the bot crashes, sends nothing, or says something factually wrong.

---

# WHAT NOT TO DO

- **Don't demo slash commands.** The brief explicitly warns against them. If you show
  `/start`, do it silently — never say the word "command".
- **Don't show a dashboard, admin panel, or web page.** Telegram is the product.
- **Don't read out your tech stack over the chat.** 10% of the score, and the optional
  scene already covers it.
- **Don't say "as you can see".** Say what it means instead.
- **Don't apologise for anything.** Not the speed, not the tier, not the model.
- **Don't let it give a buy/sell rating.** If it ever does, that's a bug — tell me. A bot
  issuing "STRONG BUY" is the fastest way to lose a finance professional's trust, and
  the brief warns against presenting unverified conclusions as fact.

---

# SUBMISSION POST

Post in the group — https://t.me/+HAws-LR5tpszOGZl

Lead with judgment, not features. A feature list invites a feature-count comparison you
don't need to win.

```
Atlas — AI Financial Assistant

Most assistants answer financial questions.
Atlas notices when you're about to be wrong.

Tell it WHY you hold a position and it breaks your reasoning into
assumptions that can be proven wrong — then watches for exactly that.
Weeks later it messages you, unprompted:
"Microsoft guided capex down. That's the assumption your Nvidia call
rests on."

- Onboarding is one question, not a form. It learns the rest by talking.
- It cites SEC filings, not headlines.
- Upload a 10-K and ask whether its risk factors are playing out today —
  it checks the document against live news.
- It never issues buy/sell ratings. Analysis, then you decide.
- And when nothing matters, it says nothing at all. Every alert is scored
  against your profile; below the bar, it stays silent.

Text, voice notes and images. No commands, no menus, no buttons.
Runs entirely on free infrastructure.

Bot: @your_bot_username
Video: <link>
```

---

# FINAL CHECK BEFORE YOU SUBMIT

- [ ] Video is under 4:30 and portrait
- [ ] Scene 7 (proactive + thesis alert) is intact and unrushed
- [ ] The bot in the video is the same one in the link, and it's awake
- [ ] Someone else watched it and can tell you what makes it different
- [ ] Your bot handle is correct in the post
- [ ] You've messaged the bot yourself in the last hour so it's warm
