"""Background job scheduling.

APScheduler in-process rather than a separate worker: Atlas is a single
service, the jobs are I/O-bound, and adding a queue would be architecture for
its own sake at this scale.

The keep-alive job exists specifically for Render's free tier, which sleeps an
instance after ~15 minutes of inactivity. A sleeping instance cannot send a
7:30am briefing, which would quietly break the product's most visible promise.
"""

from __future__ import annotations

import logging

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.jobs.briefing import run_briefings
from app.jobs.monitor import run_monitor

log = logging.getLogger(__name__)

scheduler: AsyncIOScheduler | None = None


async def _keep_alive() -> None:
    if not settings.base_url:
        return
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            await client.get(f"{settings.base_url}/health")
    except Exception as exc:  # noqa: BLE001
        log.debug("Keep-alive ping failed: %s", exc)


def start_scheduler() -> AsyncIOScheduler:
    global scheduler
    if scheduler and scheduler.running:
        return scheduler

    scheduler = AsyncIOScheduler(
        timezone="UTC",
        job_defaults={
            "coalesce": True,       # a missed run shouldn't stack up duplicates
            "max_instances": 1,     # never run two sweeps concurrently
            "misfire_grace_time": 300,
        },
    )

    # Every 10 minutes: catches each user's local briefing slot worldwide.
    scheduler.add_job(
        run_briefings,
        IntervalTrigger(minutes=10),
        id="briefings",
        name="Daily briefings",
    )

    # Watchlist + thesis sweep, on weekdays around US market hours.
    scheduler.add_job(
        run_monitor,
        CronTrigger(day_of_week="mon-fri", hour="13-21", minute="5,35"),
        id="monitor_market_hours",
        name="Monitor (market hours)",
    )
    # A lighter off-hours pass catches after-hours releases and filings.
    scheduler.add_job(
        run_monitor,
        CronTrigger(hour="0,4,9,23", minute="15"),
        id="monitor_off_hours",
        name="Monitor (off hours)",
    )

    scheduler.add_job(
        _keep_alive,
        IntervalTrigger(minutes=12),
        id="keep_alive",
        name="Keep-alive ping",
    )

    scheduler.start()
    log.info("Scheduler started with %d jobs", len(scheduler.get_jobs()))
    return scheduler


def stop_scheduler() -> None:
    global scheduler
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
        log.info("Scheduler stopped")
    scheduler = None
