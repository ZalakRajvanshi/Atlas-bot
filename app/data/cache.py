"""Tiny async TTL cache.

Market data gets hit repeatedly inside a single agent turn (quote, then
profile, then news for the same ticker) and again by the briefing job across
users who share watchlist names. A 60-second cache removes most of that
traffic and keeps us inside free-tier rate limits.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

_store: dict[str, tuple[float, Any]] = {}
_locks: dict[str, asyncio.Lock] = {}
_lock_guard = asyncio.Lock()


async def _lock_for(key: str) -> asyncio.Lock:
    async with _lock_guard:
        if key not in _locks:
            _locks[key] = asyncio.Lock()
        return _locks[key]


async def cached(key: str, ttl: float, producer: Callable[[], Awaitable[Any]]) -> Any:
    """Return cached value or call `producer`, collapsing concurrent misses."""
    now = time.monotonic()
    hit = _store.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]

    lock = await _lock_for(key)
    async with lock:
        # Re-check: another coroutine may have filled it while we waited.
        hit = _store.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]

        value = await producer()
        _store[key] = (time.monotonic(), value)

        # Opportunistic eviction; this process is single-tenant and small.
        if len(_store) > 2000:
            cutoff = time.monotonic() - 900
            for k in [k for k, (t, _) in _store.items() if t < cutoff]:
                _store.pop(k, None)
                _locks.pop(k, None)

        return value


def invalidate(prefix: str = "") -> None:
    for k in [k for k in _store if k.startswith(prefix)]:
        _store.pop(k, None)
