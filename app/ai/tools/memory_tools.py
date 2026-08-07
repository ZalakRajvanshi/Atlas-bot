"""Memory and proactive-tracking tools.

These are what make Atlas an assistant rather than a stateless answer engine.
`record_thesis` in particular is the hook the background monitor watches: it
turns a passing remark into something reality can be checked against.
"""

from __future__ import annotations

import logging

from app.ai.tools.base import Tool, ToolContext, norm_ticker, obj
from app.data import market
from app.db import repo
from app.db.models import EventKind, FactKind

log = logging.getLogger(__name__)


async def h_remember(ctx: ToolContext, args: dict) -> dict:
    kind_raw = (args.get("kind") or "context").lower()
    try:
        kind = FactKind(kind_raw)
    except ValueError:
        kind = FactKind.CONTEXT

    fact = await repo.add_fact(
        ctx.db,
        ctx.user,
        content=args["fact"],
        kind=kind,
        confidence=float(args.get("confidence", 0.85)),
    )
    # A duplicate is a success from the model's point of view — it wanted the
    # fact stored, and it is stored.
    return {"stored": True, "duplicate": fact is None}


async def h_update_watchlist(ctx: ToolContext, args: dict) -> dict:
    action = (args.get("action") or "add").lower()
    ticker = norm_ticker(args.get("ticker"))

    if not ticker:
        return {"error": "A ticker is required."}

    if action == "remove":
        removed = await repo.remove_from_watchlist(ctx.db, ctx.user, ticker)
        return {"removed": removed, "ticker": ticker}

    # Enrich with the company name so later briefings read naturally.
    company_name = args.get("company_name")
    if not company_name:
        profile = await market.get_profile(ticker)
        company_name = (profile or {}).get("name")

    item = await repo.add_to_watchlist(
        ctx.db,
        ctx.user,
        ticker=ticker,
        company_name=company_name,
        reason=args.get("reason"),
        priority=int(args.get("priority", 1)),
    )
    ctx.touched_tickers.add(ticker)
    return {
        "added": True,
        "ticker": item.ticker,
        "company_name": item.company_name,
    }


async def h_record_thesis(ctx: ToolContext, args: dict) -> dict:
    assumptions = args.get("assumptions") or []
    if isinstance(assumptions, str):
        assumptions = [assumptions]

    ticker = norm_ticker(args.get("ticker")) or None
    if ticker:
        ctx.touched_tickers.add(ticker)
        # A stated view implies they care about the name; track it too.
        await repo.add_to_watchlist(
            ctx.db,
            ctx.user,
            ticker=ticker,
            reason=args.get("claim"),
            priority=2,
        )

    thesis = await repo.add_thesis(
        ctx.db,
        ctx.user,
        subject=args["subject"],
        claim=args["claim"],
        assumptions=assumptions,
        ticker=ticker,
        stance=(args.get("stance") or "watching").lower(),
    )
    log.info(
        "Recorded thesis for user %s on %s: %s", ctx.user.id, thesis.subject, thesis.claim
    )
    return {"recorded": True, "thesis_id": thesis.id, "assumptions": assumptions}


async def h_create_alert(ctx: ToolContext, args: dict) -> dict:
    kind_raw = (args.get("kind") or "news").lower()
    try:
        kind = EventKind(kind_raw)
    except ValueError:
        kind = EventKind.NEWS

    ticker = norm_ticker(args.get("ticker")) or None
    params: dict = {}
    if args.get("percent_move") is not None:
        params["pct_move"] = float(args["percent_move"])

    alert = await repo.add_alert(
        ctx.db,
        user_id=ctx.user.id,
        kind=kind,
        description=args["description"],
        ticker=ticker,
        params=params,
    )
    if ticker:
        await repo.add_to_watchlist(
            ctx.db, ctx.user, ticker=ticker, reason=args["description"], priority=2
        )
    return {"created": True, "alert_id": alert.id, "description": alert.description}


async def h_set_briefing(ctx: ToolContext, args: dict) -> dict:
    if args.get("enabled") is False:
        ctx.user.briefing_enabled = False
        return {"briefing_enabled": False}

    time_str = (args.get("time") or "").strip()
    if time_str:
        # Accept "7", "7:30", "07:30" and normalise.
        try:
            if ":" in time_str:
                hh, mm = time_str.split(":")[:2]
            else:
                hh, mm = time_str, "00"
            hour, minute = int(hh), int(mm)
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
            ctx.user.briefing_time = f"{hour:02d}:{minute:02d}"
        except ValueError:
            return {"error": f"Could not parse '{time_str}' as a time."}

    if args.get("timezone"):
        ctx.user.timezone = args["timezone"]

    ctx.user.briefing_enabled = True
    return {
        "briefing_enabled": True,
        "time": ctx.user.briefing_time,
        "timezone": ctx.user.timezone,
    }


TOOLS = [
    Tool(
        name="remember",
        description='Store a durable fact about the user (role, interests, goals, constraints, style). Write a full sentence. Never mention that you saved it.',
        input_schema=obj(
            {
                "fact": {"type": "string", "description": "Full sentence, third person"},
                "kind": {
                    "type": "string",
                    "enum": [
                        "role",
                        "interest",
                        "preference",
                        "goal",
                        "context",
                        "constraint",
                    ],
                },
            },
            ["fact"],
        ),
        handler=h_remember,
    ),
    Tool(
        name="update_watchlist",
        description='Add/remove a company the user follows. Always record why — it drives alert relevance. priority 2 = they hold it or care most.',
        input_schema=obj(
            {
                "action": {"type": "string", "enum": ["add", "remove"]},
                "ticker": {"type": "string"},
                "company_name": {"type": "string"},
                "reason": {"type": "string"},
                "priority": {"type": "integer", "enum": [0, 1, 2]},
            },
            ["action", "ticker"],
        ),
        handler=h_update_watchlist,
    ),
    Tool(
        name="record_thesis",
        description="Record a stated investment VIEW ('I'm long X because Y', 'worried about Z'). Break it into 2-4 falsifiable assumptions that future news could contradict. Do it silently.",
        input_schema=obj(
            {
                "subject": {"type": "string"},
                "ticker": {"type": "string"},
                "stance": {
                    "type": "string",
                    "enum": ["long", "short", "watching", "concerned"],
                },
                "claim": {"type": "string", "description": "The view in one sentence"},
                "assumptions": {"type": "array", "items": {"type": "string"}},
            },
            ["subject", "claim", "assumptions"],
        ),
        handler=h_record_thesis,
    ),
    Tool(
        name="create_alert",
        description='Set a standing alert the user explicitly asked for (price move %, earnings, filings, news).',
        input_schema=obj(
            {
                "description": {"type": "string"},
                "kind": {
                    "type": "string",
                    "enum": ["price_move", "earnings", "news", "filing"],
                },
                "ticker": {"type": "string"},
                "percent_move": {"type": "number"},
            },
            ["description", "kind"],
        ),
        handler=h_create_alert,
    ),
    Tool(
        name="set_briefing_schedule",
        description='Set or disable the daily brief time and timezone.',
        input_schema=obj(
            {
                "time": {"type": "string", "description": "24h local, e.g. 07:30"},
                "timezone": {"type": "string", "description": "IANA, e.g. Asia/Kolkata"},
                "enabled": {"type": "boolean"},
            }
        ),
        handler=h_set_briefing,
    ),
]
