import asyncio
import base64
import json

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.chat import context as ctx_mod
from server.chat import orchestrator, store
from server.chat.tools import search_documents as sd_tool
from server.llm.types import Capabilities, Finish, TextDelta, Usage
from server.routers import chat_turns
from server.services import inference_budget
from server.tests.chat_harness import FakeRouter, member, shim_pool

BODY = {"message": {"content": "Hi"}, "model": "ollama:m", "context": {"timezone": "UTC"}}
PNG = base64.b64encode(b"\x89PNG fake").decode()


@pytest.fixture
def app(db_conn, monkeypatch):
    shim_pool(monkeypatch, db_conn, store, orchestrator, ctx_mod, sd_tool, chat_turns)
    fake = FakeRouter()
    monkeypatch.setattr(chat_turns, "get_router", lambda: fake)
    for var in ("INFERENCE_DAILY_TOKEN_BUDGET", "CHAT_MAX_REQUEST_MB"):
        monkeypatch.delenv(var, raising=False)
    application = FastAPI()
    application.include_router(chat_turns.router)
    current: dict = {}
    application.dependency_overrides[deps.get_current_user] = lambda: current["p"]
    return application, fake, current


async def _post(app, principal, sid="s-1", body=BODY, **kw):
    application, _, current = app
    current["p"] = principal
    async with httpx.AsyncClient(transport=ASGITransport(app=application), base_url="http://t") as c:
        if "content" in kw:
            return await c.post(f"/v1/chat/sessions/{sid}/turns", headers={"content-type": "application/json"}, **kw)
        return await c.post(f"/v1/chat/sessions/{sid}/turns", json=body)


def _frames(text):
    out = []
    for frame in text.split("\n\n"):
        if frame.startswith("data: "):
            data = frame[6:]
            out.append(data if data == "[DONE]" else json.loads(data))
    return out


async def _one(conn, sql, params=()):
    cur = await conn.execute(sql, params)
    return await cur.fetchone()


async def test_a_turn_streams_sse_and_creates_the_session(db_conn, app):
    alice = await member(db_conn, "alice")
    body = {**BODY, "session": {"pins": [{"id": "p1", "text": "pinned"}]}}
    r = await _post(app, alice, body=body)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache" and r.headers["x-accel-buffering"] == "no"
    frames = _frames(r.text)
    assert frames[0]["type"] == "start" and frames[-2]["type"] == "finish" and frames[-1] == "[DONE]"
    assert await _one(db_conn, "SELECT title, pins, active_turn_id FROM chat_sessions WHERE id='s-1'") == (
        "Hi", [{"id": "p1", "text": "pinned"}], None)


async def test_someone_elses_session_is_404(db_conn, app):
    alice, bob = await member(db_conn, "alice"), await member(db_conn, "bob")
    await db_conn.execute("INSERT INTO chat_sessions (id, user_id) VALUES ('s-1', %s)", (alice.user_id,))
    r = await _post(app, bob)
    assert r.status_code == 404 and r.json()["detail"]["error"] == "not_found"


async def test_second_tab_gets_409_and_first_turn_is_untouched(db_conn, app):
    alice = await member(db_conn, "alice")
    first = await store.begin_turn(session_id="s-1", user_id=alice.user_id, model_id="ollama:m", text="first",
                                   attachments=[], new_session_pins=None)
    r = await _post(app, alice)
    assert r.status_code == 409 and r.json()["detail"]["error"] == "turn_in_progress"
    assert (await _one(db_conn, "SELECT active_turn_id FROM chat_sessions WHERE id='s-1'"))[0] == first.turn_id
    assert (await _one(db_conn, "SELECT status FROM chat_messages WHERE id=%s", (first.turn_id,)))[0] == "streaming"


async def test_body_over_the_cap_is_413(db_conn, app, monkeypatch):
    monkeypatch.setenv("CHAT_MAX_REQUEST_MB", "1")
    alice = await member(db_conn, "alice")
    big = base64.b64encode(b"x" * 1_200_000).decode()
    body = {**BODY, "message": {"content": "Hi", "attachments": [
        {"kind": "image", "mime": "image/png", "name": "a.png", "base64": big}]}}
    r = await _post(app, alice, body=body)
    assert r.status_code == 413
    assert r.json()["detail"] == {"error": "too_large", "message": "Attachments too large (limit 1 MB).", "limit_mb": 1}


async def test_model_not_allowed_is_422(db_conn, app):
    app[1].allowed = False
    r = await _post(app, await member(db_conn, "alice"))
    assert r.status_code == 422 and r.json()["detail"]["error"] == "model_not_allowed"


async def test_model_not_in_the_providers_list_is_422_before_claiming(db_conn, app):
    """A saved id can resolve to a real provider (is_allowed passes: no
    allow-list configured) yet name a model that provider doesn't actually
    have — the walk's bug (a stale 'ollama:qwen3.5:latest' id sent verbatim to
    the first ollama provider). Refused before the turn is claimed, so the
    session is never touched."""
    app[1].listed = False
    r = await _post(app, await member(db_conn, "alice"))
    assert r.status_code == 422
    assert r.json()["detail"] == {"error": "model_not_allowed", "message": "This model isn't available."}
    assert await _one(db_conn, "SELECT 1 FROM chat_sessions WHERE id='s-1'") is None


async def test_attachment_the_model_cant_read_is_422_and_nothing_is_written(db_conn, app):
    app[1].caps = Capabilities(tools=True, vision=False)
    body = {**BODY, "message": {"content": "look", "attachments": [
        {"kind": "image", "mime": "image/png", "name": "a.png", "base64": PNG}]}}
    r = await _post(app, await member(db_conn, "alice"), body=body)
    assert r.status_code == 422
    assert r.json()["detail"] == {"error": "attachment_unsupported", "message": "This model can't read images.",
                                  "kind": "image"}
    assert await _one(db_conn, "SELECT 1 FROM chat_sessions WHERE id='s-1'") is None


@pytest.mark.parametrize("att", [
    {"kind": "image", "mime": "image/png", "name": "a.png", "base64": "not base64!!"},
    {"kind": "image", "mime": "audio/wav", "name": "a.wav", "base64": PNG},
])
async def test_unreadable_attachment_is_422(db_conn, app, att):
    body = {**BODY, "message": {"content": "look", "attachments": [att]}}
    r = await _post(app, await member(db_conn, "alice"), body=body)
    assert r.status_code == 422 and r.json()["detail"]["error"] == "invalid_attachment"


async def test_empty_message_without_pins_is_422(db_conn, app):
    r = await _post(app, await member(db_conn, "alice"), body={**BODY, "message": {"content": "  "}})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "empty_message"


async def test_pins_only_message_is_allowed(db_conn, app):
    body = {**BODY, "message": {"content": ""}, "session": {"pins": [{"id": "p1", "text": "x"}]}}
    r = await _post(app, await member(db_conn, "alice"), body=body)
    assert r.status_code == 200


async def test_budget_already_spent_is_429_with_reset_time(db_conn, app):
    alice = await member(db_conn, "alice")
    await db_conn.execute("UPDATE users SET inference_daily_token_budget = 10 WHERE id = %s", (alice.user_id,))
    await inference_budget.record_usage(db_conn, alice.user_id, 10, 0)
    r = await _post(app, alice)
    detail = r.json()["detail"]
    assert r.status_code == 429 and detail["error"] == "budget_exhausted"
    assert detail["remaining_tokens"] == 0 and detail["reset_at"].endswith("Z")


async def test_no_providers_is_503(db_conn, app):
    app[1].providers = False
    r = await _post(app, await member(db_conn, "alice"))
    assert r.status_code == 503 and r.json()["detail"]["error"] == "no_providers"


async def test_missing_chat_capability_is_403(db_conn, app):
    r = await _post(app, await member(db_conn, "alice", caps=("reader",)))
    assert r.status_code == 403


async def test_unknown_fields_are_422(db_conn, app):
    r = await _post(app, await member(db_conn, "alice"), body={**BODY, "history": []})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "invalid_request"


async def test_bad_timezone_still_streams(db_conn, app):
    r = await _post(app, await member(db_conn, "alice"), body={**BODY, "context": {"timezone": "Mars/Olympus"}})
    assert r.status_code == 200
    # v2.4: the date is in the rules (to the day, in the browser's timezone,
    # else UTC); the user's message is only what they typed.
    system, user = app[1].calls[0]["messages"][0].content, app[1].calls[0]["messages"][-1].content
    assert "Today is " in system and "(UTC)." in system and user == "Hi"


async def test_bare_model_names_are_canonicalized(db_conn, app):
    await _post(app, await member(db_conn, "alice"), body={**BODY, "model": "qwen2.5:7b"})
    assert app[1].calls[0]["model"] == "ollama:qwen2.5:7b"
    assert (await _one(db_conn, "SELECT model FROM chat_sessions WHERE id='s-1'"))[0] == "ollama:qwen2.5:7b"


# ---- Controller ruling 1: deterministic close on client disconnect ----
#
# httpx's ASGITransport (used by every test above) buffers the WHOLE response
# before returning it, so it can never deliver an `http.disconnect` while a
# response is mid-stream — it literally has no way to reproduce this bug.
# These tests drive the ASGI app callable directly instead.

def _turn_scope(body: bytes) -> dict:
    return {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
        "path": "/v1/chat/sessions/s-1/turns", "raw_path": b"/v1/chat/sessions/s-1/turns",
        "query_string": b"", "headers": [(b"content-type", b"application/json"),
                                         (b"content-length", str(len(body)).encode())],
        "client": ("test", 123), "server": ("test", 80), "scheme": "http", "root_path": "",
    }


class _StuckRouter:
    """A "model call" that yields one delta and then hangs — the turn is
    genuinely still streaming for as long as this test needs it to be."""

    def __init__(self, hold: asyncio.Event) -> None:
        self._hold = hold

    def has_providers(self) -> bool:
        return True

    def is_allowed(self, model_id: str) -> bool:
        return True

    async def is_listed(self, model_id: str) -> bool:
        return True

    def canonical_id(self, model_id: str) -> str:
        return model_id if model_id.startswith("ollama:") else f"ollama:{model_id}"

    async def capabilities(self, model_id: str) -> Capabilities:
        return Capabilities(tools=True, thinking=True, vision=True, audio=None, context_window=None)

    def provider_kind(self, model_id: str) -> str | None:
        return "ollama"

    def stream_chat(self, model_id, messages, tools, settings):
        return self._gen()

    async def _gen(self):
        yield TextDelta("Hi")
        await self._hold.wait()   # never set in this test
        yield TextDelta(" there.")   # pragma: no cover — never reached here
        yield Usage(5, 2)
        yield Finish("stop")


async def _drive_disconnect_during_send(db_conn, app, monkeypatch):
    """Start a turn, let it stream one delta, then hang the ASGI transport's
    `send()` on THAT exact frame — so when the disconnect below is delivered,
    the task carrying the cancellation is parked inside `send()`, never inside
    chat_turns._sse's own frame. That is the precise race the controller's
    ruling describes: Starlette's disconnect machinery (a task-group cancel,
    racing a concurrent `listen_for_disconnect()` against `stream_response()`)
    can land the cancellation on either side of that boundary depending on
    what the response happens to be doing at that instant, and only landing
    OUTSIDE the generator is a problem — landing inside it, cancellation
    propagates through run_turn's own try/except exactly like a direct
    `task.cancel()` would (see test_chat_orchestrator's cancellation tests).
    Delivers the disconnect and returns the turn id once the ASGI call itself
    has returned; callers inspect the DB from there.
    """
    application, _, current = app
    alice = await member(db_conn, "alice")
    current["p"] = alice
    hold = asyncio.Event()
    monkeypatch.setattr(chat_turns, "get_router", lambda: _StuckRouter(hold))

    body = json.dumps(BODY).encode()
    receive_q: asyncio.Queue = asyncio.Queue()
    await receive_q.put({"type": "http.request", "body": body, "more_body": False})

    async def receive():
        return await receive_q.get()

    stuck_at_hi = asyncio.Event()

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body", b"").startswith(b"data: "):
            # Each SSE frame is `data: {json}\n\n` (chat_turns._sse) — strip the
            # framing before parsing the payload underneath.
            payload = message["body"][len(b"data: "):].rstrip(b"\n")
            try:
                frame = json.loads(payload)
            except ValueError:
                frame = None
            if isinstance(frame, dict) and frame.get("type") == "text-delta" and frame.get("delta") == "Hi":
                stuck_at_hi.set()
                await asyncio.Event().wait()   # a transport write that never completes

    task = asyncio.create_task(application(_turn_scope(body), receive, send))
    await asyncio.wait_for(stuck_at_hi.wait(), 5)

    turn_id = (await _one(db_conn, "SELECT active_turn_id FROM chat_sessions WHERE id='s-1'"))[0]
    assert turn_id is not None, "the turn must genuinely hold its claim before we disconnect"
    assert (await _one(db_conn, "SELECT status FROM chat_messages WHERE id=%s", (turn_id,)))[0] == "streaming"

    await receive_q.put({"type": "http.disconnect"})
    await asyncio.wait_for(task, 5)   # the ASGI call itself must return promptly either way
    return turn_id


async def test_disconnect_mid_stream_closes_deterministically_and_saves_aborted(db_conn, app, monkeypatch):
    """With _EagerCloseStreamingResponse, the disconnect above reliably ends
    the turn — the claim is released and the partial reply is saved 'aborted'
    — instead of leaking the claim until the abandoned generator happens to be
    garbage-collected. Verified RED against a plain StreamingResponse and
    GREEN against _EagerCloseStreamingResponse (see task-9-report.md)."""
    turn_id = await _drive_disconnect_during_send(db_conn, app, monkeypatch)

    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5
    row = claim = None
    while loop.time() < deadline:
        row = await _one(db_conn, "SELECT status, content FROM chat_messages WHERE id=%s", (turn_id,))
        claim = (await _one(db_conn, "SELECT active_turn_id FROM chat_sessions WHERE id='s-1'"))[0]
        if row[0] == "aborted" and claim is None:
            break
        await asyncio.sleep(0.05)
    assert row == ("aborted", "Hi"), f"message never reached aborted (last seen: {row})"
    assert claim is None, "the session's claim must be released so the next message isn't 409'd forever"


async def test_orchestrator_exception_ends_in_internal_error_then_done(db_conn, app, monkeypatch):
    """Ruling R5: if the orchestrator ever raises OUT of its own handling
    (a bug, not a refusal it already turned into its own `error` event), the
    route still owes the client a terminal frame — a generic internal_error,
    never the exception's own text — followed by [DONE]."""
    alice = await member(db_conn, "alice")

    async def _boom(req, claim, *, router, cfg, deployment_budget):
        yield {"type": "start", "seq": 1, "runId": claim.turn_id, "sessionId": claim.session_id,
              "userMessageId": claim.user_message_id, "messageId": claim.assistant_message_id}
        raise RuntimeError("super secret db password 12345")

    monkeypatch.setattr(chat_turns, "run_turn", _boom)
    r = await _post(app, alice)
    assert r.status_code == 200
    frames = _frames(r.text)
    assert frames[0]["type"] == "start"
    assert frames[-2] == {"type": "error", "seq": 2, "code": "internal_error",
                          "message": chat_turns.INTERNAL_ERROR_MESSAGE}
    assert frames[-1] == "[DONE]"
    assert "secret" not in r.text and "12345" not in r.text


# ---- Final review I1: a disconnect before the stream starts ----

async def test_disconnect_before_the_stream_starts_releases_the_claim(db_conn, app):
    """Send then Stop at once: the client is gone before the response starts.
    begin_turn has already committed the claim in the handler. Starlette
    cancels the response while it is still sending `http.response.start`, so
    `_sse` (and so run_turn) never runs, and run_turn's own finally never
    saves anything. The claim must still be released and the empty reply
    saved 'aborted', or the resend is 409'd for ~60 s.

    Where the cancellation lands: anyio never cancels a task that hasn't
    started, so the response task always runs up to its first real
    suspension. uvicorn's h11 `send` returns without suspending once the
    client is gone, so there the stream starts and run_turn's own cancel
    path saves `aborted`; the leak needs a `send(http.response.start)` that
    suspends (uvicorn under write flow control, or another ASGI server). This
    test's `send` yields on the start message to put the cancellation there."""
    application, _, current = app
    alice = await member(db_conn, "alice")
    current["p"] = alice
    body = json.dumps(BODY).encode()
    receive_q: asyncio.Queue = asyncio.Queue()
    await receive_q.put({"type": "http.request", "body": body, "more_body": False})
    await receive_q.put({"type": "http.disconnect"})   # queued before the handler even returns
    sent: list = []

    async def receive():
        return await receive_q.get()

    async def send(message):
        sent.append(message)
        if message["type"] == "http.response.start":
            await asyncio.sleep(0.01)   # a transport write that yields to the loop

    await asyncio.wait_for(application(_turn_scope(body), receive, send), 5)
    assert not any(m.get("body", b"").startswith(b"data: ") for m in sent), "the stream must not have started"

    assert (await _one(db_conn, "SELECT active_turn_id FROM chat_sessions WHERE id='s-1'"))[0] is None
    status, reason, content = await _one(
        db_conn, "SELECT status, finish_reason, content FROM chat_messages WHERE role='assistant'")
    assert (status, reason, content) == ("aborted", "aborted", "")
    kinds = [r[0] for r in await (await db_conn.execute(
        "SELECT kind FROM chat_events WHERE session_id='s-1' ORDER BY id")).fetchall()]
    assert kinds == ["aborted"]

    again = await _post(app, alice)
    assert again.status_code == 200 and _frames(again.text)[-2]["type"] == "finish"


# ---- Final review I4: SSE keep-alive during silent phases ----

class _PausingRouter(_StuckRouter):
    """Yields, goes quiet for `pause` seconds (a tool run, a long prompt
    evaluation), then finishes normally."""

    def __init__(self, pause: float) -> None:
        self._pause = pause

    async def _gen(self):
        yield TextDelta("Hi")
        await asyncio.sleep(self._pause)
        yield TextDelta(" there.")
        yield Usage(5, 2)
        yield Finish("stop")


async def test_a_silent_phase_sends_keep_alive_comments_and_the_turn_still_completes(db_conn, app, monkeypatch):
    monkeypatch.setattr(chat_turns, "SSE_KEEPALIVE_S", 0.05)
    monkeypatch.setattr(chat_turns, "get_router", lambda: _PausingRouter(0.3))
    r = await _post(app, await member(db_conn, "alice"))
    assert r.status_code == 200
    raw = r.text.split("\n\n")
    assert raw.count(": keep-alive") >= 3   # ~0.3 s of silence / 0.05 s
    # Keep-alives sit BETWEEN events: the one delta before the pause, then the rest.
    first_ka = raw.index(": keep-alive")
    assert '"delta":"Hi"' in raw[first_ka - 1] and '"delta":" there."' in raw[first_ka + raw[first_ka:].index(
        next(f for f in raw[first_ka:] if f != ": keep-alive"))]
    frames = _frames(r.text)
    assert [f["type"] for f in frames[:-1] if isinstance(f, dict)][-1] == "finish" and frames[-1] == "[DONE]"
    assert [f["seq"] for f in frames[:-1]] == list(range(1, len(frames)))
    row = await _one(db_conn, "SELECT status, content FROM chat_messages WHERE role='assistant'")
    assert row == ("complete", "Hi there.")
    assert (await _one(db_conn, "SELECT active_turn_id FROM chat_sessions WHERE id='s-1'"))[0] is None


async def test_no_keep_alive_when_events_keep_coming(db_conn, app, monkeypatch):
    monkeypatch.setattr(chat_turns, "SSE_KEEPALIVE_S", 5.0)
    r = await _post(app, await member(db_conn, "alice"))
    assert ": keep-alive" not in r.text


async def test_disconnect_during_a_silent_phase_saves_aborted_and_releases_the_claim(db_conn, app, monkeypatch):
    """I4 runs each wait for the next event as its own task. A hang-up while
    the turn is silent (here: after a keep-alive, the model stuck after "Hi")
    lands in that wait, not in run_turn, and must still end the turn:
    'aborted' with the partial text, claim released."""
    monkeypatch.setattr(chat_turns, "SSE_KEEPALIVE_S", 0.05)
    application, _, current = app
    current["p"] = await member(db_conn, "alice")
    monkeypatch.setattr(chat_turns, "get_router", lambda: _StuckRouter(asyncio.Event()))
    body = json.dumps(BODY).encode()
    receive_q: asyncio.Queue = asyncio.Queue()
    await receive_q.put({"type": "http.request", "body": body, "more_body": False})
    kept_alive = asyncio.Event()

    async def receive():
        return await receive_q.get()

    async def send(message):
        if message.get("body") == chat_turns.KEEPALIVE_FRAME:
            kept_alive.set()

    task = asyncio.create_task(application(_turn_scope(body), receive, send))
    await asyncio.wait_for(kept_alive.wait(), 5)
    await receive_q.put({"type": "http.disconnect"})
    await asyncio.wait_for(task, 5)

    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5
    row = claim = None
    while loop.time() < deadline:
        row = await _one(db_conn, "SELECT status, content FROM chat_messages WHERE role='assistant'")
        claim = (await _one(db_conn, "SELECT active_turn_id FROM chat_sessions WHERE id='s-1'"))[0]
        if row[0] == "aborted" and claim is None:
            break
        await asyncio.sleep(0.05)
    assert row == ("aborted", "Hi") and claim is None
