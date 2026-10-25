"""Cancelling must stop the synthesis, not just the browser's wait for it.

The audiobook export runs up to three page requests at once, each a long
batch under the one Kokoro lock. Before this, Cancel aborted the browser's
requests but the server synthesized every page anyway: measured, a one-word
read right after Cancel waited 76.8 s (256 ms on an idle server).

These drive the ASGI app directly, because a disconnect is something the
client does mid-request, and count the calls a fake Kokoro receives."""
import asyncio
import time

import numpy as np
import pytest
from fastapi import FastAPI

from server import endpoints
from server.auth import deps

SR = 24000


class FakeKokoro:
    def __init__(self, seconds_per_sentence=0.05):
        self.calls = 0
        self.seconds = seconds_per_sentence

    def create(self, text, voice, speed, lang):
        self.calls += 1
        time.sleep(self.seconds)          # runs in the executor, like the real one
        return np.zeros(SR // 10, dtype=np.float32), SR


@pytest.fixture
def app(monkeypatch):
    fake = FakeKokoro()
    monkeypatch.setattr(endpoints, "kokoro", fake)
    # A fresh lock per test: an asyncio.Lock binds to the first event loop that
    # waits on it, and each test runs on its own loop (a stale one 500s).
    monkeypatch.setattr(endpoints, "inference_lock", asyncio.Lock())
    a = FastAPI()
    a.include_router(endpoints.router)
    a.dependency_overrides[deps.get_current_user] = lambda: deps.Principal(
        user_id="u", email="u@x.io", role="member", capabilities=frozenset({"reader"}))
    return a, fake


async def _call(app, path, body: bytes, gone: asyncio.Event):
    """One request whose client leaves when `gone` is set."""
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
             "root_path": "", "server": ("t", 80), "client": ("c", 1),
             "headers": [(b"content-type", b"application/json"),
                         (b"content-length", str(len(body)).encode())]}
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        if not gone.is_set():
            await gone.wait()
        return {"type": "http.disconnect"}

    status = {}

    async def send(message):
        if message["type"] == "http.response.start":
            status["code"] = message["status"]

    await app(scope, receive, send)
    return status.get("code")


def _batch(n):
    import json
    return json.dumps({"sentences": [f"Sentence {i}." for i in range(n)],
                       "voice": "af_heart", "speed": 1}).encode()


@pytest.mark.asyncio
async def test_a_batch_stops_soon_after_the_client_leaves(app):
    a, fake = app
    gone = asyncio.Event()
    task = asyncio.create_task(_call(a, "/v1/batch_synthesize", _batch(40), gone))
    await asyncio.sleep(0.2)              # a few sentences in
    gone.set()                            # Cancel
    await asyncio.wait_for(task, timeout=5)
    assert fake.calls < 10, f"kept synthesizing after the client left: {fake.calls} of 40"
    assert not endpoints.inference_lock.locked()


@pytest.mark.asyncio
async def test_a_queued_batch_whose_client_left_never_runs(app):
    a, fake = app
    first_gone, second_gone = asyncio.Event(), asyncio.Event()
    first = asyncio.create_task(_call(a, "/v1/batch_synthesize", _batch(8), first_gone))
    await asyncio.sleep(0.05)             # the first holds the lock
    second = asyncio.create_task(_call(a, "/v1/batch_synthesize", _batch(40), second_gone))
    await asyncio.sleep(0.05)
    second_gone.set()                     # cancelled while still waiting for the lock
    codes = await asyncio.wait_for(asyncio.gather(first, second), timeout=10)
    assert codes[0] == 200
    assert fake.calls == 8, f"the cancelled, queued batch still ran: {fake.calls - 8} extra sentences"


@pytest.mark.asyncio
async def test_a_batch_whose_client_stays_runs_to_the_end(app):
    a, fake = app
    code = await asyncio.wait_for(_call(a, "/v1/batch_synthesize", _batch(12), asyncio.Event()), timeout=10)
    assert code == 200 and fake.calls == 12


@pytest.mark.asyncio
async def test_a_single_synthesis_whose_client_left_never_runs(app):
    import json
    a, fake = app
    hold = asyncio.Event()
    first = asyncio.create_task(_call(a, "/v1/batch_synthesize", _batch(6), hold))
    await asyncio.sleep(0.05)
    gone = asyncio.Event()
    single = asyncio.create_task(_call(a, "/v1/synthesize", json.dumps(
        {"text": "Hi.", "voice": "af_heart", "speed": 1}).encode(), gone))
    await asyncio.sleep(0.05)
    gone.set()
    codes = await asyncio.wait_for(asyncio.gather(first, single), timeout=10)
    assert codes[0] == 200
    assert fake.calls == 6, f"the cancelled single synthesis still ran (statuses {codes})"
