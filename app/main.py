"""FastAPI application — webhook receiver, health, and admin endpoints."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.config import settings
from app.data import http as http_pool
from app.db import dispose_db, init_db, session_scope
from app.db import repo
from app.jobs.scheduler import start_scheduler, stop_scheduler
from app.telegram import client as tg
from app.telegram.handlers import handle_update

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
)
# These are chatty at INFO and drown out our own logs.
for noisy in ("httpx", "httpcore", "apscheduler.executors.default"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

log = logging.getLogger("atlas")


async def _warm_caches() -> None:
    """Pre-fetch the two slowest cold-start lookups. Failures are harmless."""
    from app.data import edgar, market

    try:
        await asyncio.gather(
            edgar.get_cik("AAPL"),        # pulls and caches the full ticker map
            market.get_market_snapshot(),  # indexes used by every briefing
            return_exceptions=True,
        )
        log.info("Caches warmed")
    except Exception:  # noqa: BLE001
        log.debug("Cache warm-up skipped")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Atlas %s starting", __version__)

    await init_db()

    if settings.telegram_bot_token:
        me = await tg.get_me()
        if me:
            log.info("Connected to Telegram as @%s", me.get("username"))
            await tg.set_webhook()
        else:
            log.error("Telegram token rejected — the bot will not receive messages.")
    else:
        log.warning("TELEGRAM_BOT_TOKEN not set — running without Telegram.")

    if not settings.groq_api_key:
        log.error("GROQ_API_KEY not set — Atlas cannot reason.")

    start_scheduler()

    # Warm the slow caches in the background so the first real question
    # doesn't pay for them. The EDGAR ticker map is ~10k rows and the index
    # snapshot hits five symbols — both would otherwise land on whoever
    # messages first.
    asyncio.create_task(_warm_caches())

    log.info(
        "Ready. voice=%s finnhub=%s",
        settings.voice_enabled,
        settings.finnhub_enabled,
    )

    yield

    log.info("Atlas shutting down")
    stop_scheduler()
    await http_pool.close_all()
    await dispose_db()


app = FastAPI(
    title="Atlas — AI Financial Assistant",
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs",
)


# =============================================================================
# Telegram webhook
# =============================================================================


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    background: BackgroundTasks,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> JSONResponse:
    """Receive an update, acknowledge immediately, process in the background.

    Telegram re-delivers any update not acknowledged within seconds, and an
    Atlas turn can take considerably longer than that — so the work must not
    happen inside the request.
    """
    if x_telegram_bot_api_secret_token != settings.webhook_secret:
        log.warning("Rejected webhook call with bad secret token")
        raise HTTPException(status_code=403, detail="Invalid secret token")

    try:
        update = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Malformed update")

    background.add_task(handle_update, update)
    return JSONResponse({"ok": True})


# =============================================================================
# Operations
# =============================================================================


@app.get("/health")
async def health() -> dict:
    """Liveness probe, also hit by the keep-alive job."""
    return {"status": "ok", "version": __version__}


@app.get("/")
async def root() -> dict:
    return {
        "service": "Atlas — AI Financial Assistant",
        "version": __version__,
        "status": "running",
        "capabilities": {
            "telegram": bool(settings.telegram_bot_token),
            "reasoning": bool(settings.groq_api_key),
            "voice": settings.voice_enabled,
            "realtime_quotes": settings.finnhub_enabled,
        },
    }


@app.get("/status")
async def status() -> dict:
    """Deeper check — verifies the database and Telegram are both reachable."""
    from app.jobs.scheduler import scheduler

    result: dict = {"version": __version__}

    try:
        async with session_scope() as db:
            users = await repo.all_active_users(db)
        result["database"] = "ok"
        result["active_users"] = len(users)
    except Exception as exc:  # noqa: BLE001
        result["database"] = f"error: {exc}"

    me = await tg.get_me() if settings.telegram_bot_token else None
    result["telegram"] = f"@{me['username']}" if me else "unavailable"

    result["jobs"] = (
        [j.id for j in scheduler.get_jobs()] if scheduler and scheduler.running else []
    )
    return result


# --- manual triggers, for demos and debugging --------------------------------


def _require_admin(token: str | None) -> None:
    if token != settings.webhook_secret:
        raise HTTPException(status_code=403, detail="Forbidden")


@app.post("/admin/run-briefings")
async def admin_run_briefings(
    background: BackgroundTasks, x_admin_token: str | None = Header(default=None)
) -> dict:
    """Force the briefing pass now, ignoring schedule slots."""
    _require_admin(x_admin_token)
    from app.jobs.briefing import send_briefing

    async def _force() -> None:
        async with session_scope() as db:
            users = await repo.all_active_users(db)
        for user in users:
            try:
                async with session_scope() as db:
                    fresh = await repo.get_user_by_id(db, user.id)
                    if fresh:
                        await send_briefing(db, fresh)
            except Exception:  # noqa: BLE001
                log.exception("Forced briefing failed for user %s", user.id)
            await asyncio.sleep(1.0)

    background.add_task(_force)
    return {"triggered": "briefings"}


@app.post("/admin/run-monitor")
async def admin_run_monitor(
    background: BackgroundTasks, x_admin_token: str | None = Header(default=None)
) -> dict:
    """Force a watchlist and thesis sweep now."""
    _require_admin(x_admin_token)
    from app.jobs.monitor import run_monitor

    background.add_task(run_monitor)
    return {"triggered": "monitor"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port, reload=False)
