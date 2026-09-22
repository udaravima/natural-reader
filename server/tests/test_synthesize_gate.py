"""Regression coverage for the /v1/synthesize and /v1/batch_synthesize
reader-capability gate (server/endpoints.py).

No other test file imports server.endpoints (see test_app_wiring.py's
docstring): the module does `from .model import kokoro`, and server/model.py
loads a ~325 MB ONNX model — and can exit(1) — at IMPORT time, not lazily.
Importing it for real in a unit test would be slow, environment-dependent
(needs the model files on disk), and could kill the test process outright.

So server.model is stubbed in sys.modules BEFORE server.endpoints is
imported. Python's import machinery checks sys.modules before executing a
module's source, so `from .model import kokoro` inside endpoints.py resolves
against this stub instead of running the real server/model.py — the router
wires up normally, kokoro is just a MagicMock instead of a loaded model. The
gate itself (require_capability("reader")) runs entirely in FastAPI's
dependency layer, before the stubbed kokoro would ever be touched, so this
is enough to prove the gate without faking inference.
"""
import sys
import types
from unittest.mock import MagicMock

if "server.model" not in sys.modules:
    _stub = types.ModuleType("server.model")
    _stub.kokoro = MagicMock()
    sys.modules["server.model"] = _stub

import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport  # noqa: E402

from server.auth import deps  # noqa: E402
from server import endpoints  # noqa: E402  (must import after the stub above)


def _app(principal):
    app = FastAPI()
    app.dependency_overrides[deps.get_current_user] = lambda: principal
    app.include_router(endpoints.router)
    return app


def _client(app):
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_synthesize_requires_reader_capability():
    p = deps.Principal(user_id="u1", email="a@x.io", role="member", capabilities=frozenset())
    async with _client(_app(p)) as client:
        r = await client.post("/v1/synthesize", json={"text": "hello"})
    assert r.status_code == 403
    assert r.json()["detail"] == {"error": "missing_capability", "capability": "reader"}


async def test_batch_synthesize_requires_reader_capability():
    p = deps.Principal(user_id="u1", email="a@x.io", role="member", capabilities=frozenset())
    async with _client(_app(p)) as client:
        r = await client.post("/v1/batch_synthesize", json={"sentences": ["hello"]})
    assert r.status_code == 403
    assert r.json()["detail"] == {"error": "missing_capability", "capability": "reader"}


async def test_synthesize_allows_reader_capability_holder_past_the_gate():
    """Positive control: holding "reader" clears the gate — the request then
    reaches the stubbed kokoro.create (a MagicMock), which returns a
    MagicMock rather than a real (audio, sample_rate) tuple, so this fails
    downstream with a 500 from the handler's own try/except, NOT a 403. That
    contrast is what proves the gate, not business logic, produced the 403
    in the tests above."""
    p = deps.Principal(user_id="u1", email="a@x.io", role="member",
                        capabilities=frozenset({"reader"}))
    async with _client(_app(p)) as client:
        r = await client.post("/v1/synthesize", json={"text": "hello"})
    assert r.status_code != 403
