"""Shared HTTP clients.

Every outbound call used to construct its own `httpx.AsyncClient`, which meant
a fresh DNS lookup, TCP handshake and TLS negotiation each time — roughly
200-400ms of pure overhead per call, on every quote, headline and filing.

Reusing one pooled client per host family removes that entirely. On a turn
that makes six calls this is the single largest latency saving in the project,
and it costs nothing.
"""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

_clients: dict[str, httpx.AsyncClient] = {}

_LIMITS = httpx.Limits(
    max_connections=20,
    max_keepalive_connections=10,
    keepalive_expiry=60.0,
)


def get_client(name: str, *, timeout: float = 15.0, headers: dict | None = None):
    """Return a pooled client for a logical group of calls.

    Grouping by name (telegram / finnhub / sec) keeps connections warm per
    host without one slow provider's pool starving another's.
    """
    client = _clients.get(name)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=5.0),
            limits=_LIMITS,
            headers=headers or {},
            follow_redirects=True,
        )
        _clients[name] = client
    return client


async def close_all() -> None:
    for name, client in list(_clients.items()):
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001
            pass
        _clients.pop(name, None)
    log.info("HTTP clients closed")
