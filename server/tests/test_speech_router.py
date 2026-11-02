"""Task 6: the speech routes. A fake transcriber stands in for the engine, so no model is loaded and no remote
server is called."""
import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.auth import scopes as token_scopes
from server.auth.users import resolve_or_provision_user
from server.routers import speech as speech_router
from server.speech import service
from server.speech.base import (AudioTooLong, BadAudio, EngineFailed, EngineNotReady, EngineStatus, EngineUnavailable,
                                Transcript)
from server.speech.languages import WHISPER_LANGUAGES

URL = "/v1/speech/transcriptions"
STATUS = "/v1/speech/status"


class FakeEngine:
    name = "local"
    model = "small"
    where = "this server"

    def __init__(self, state="ready", raises=None, detail=""):
        self.state, self.raises, self.detail = state, raises, detail
        self.warm_calls = 0
        self.status_calls = 0
        self.seen = []

    async def transcribe(self, audio, mime, language):
        self.seen.append((len(audio), mime, language))
        if self.raises:
            raise self.raises
        return Transcript("hello there", language or "en", 3.5)

    async def status(self):
        self.status_calls += 1
        return EngineStatus(self.state == "ready", self.state, self.detail)

    def warm(self):
        self.warm_calls += 1


@pytest.fixture
def engine(monkeypatch):
    fake = FakeEngine()
    service.reset_for_tests()
    monkeypatch.setattr(service, "_transcriber", fake)
    yield fake
    service.reset_for_tests()


async def _voice(db_conn, on=True):
    await db_conn.execute("DELETE FROM app_settings WHERE key = 'chat_options'")
    if on is not None:
        await db_conn.execute("INSERT INTO app_settings (key, value) VALUES ('chat_options', %s::jsonb)",
                              ('{"voice_input": true}' if on else '{"voice_input": false}',))


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv("CHAT_VOICE_INPUT", raising=False)


def _principal(caps=frozenset({"chat"}), scopes=None):
    account = frozenset(caps)
    return deps.Principal(user_id="u1", email="e@x.io", role="member",
                          capabilities=token_scopes.narrow(account, scopes), scopes=scopes,
                          account_capabilities=account)


class Pool:
    """The seam the speech routes take their short authentication connection through (routers/speech.py
    `speech_conn`). It hands out the test's transactional connection and counts who holds it right now."""

    def __init__(self, db_conn):
        self.db_conn, self.open, self.opened = db_conn, 0, 0

    @asynccontextmanager
    async def __call__(self):
        self.open += 1
        self.opened += 1
        try:
            yield self.db_conn
        finally:
            self.open -= 1


_WHO = {}


@pytest.fixture(autouse=True)
def _who(monkeypatch):
    """The routes resolve the caller on their short connection (deps.resolve_principal); a test says who."""
    async def resolve(request, conn):
        return _WHO["principal"]
    monkeypatch.setattr(deps, "resolve_principal", resolve)


def _app(db_conn, principal, pool=None):
    _WHO["principal"] = principal
    app = FastAPI()
    app.dependency_overrides[speech_router.speech_conn] = lambda: (pool or Pool(db_conn))
    app.include_router(speech_router.router)
    return app


def _client(db_conn, principal=None, pool=None):
    return httpx.AsyncClient(transport=ASGITransport(app=_app(db_conn, principal or _principal(), pool)),
                             base_url="http://t")


def _post(client, body=b"abc", language=None, name="rec.webm", ctype="audio/webm"):
    data = {} if language is None else {"language": language}
    return client.post(URL, files={"file": (name, body, ctype)}, data=data)


def _msg(r):
    return r.json()["detail"]["message"]


async def test_voice_on_returns_the_transcript(db_conn, engine):
    await _voice(db_conn)
    async with _client(db_conn) as c:
        r = await _post(c, language="si")
    assert r.status_code == 200
    assert r.json() == {"text": "hello there", "language": "si", "duration_s": 3.5}
    assert engine.seen == [(3, "audio/webm", "si")]


@pytest.mark.parametrize("language", [None, "auto"])
async def test_auto_and_missing_language_pass_none(db_conn, engine, language):
    await _voice(db_conn)
    async with _client(db_conn) as c:
        assert (await _post(c, language=language)).status_code == 200
    assert engine.seen[0][2] is None


async def test_voice_off_is_404_on_post_and_status_still_answers(db_conn, engine):
    await _voice(db_conn, on=None)
    async with _client(db_conn) as c:
        assert (await _post(c)).status_code == 404
        r = await c.get(STATUS)
    assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["ready"] is False
    # off means no engine is built, probed or warmed (F3)
    assert engine.status_calls == 0 and engine.warm_calls == 0 and engine.seen == []


async def test_voice_turned_off_explicitly_is_also_404(db_conn, engine):
    await _voice(db_conn, on=False)
    async with _client(db_conn) as c:
        assert (await _post(c)).status_code == 404


async def test_the_environment_switches_it_on_when_the_admin_hasnt_chosen(db_conn, engine, monkeypatch):
    await _voice(db_conn, on=None)
    monkeypatch.setenv("CHAT_VOICE_INPUT", "on")
    async with _client(db_conn) as c:
        assert (await _post(c)).status_code == 200


async def test_no_chat_capability_is_403_before_anything_else(db_conn, engine):
    await _voice(db_conn, on=None)   # off would be 404: the 403 comes first
    async with _client(db_conn, _principal(caps={"reader"})) as c:
        assert (await _post(c)).status_code == 403
        assert (await c.get(STATUS)).status_code == 403


async def test_a_pat_without_the_chat_scope_is_403(db_conn, engine):
    await _voice(db_conn)
    pat = _principal(caps={"chat", "reader"}, scopes=frozenset({"documents"}))
    async with _client(db_conn, pat) as c:
        assert (await _post(c)).status_code == 403
    chat_pat = _principal(caps={"chat", "reader"}, scopes=frozenset({"chat"}))
    async with _client(db_conn, chat_pat) as c:
        assert (await _post(c)).status_code == 200


async def test_over_10_mb_is_413(db_conn, engine):
    await _voice(db_conn)
    async with _client(db_conn) as c:
        r = await _post(c, body=b"x" * (10 * 1024 * 1024 + 1))
    assert r.status_code == 413 and _msg(r) == "The recording is over 10 MB."
    assert engine.seen == []


async def test_exactly_10_mb_is_accepted(db_conn, engine):
    await _voice(db_conn)
    async with _client(db_conn) as c:
        r = await _post(c, body=b"x" * (10 * 1024 * 1024))
    assert r.status_code == 200


async def test_an_empty_upload_is_400(db_conn, engine):
    await _voice(db_conn)
    async with _client(db_conn) as c:
        r = await _post(c, body=b"")
    assert r.status_code == 400 and _msg(r) == "That recording couldn't be read."
    assert engine.seen == []


@pytest.mark.parametrize("exc, status, message", [
    (AudioTooLong("x"), 413, "The recording is over 2 minutes."),
    (BadAudio("x"), 400, "That recording couldn't be read."),
    (service.AlreadyRunning(), 409, "A recording of yours is already being transcribed."),
    (service.Busy(), 503, "Voice input is busy. Try again in a moment."),
    (EngineNotReady("loading"), 503, "Voice input is still getting ready, try again in a minute"),
    (EngineFailed("The transcription server didn't answer"), 503, "The transcription server didn't answer"),
])
async def test_each_service_error_maps_to_its_status_and_words(db_conn, engine, monkeypatch, exc, status, message):
    await _voice(db_conn)

    async def boom(*a, **k):
        raise exc
    monkeypatch.setattr(speech_router.service, "transcribe_for", boom)
    async with _client(db_conn) as c:
        r = await _post(c)
    assert r.status_code == status and _msg(r) == message


async def test_an_engine_error_through_the_real_service_maps_the_same_way(db_conn, engine):
    await _voice(db_conn)
    engine.raises = EngineNotReady("The speech model is still loading")
    async with _client(db_conn) as c:
        r = await _post(c)
    assert r.status_code == 503 and _msg(r) == "Voice input is still getting ready, try again in a minute"


@pytest.mark.parametrize("language", ["xx", "english", "AUTO", "../x"])
async def test_an_unknown_language_is_422(db_conn, engine, language):
    await _voice(db_conn)
    async with _client(db_conn) as c:
        assert (await _post(c, language=language)).status_code == 422
    assert engine.seen == []


async def test_voice_off_beats_a_bad_language_and_a_bad_language_never_transcribes(db_conn, engine, monkeypatch):
    """Capability, then voice off (404), then the body is parsed in memory, then language (422), then the file.
    (Round 1 amended G4: `language` lives in the body, so it can only be checked after the capped parse.)"""
    await _voice(db_conn, on=None)
    async with _client(db_conn) as c:
        assert (await _post(c, language="xx")).status_code == 404     # off wins over a bad language
    await _voice(db_conn)
    async with _client(db_conn) as c:
        assert (await _post(c, language="xx")).status_code == 422


async def test_the_transcript_is_never_logged(db_conn, engine, caplog):
    await _voice(db_conn)
    with caplog.at_level(logging.DEBUG):
        async with _client(db_conn) as c:
            await _post(c)
    assert "hello there" not in caplog.text


async def test_status_lists_languages_sorted_by_name(db_conn, engine):
    await _voice(db_conn)
    async with _client(db_conn) as c:
        r = await c.get(STATUS)
    body = r.json()
    assert body["enabled"] is True and body["ready"] is True and body["state"] == "ready"
    assert body["max_seconds"] == 120
    names = [lang["name"] for lang in body["languages"]]
    assert names == sorted(names) and len(names) == len(WHISPER_LANGUAGES)
    assert {"code": "si", "name": "Sinhala"} in body["languages"]


async def test_status_lists_languages_when_off_too(db_conn, engine):
    await _voice(db_conn, on=None)
    async with _client(db_conn) as c:
        body = (await c.get(STATUS)).json()
    assert body["max_seconds"] == 120 and len(body["languages"]) == len(WHISPER_LANGUAGES)


async def test_a_never_warmed_engine_is_warmed_by_status_and_by_post(db_conn, engine):
    """G2: an admin's PUT lands on one worker; the others warm themselves on first use."""
    await _voice(db_conn)
    engine.state = "loading"
    async with _client(db_conn) as c:
        r = await c.get(STATUS)
        assert r.json()["ready"] is False and r.json()["state"] == "loading"
        assert engine.warm_calls == 1
        await _post(c)
    assert engine.warm_calls == 2


async def test_an_engine_in_error_is_not_retried_by_a_page_load(db_conn, engine):
    await _voice(db_conn)
    engine.state = "error"
    async with _client(db_conn) as c:
        r = await c.get(STATUS)
        await _post(c)
    assert r.json()["state"] == "error" and r.json()["ready"] is False
    assert engine.warm_calls == 0


async def test_a_ready_engine_is_not_warmed(db_conn, engine):
    await _voice(db_conn)
    async with _client(db_conn) as c:
        await c.get(STATUS)
    assert engine.warm_calls == 0


async def test_status_survives_an_engine_that_raises(db_conn, engine, monkeypatch):
    """G6: a config mistake or a down server must not turn the composer's status call into a 500."""
    await _voice(db_conn)

    async def boom():
        raise RuntimeError("secret detail")
    monkeypatch.setattr(engine, "status", boom)
    async with _client(db_conn) as c:
        r = await c.get(STATUS)
    assert r.status_code == 200
    assert r.json()["enabled"] is True and r.json()["ready"] is False and r.json()["state"] == "error"
    assert "secret detail" not in r.text


# ---------- fix round 1: the body is parsed in memory, with a running cap ----------

MB = 1024 * 1024


@pytest.fixture
def no_rollover(monkeypatch):
    """A recording must never reach a temp file: any SpooledTemporaryFile that rolls over fails the test."""
    import tempfile

    rolled = []

    def boom(self, *a, **k):
        rolled.append(True)    # the framework may turn this exception into a 400: the list is the real signal
        raise AssertionError("a recording was spooled to disk")
    monkeypatch.setattr(tempfile.SpooledTemporaryFile, "rollover", boom)
    yield rolled
    assert rolled == [], "a recording was spooled to a temp file"


async def test_a_3_mb_upload_never_rolls_over_to_a_temp_file(db_conn, engine, no_rollover):
    await _voice(db_conn)
    async with _client(db_conn) as c:
        r = await _post(c, body=b"x" * (3 * MB))
    assert r.status_code == 200
    assert engine.seen[0][0] == 3 * MB


def _multipart_headers(extra=None):
    return {"content-type": "multipart/form-data; boundary=BOUND", **(extra or {})}


class _Body:
    """A request body that records whether, and how much of it, the server read."""

    def __init__(self, chunks):
        self.chunks, self.pulled = list(chunks), 0

    async def __aiter__(self):
        for chunk in self.chunks:
            self.pulled += 1
            yield chunk


async def test_voice_off_never_reads_the_body(db_conn, engine):
    await _voice(db_conn, on=None)
    body = _Body([b"x" * MB] * 3)
    async with _client(db_conn) as c:
        r = await c.post(URL, content=body, headers=_multipart_headers())
    assert r.status_code == 404 and body.pulled == 0


async def test_a_declared_length_over_the_cap_is_413_with_the_body_unread(db_conn, engine):
    await _voice(db_conn)
    body = _Body([b"x" * MB])
    async with _client(db_conn) as c:
        r = await c.post(URL, content=body, headers=_multipart_headers({"content-length": str(11 * MB)}))
    assert r.status_code == 413 and _msg(r) == "The recording is over 10 MB."
    assert body.pulled == 0


async def test_a_chunked_body_with_no_length_that_runs_past_the_cap_is_413(db_conn, engine, no_rollover):
    await _voice(db_conn)
    opening = (b'--BOUND\r\nContent-Disposition: form-data; name="file"; filename="a.webm"\r\n'
               b'Content-Type: audio/webm\r\n\r\n')
    body = _Body([opening + b"x" * MB] + [b"x" * MB] * 11)
    async with _client(db_conn) as c:
        r = await c.post(URL, content=body, headers=_multipart_headers())
    assert r.status_code == 413 and _msg(r) == "The recording is over 10 MB."
    assert body.pulled < 12          # it stopped reading at the cap


async def test_a_file_field_sent_as_plain_text_is_400(db_conn, engine):
    await _voice(db_conn)
    async with _client(db_conn) as c:
        r = await c.post(URL, data={"file": "just text"})     # urlencoded: no file part at all
        r2 = await c.post(URL, files={"other": ("a.webm", b"abc", "audio/webm")})
    assert r.status_code == 400 and r2.status_code == 400
    assert _msg(r) == "That recording couldn't be read."
    boundary_text = (b'--B\r\nContent-Disposition: form-data; name="file"\r\n\r\nplain\r\n--B--\r\n')
    async with _client(db_conn) as c:
        r3 = await c.post(URL, content=boundary_text, headers={"content-type": "multipart/form-data; boundary=B"})
    assert r3.status_code == 400 and _msg(r3) == "That recording couldn't be read."


async def test_a_body_that_is_not_multipart_is_400(db_conn, engine):
    await _voice(db_conn)
    async with _client(db_conn) as c:
        r = await c.post(URL, content=b"abc", headers={"content-type": "application/json"})
        r2 = await c.post(URL, content=b"abc", headers={"content-type": "multipart/form-data"})  # no boundary
        r3 = await c.post(URL, content=b"garbage", headers=_multipart_headers())
    assert [r.status_code, r2.status_code, r3.status_code] == [400, 400, 400]
    assert _msg(r) == "That recording couldn't be read."
    assert engine.seen == []


async def test_a_client_that_drops_mid_upload_is_a_quiet_400_not_a_traceback(db_conn, engine, caplog):
    """A closed tab raises ClientDisconnect out of request.stream(): a normal event, so no ERROR line, no traceback."""
    from fastapi import HTTPException
    from starlette.requests import ClientDisconnect

    class _Dropped:
        from starlette.datastructures import Headers
        headers = Headers({"content-type": "multipart/form-data; boundary=BOUND"})

        async def stream(self):
            yield (b'--BOUND\r\nContent-Disposition: form-data; name="file"; filename="a.webm"\r\n'
                   b'Content-Type: audio/webm\r\n\r\n' + b"x" * 1000)
            raise ClientDisconnect()

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(HTTPException) as e:
            await speech_router._read_upload(_Dropped())
    assert e.value.status_code == 400
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert [r for r in caplog.records if r.levelno == logging.DEBUG and "disconnect" in r.getMessage().lower()]
    assert not any(r.exc_info for r in caplog.records)


async def test_status_is_bounded_when_the_engine_hangs(db_conn, engine, monkeypatch):
    import asyncio
    await _voice(db_conn)

    async def hang():
        await asyncio.sleep(30)
    monkeypatch.setattr(engine, "status", hang)
    monkeypatch.setattr(speech_router, "STATUS_TIMEOUT_S", 0.05)
    async with _client(db_conn) as c:
        r = await c.get(STATUS)
    assert r.status_code == 200
    assert (r.json()["enabled"], r.json()["ready"], r.json()["state"]) == (True, False, "error")


async def test_a_failed_pre_transcribe_status_check_is_logged_at_debug(db_conn, engine, monkeypatch, caplog):
    await _voice(db_conn)

    async def boom():
        raise RuntimeError("secret detail")
    monkeypatch.setattr(engine, "status", boom)
    with caplog.at_level(logging.DEBUG, logger="server.routers.speech"):
        async with _client(db_conn) as c:
            r = await _post(c)
    assert r.status_code == 200
    assert any("RuntimeError" in rec.getMessage() for rec in caplog.records)
    assert "secret detail" not in caplog.text


# ---------- final fix wave, I3: no pooled connection is held while a recording uploads or transcribes ----------

async def test_the_connection_is_back_in_the_pool_while_a_transcription_runs(db_conn, engine):
    await _voice(db_conn)
    pool = Pool(db_conn)
    started, release = asyncio.Event(), asyncio.Event()
    held_during = []

    async def slow(audio, mime, language):
        held_during.append(pool.open)
        started.set()
        await release.wait()
        return Transcript("slow words", "en", 1.0)
    engine.transcribe = slow
    async with _client(db_conn, pool=pool) as c:
        task = asyncio.ensure_future(_post(c))
        await asyncio.wait_for(started.wait(), 5)
        assert pool.open == 0 and pool.opened == 1      # authenticated on a short connection, then let go
        release.set()
        r = await task
    assert r.status_code == 200 and held_during == [0]


async def test_the_connection_is_back_in_the_pool_while_the_status_probe_runs(db_conn, engine):
    await _voice(db_conn)
    pool = Pool(db_conn)
    seen = []

    async def probe():
        seen.append(pool.open)
        return EngineStatus(True, "ready", "")
    engine.status = probe
    async with _client(db_conn, pool=pool) as c:
        assert (await c.get(STATUS)).status_code == 200
    assert seen == [0]


async def test_the_connection_is_back_in_the_pool_before_the_upload_is_read(db_conn, engine):
    await _voice(db_conn)
    pool = Pool(db_conn)
    body = _Body([b"x" * 10])
    async with _client(db_conn, pool=pool) as c:
        await c.post(URL, content=body, headers=_multipart_headers())
    assert pool.open == 0 and pool.opened == 1


async def test_refusals_from_the_short_connection_keep_their_codes(db_conn, engine):
    """401 (no credential), 403 (no chat capability, or a PAT without the chat scope), 404 (voice off)."""
    from fastapi import HTTPException

    async def unauthenticated(request, conn):
        raise HTTPException(status_code=401, detail="Authentication required")
    await _voice(db_conn)
    pool = Pool(db_conn)
    app = _app(db_conn, _principal(), pool)
    real = deps.resolve_principal
    deps.resolve_principal = unauthenticated
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            assert (await _post(c)).status_code == 401
            assert (await c.get(STATUS)).status_code == 401
    finally:
        deps.resolve_principal = real
    assert pool.open == 0                            # a refusal gives the connection back too
    async with _client(db_conn, _principal(caps={"reader"}), pool) as c:
        r = await _post(c)
        assert r.status_code == 403 and r.json()["detail"] == {"error": "missing_capability", "capability": "chat"}
    async with _client(db_conn, _principal(caps={"chat"}, scopes=frozenset({"documents"})), pool) as c:
        assert (await _post(c)).status_code == 403
    await _voice(db_conn, on=None)
    async with _client(db_conn, pool=pool) as c:
        assert (await _post(c)).status_code == 404
    assert pool.open == 0


# ---------- final fix wave, I5: a failed load heals on every worker; the words say what is true ----------

async def test_an_engine_in_error_retries_at_most_once_every_five_minutes(db_conn, engine, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(service, "_now", lambda: now[0])
    await _voice(db_conn)
    engine.state = "error"
    async with _client(db_conn) as c:
        async def visit(seconds):
            now[0] += seconds
            await c.get(STATUS)
            return engine.warm_calls
        assert await visit(0) == 0            # first sight of the failure starts the clock
        assert await visit(299) == 0          # a page load inside 5 minutes retries nothing
        assert await visit(1) == 1            # 300 s after it: one retry
        assert await visit(10) == 1 and await visit(289) == 1
        assert await visit(1) == 2            # and again 300 s after that
        engine.state = "ready"
        assert await visit(1000) == 2         # a healthy engine is never warmed


async def test_either_route_can_make_the_retry(db_conn, engine, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(service, "_now", lambda: now[0])
    await _voice(db_conn)
    engine.state = "error"
    async with _client(db_conn) as c:
        await c.get(STATUS)
        now[0] += service.RETRY_AFTER_S
        await _post(c)
    assert service.RETRY_AFTER_S == 300 and engine.warm_calls == 1


async def test_a_broken_engine_says_it_is_not_working_and_a_loading_one_says_wait(db_conn, engine):
    await _voice(db_conn)
    async with _client(db_conn) as c:
        engine.raises = EngineUnavailable("ValueError")
        r = await _post(c)
        assert r.status_code == 503 and _msg(r) == "Voice input isn't working right now. Ask your admin."
        engine.raises = EngineNotReady("The speech model is still loading")
        r = await _post(c)
        assert r.status_code == 503 and _msg(r) == "Voice input is still getting ready, try again in a minute"


async def test_the_pre_transcribe_probe_is_for_the_local_engine_only(db_conn, engine):
    await _voice(db_conn)
    engine.name = "openai"
    async with _client(db_conn) as c:
        assert (await _post(c)).status_code == 200
    assert engine.status_calls == 0           # no GET /models per recording on a remote engine
    engine.name = "local"
    async with _client(db_conn) as c:
        await _post(c)
    assert engine.status_calls == 1
