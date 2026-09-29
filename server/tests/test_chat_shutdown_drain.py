"""Deferred C1 minor: the shutdown drain waits for end-of-turn writes that are
added while it waits, until none are left or the deadline passes."""
import asyncio
import time

import pytest

from server.chat import orchestrator

pytestmark = pytest.mark.asyncio


def _track(coro):
    task = asyncio.ensure_future(coro)
    orchestrator._BACKGROUND.add(task)
    task.add_done_callback(orchestrator._BACKGROUND.discard)
    return task


async def test_the_drain_waits_for_writes_added_during_the_wait():
    done = []

    async def later():
        await asyncio.sleep(0.05)
        done.append("later")

    async def first():
        await asyncio.sleep(0.05)
        _track(later())          # a second write, started while the drain waits
        done.append("first")

    _track(first())
    assert await orchestrator.drain_background(timeout=2) == 0
    assert done == ["first", "later"]
    assert not orchestrator._BACKGROUND


async def test_the_drain_stops_at_the_deadline_and_reports_what_is_left():
    stuck = _track(asyncio.sleep(10))
    started = time.monotonic()
    assert await orchestrator.drain_background(timeout=0.1) == 1
    assert time.monotonic() - started < 1
    stuck.cancel()
    await asyncio.gather(stuck, return_exceptions=True)
    await asyncio.sleep(0)   # let its discard callback run: _BACKGROUND is module-global


async def test_nothing_pending_returns_at_once():
    assert await orchestrator.drain_background(timeout=5) == 0
