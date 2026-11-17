"""Kept-open connections to cloud services.

A request on a new connection first pays TCP and TLS set-up (about 0.1 s from Colombo to Google, measured
2026-11-17) and then starts slowly while TCP learns the path (a 230 KB clip took 105 ms to send, 3 ms on a warm
connection). The call benchmark found 72 of 73 requests in an all-Google call opening a new connection, about
0.65 s of every turn. Three things caused it; this module is the shared answer to the first two:

- httpx closes an idle kept-open connection after 5 s by default, and a caller speaks for longer than that between
  requests. Every cloud client here keeps idle connections for KEEPALIVE_S instead.
- Google's speech-to-text and voice made a client per request. They now share one per event loop (PerLoopClient,
  used by google_cloud.http()).

(The third, chat providers closing a stream before reading its end, is providers/base.py drain().)

An httpx client belongs to the event loop it first ran on, so PerLoopClient makes one per loop: the app runs one
loop per worker, tests get a fresh loop each."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import httpx

KEEPALIVE_S = 60.0   # longer than a caller's turn; under nginx's default idle limit (75 s), a common front for model servers
LIMITS = httpx.Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=KEEPALIVE_S)


def new_client(*, transport: httpx.AsyncBaseTransport | None = None, **kwargs: Any) -> httpx.AsyncClient:
    """An AsyncClient that keeps idle connections for KEEPALIVE_S. A transport passed in brings its own pool: build
    it with `limits=LIMITS` (httpx applies `limits` only to the transport it makes itself)."""
    return httpx.AsyncClient(transport=transport, limits=LIMITS, **kwargs)


class PerLoopClient:
    def __init__(self, factory: Callable[[], httpx.AsyncClient] = new_client) -> None:
        self._factory = factory
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: httpx.AsyncClient | None = None

    def get(self) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        if self._client is None or self._loop is not loop or self._client.is_closed:
            self._client, self._loop = self._factory(), loop
        return self._client

    def install(self, client: httpx.AsyncClient) -> None:
        """Use this client on the running loop (the benchmark's traced client; tests)."""
        self._client, self._loop = client, asyncio.get_running_loop()

    async def aclose(self) -> None:
        client, self._client, self._loop = self._client, None, None
        if client is not None and not client.is_closed:
            await client.aclose()

    def forget(self) -> None:
        self._client, self._loop = None, None
