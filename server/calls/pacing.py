"""Staying under a provider's per-minute quota without counting the wait as the agent's time (spec
2026-11-17-call-simulation). Free tiers refuse past a few requests a minute (Gemini's free tier: 15; a Gemini-TTS
project: about 10, both seen in the first runs), and a refusal is the quota's failure, not the agent's.

Throttle.wait() sleeps until a request fits in the last minute and says how long it slept. PausableClock is a
perf-counter that leaves those sleeps out, so the agent's timings stay the model's own. PacedRouter puts a throttle
in front of every model request and pauses the clock while it waits."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any


class Throttle:
    def __init__(self, per_minute: int, *, monotonic: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self.per_minute = max(0, per_minute)
        self._monotonic, self._sleep = monotonic, sleep
        self._sent: list[float] = []

    async def wait(self) -> float:
        """Seconds slept; 0 when the quota had room (or there is no quota)."""
        if not self.per_minute:
            return 0.0
        now = self._monotonic()
        self._sent = [t for t in self._sent if now - t < 60]
        waited = 0.0
        if len(self._sent) >= self.per_minute:
            waited = 60 - (now - self._sent[0]) + 0.1
            await self._sleep(waited)
        self._sent.append(self._monotonic())
        return waited


class PausableClock:
    def __init__(self, source: Callable[[], float] = time.perf_counter) -> None:
        self._source, self._paused = source, 0.0

    def pause(self, seconds: float) -> None:
        self._paused += seconds

    def __call__(self) -> float:
        return self._source() - self._paused


class PacedRouter:
    """The router, with a throttle before every request; the waits are taken off `clock`."""

    def __init__(self, router: Any, throttle: Throttle, clock: PausableClock) -> None:
        self.router, self.throttle, self.clock = router, throttle, clock

    def resolve(self, model_id: str):
        return self.router.resolve(model_id)

    def stream_chat(self, model_id, messages, tools, settings):
        async def paced():
            self.clock.pause(await self.throttle.wait())
            async for chunk in self.router.stream_chat(model_id, messages, tools, settings):
                yield chunk
        return paced()

    async def aclose(self) -> None:
        await self.router.aclose()
