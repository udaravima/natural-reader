"""Task 6: the speech routes. A fake transcriber stands in for the engine, so no model is loaded and no remote
server is called."""
import asyncio
import json
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
from server.speech import catalogue, service
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


class Running(FakeEngine):
    """A local engine on the admin's model: it has a `spec`, so a different `wanted` replaces it."""

    def __init__(self):
        super().__init__()
        self.spec = catalogue.all_models({})["whisper-small-ta"]
        self.languages = ("ta",)


@pytest.fixture
def running(monkeypatch):
    fake = Running()
    service.reset_for_tests()
    monkeypatch.setattr(service, "_transcriber", fake)
    monkeypatch.setenv("CHAT_VOICE_INPUT", "on")
    monkeypatch.delenv("SPEECH_MODEL", raising=False)
    yield fake
    service.reset_for_tests()


async def test_a_failed_options_read_keeps_the_model_running(db_conn, running, monkeypatch):
    # The fallback knows nothing about what an admin chose: `small` from it would drop a loaded model, and the
    # next good read would load the first one again.
    async def boom(conn):
        raise RuntimeError("blip")
    monkeypatch.setattr(speech_router.chat_options, "load_chat_options", boom)
    async with _client(db_conn) as c:
        r = await c.get(STATUS)
    assert r.status_code == 200 and r.json()["enabled"] is True
    assert service.get_transcriber() is running


async def test_a_switch_during_an_upload_swaps_nobodys_model(db_conn, pooled, monkeypatch):
    # The options were read before the upload. An admin switches while it arrives, and a status call loads the new
    # model on this worker. There is no one engine to swap any more (speech/service.py's pool): the clip is heard by
    # the model its own request chose, and the new one stays loaded for whoever asked for it.
    await _voice(db_conn)
    await db_conn.execute("""UPDATE app_settings SET value = value || '{"speech_model": "whisper-small-ta"}'::jsonb
                             WHERE key = 'chat_options'""")
    real_read = speech_router._read_upload

    async def read(request):
        out = await real_read(request)
        service.get_transcriber(wanted=catalogue.all_models({})["tiny"])   # the switch lands while the upload arrives
        return out
    monkeypatch.setattr(speech_router, "_read_upload", read)
    async with _client(db_conn) as c:
        r = await _post(c)
    assert r.status_code == 200 and r.json()["model"] == "whisper-small-ta"
    assert pooled["whisper-small-ta"].seen and "tiny" in service._pool


async def test_voice_on_returns_the_transcript(db_conn, engine):
    await _voice(db_conn)
    async with _client(db_conn) as c:
        r = await _post(c, language="si")
    assert r.status_code == 200
    assert r.json() == {"text": "hello there", "language": "si", "duration_s": 3.5,
                        "no_speech": None, "avg_logprob": None, "model": "small"}
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


async def test_a_recording_too_long_for_google_says_a_minute(db_conn, engine, monkeypatch):
    await _voice(db_conn)
    engine.name = "google"
    monkeypatch.setenv("SPEECH_ENGINE", "google")

    async def boom(*a, **k):
        raise AudioTooLong("x")
    monkeypatch.setattr(speech_router.service, "transcribe_for", boom)
    async with _client(db_conn) as c:
        r = await _post(c)
    assert r.status_code == 413 and _msg(r) == "The recording is over a minute."


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


async def test_the_scores_reach_the_browser(db_conn, engine, monkeypatch):
    async def scored(audio, mime, language):
        return Transcript("hold on", "en", 1.0, no_speech=0.03, avg_logprob=-0.4)
    monkeypatch.setattr(engine, "transcribe", scored)
    await _voice(db_conn)
    async with _client(db_conn) as c:
        out = (await _post(c)).json()
    assert (out["no_speech"], out["avg_logprob"]) == (0.03, -0.4)


async def test_status_carries_the_window_settings(db_conn, engine, monkeypatch):
    monkeypatch.setenv("SPEECH_WINDOW_SILENCE_MS", "450")
    await _voice(db_conn)
    async with _client(db_conn) as c:
        body = (await c.get(STATUS)).json()
    assert body["window"]["silence_ms"] == 450
    assert body["window"]["language"] == "en"


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


async def test_the_pre_transcribe_probe_is_for_the_local_engine_only(db_conn, engine, monkeypatch):
    await _voice(db_conn)
    engine.name = "openai"
    monkeypatch.setenv("SPEECH_ENGINE", "openai")   # what runs is what the deployment asks for
    async with _client(db_conn) as c:
        assert (await _post(c)).status_code == 200
    assert engine.status_calls == 0           # no GET /models per recording on a remote engine
    engine.name = "local"
    monkeypatch.delenv("SPEECH_ENGINE")
    async with _client(db_conn) as c:
        await _post(c)
    assert engine.status_calls == 1


async def test_status_says_who_hears_and_how_long_a_recording_may_be(db_conn, engine, monkeypatch):
    await _voice(db_conn, True)
    async with _client(db_conn) as c:
        body = (await c.get(STATUS)).json()
    assert (body["heard_by"], body["max_seconds"]) == ("this server", 120)
    engine.name, engine.where = "google", "Google Cloud"
    monkeypatch.setenv("SPEECH_ENGINE", "google")
    async with _client(db_conn) as c:
        body = (await c.get(STATUS)).json()
    assert (body["heard_by"], body["max_seconds"]) == ("Google Cloud", 55)   # Google takes a minute at most


async def test_google_offers_only_its_recognizers_languages_and_says_what_auto_hears(db_conn, engine, monkeypatch):
    await _voice(db_conn, True)
    async with _client(db_conn) as c:
        body = (await c.get(STATUS)).json()
    assert body["auto_hears"] is None and len(body["languages"]) == len(WHISPER_LANGUAGES)
    engine.name, engine.where = "google", "Google Cloud"
    engine.languages, engine.auto_hears = ("si", "ta", "en"), "si"
    monkeypatch.setenv("SPEECH_ENGINE", "google")
    async with _client(db_conn) as c:
        body = (await c.get(STATUS)).json()
    # Any other language Google would refuse: the person isn't offered it.
    assert body["languages"] == [{"code": "en", "name": "English"}, {"code": "si", "name": "Sinhala"},
                                 {"code": "ta", "name": "Tamil"}]
    assert body["auto_hears"] == "Sinhala"


async def test_a_google_recognizer_for_every_language_offers_them_all(db_conn, engine, monkeypatch):
    await _voice(db_conn, True)
    engine.name, engine.languages, engine.auto_hears = "google", (), None
    monkeypatch.setenv("SPEECH_ENGINE", "google")
    async with _client(db_conn) as c:
        body = (await c.get(STATUS)).json()
    assert len(body["languages"]) == len(WHISPER_LANGUAGES) and body["auto_hears"] is None


async def test_languages_learned_by_the_status_check_are_in_the_same_answer(db_conn, engine):
    await _voice(db_conn, True)
    engine.languages = ()

    async def learn():
        engine.languages = ("ta",)
        return EngineStatus(True, "ready", "")
    engine.status = learn
    async with _client(db_conn) as c:
        body = (await c.get(STATUS)).json()
    assert body["model_languages"] == ["Tamil"]


async def test_status_names_what_the_model_is_for(db_conn, engine, monkeypatch):
    await _voice(db_conn, True)
    engine.languages = ("si",)
    async with _client(db_conn) as c:
        body = (await c.get(STATUS)).json()
    assert body["model_languages"] == ["Sinhala"]


async def test_a_model_for_every_language_names_none(db_conn, engine):
    await _voice(db_conn, True)
    async with _client(db_conn) as c:
        assert (await c.get(STATUS)).json()["model_languages"] == []


# ---------- the language a person speaks picks its model (spec 2026-11-04-speech-choices-per-person) ----------

@pytest.fixture
def pooled(monkeypatch):
    """Local engines built on demand, every model on disk, nothing really loaded."""
    from server.speech import disk
    service.reset_for_tests()
    built = {}

    def build(spec, env):
        fake = FakeEngine()
        fake.spec, fake.model, fake.languages = spec, spec.id, spec.languages
        built[spec.id] = fake
        return fake
    monkeypatch.setattr(service, "_build_local", build)
    monkeypatch.setattr(service, "_release_memory", lambda: None)
    monkeypatch.setattr(disk, "on_disk", lambda model, env=None: True)
    monkeypatch.delenv("SPEECH_MODEL", raising=False)
    monkeypatch.delenv("SPEECH_ENGINE", raising=False)
    yield built
    service.reset_for_tests()


async def _offer(db_conn, ids):
    await db_conn.execute("""UPDATE app_settings SET value = value || jsonb_build_object('offered_speech_models', %s::jsonb)
                             WHERE key = 'chat_options'""", (json.dumps(ids),))


async def test_status_lists_the_languages_the_offered_models_hear(db_conn, pooled):
    await _voice(db_conn)
    await _offer(db_conn, ["whisper-small-si", "whisper-small-ta"])
    async with _client(db_conn) as c:
        body = (await c.get(STATUS)).json()
    assert [(h["code"], h["model"]) for h in body["hears"]] == [("si", "whisper-small-si"), ("ta", "whisper-small-ta")]
    assert body["default_model"] == {"id": "small", "label": "Whisper small"}
    assert body["engine"] == "local"   # the Speaking switch lists a Google recognizer's languages instead


async def test_status_loads_nothing_but_the_default(db_conn, pooled):
    await _voice(db_conn)
    await _offer(db_conn, ["whisper-small-si", "whisper-small-ta"])
    async with _client(db_conn) as c:
        await c.get(STATUS)
    assert list(pooled) == ["small"]


async def test_a_sinhala_clip_is_heard_by_the_sinhala_model_and_says_so(db_conn, pooled):
    await _voice(db_conn)
    await _offer(db_conn, ["whisper-small-si"])
    async with _client(db_conn) as c:
        r = await _post(c, language="si")
    assert r.status_code == 200 and r.json()["model"] == "whisper-small-si"
    assert pooled["whisper-small-si"].seen == [(3, "audio/webm", "si")]


async def test_a_clip_in_a_language_nobody_offers_is_heard_by_the_default(db_conn, pooled):
    await _voice(db_conn)
    await _offer(db_conn, ["whisper-small-si"])
    async with _client(db_conn) as c:
        r = await _post(c, language="de")
    assert r.json()["model"] == "small" and "whisper-small-si" not in pooled


async def test_a_model_withdrawn_mid_conversation_leaves_the_next_clip_to_the_default(db_conn, pooled):
    await _voice(db_conn)
    await _offer(db_conn, ["whisper-small-si"])
    async with _client(db_conn) as c:
        assert (await _post(c, language="si")).json()["model"] == "whisper-small-si"
        await _offer(db_conn, [])                       # the admin untick it between two clips
        r = await _post(c, language="si")
    assert r.status_code == 200 and r.json()["model"] == "small"


async def test_a_model_that_fails_to_load_hands_the_clip_to_the_default(db_conn, pooled, monkeypatch):
    await _voice(db_conn)
    await _offer(db_conn, ["whisper-small-si"])
    real = service._build_local

    def build(spec, env):
        fake = real(spec, env)
        if spec.id == "whisper-small-si":
            fake.raises = EngineUnavailable("ValueError")
        return fake
    monkeypatch.setattr(service, "_build_local", build)
    async with _client(db_conn) as c:
        r = await _post(c, language="si")
    assert r.status_code == 200 and r.json()["model"] == "small"
    assert pooled["small"].seen[0][2] == "si"              # heard in the language asked for


async def test_a_remote_engine_hears_every_clip_and_ignores_the_offer(db_conn, engine, monkeypatch):
    await _voice(db_conn)
    await _offer(db_conn, ["whisper-small-si"])
    engine.name = "openai"
    monkeypatch.setenv("SPEECH_ENGINE", "openai")
    async with _client(db_conn) as c:
        assert (await c.get(STATUS)).json()["hears"] == []
        r = await _post(c, language="si")
    assert r.status_code == 200 and engine.seen[0][2] == "si"


# ---------- Google recognizers, one per language (spec 2026-11-04-google-recognizers) ----------

G_SI = "projects/p-1/locations/asia-southeast1/recognizers/asr-si"
G_TA = "projects/p-1/locations/asia-southeast1/recognizers/asr-ta-en"
_RECOGNIZERS = {G_SI: ("chirp_2", ["si-LK"]), G_TA: ("chirp_3", ["ta-IN", "en-GB"])}


@pytest.fixture
def google(monkeypatch):
    """Two real GoogleSpeech engines over a fake Google: each recognizer answers its own model and languages, and
    every recognize call is recorded as (recognizer id, the language codes sent)."""
    from server.speech.google_engine import GoogleSpeech
    service.reset_for_tests()
    calls = {"get": [], "recognize": []}

    def handler(request):
        path = request.url.path
        if path.endswith(":recognize"):
            rid = path.rsplit("/", 1)[1].split(":")[0]
            sent = json.loads(request.content)["config"].get("languageCodes")
            calls["recognize"].append((rid, sent))
            return httpx.Response(200, json={"results": [{"alternatives": [{"transcript": f"heard by {rid}"}],
                                                          "languageCode": (sent or ["en-GB"])[0].lower()}]})
        name = path.split("/v2/", 1)[1]
        calls["get"].append(name.rsplit("/", 1)[1])
        model, codes = _RECOGNIZERS[name]
        return httpx.Response(200, json={"name": name, "model": model, "languageCodes": codes})

    async def token():
        return "t", "p-1"
    for name in _RECOGNIZERS:
        service._google[name] = GoogleSpeech(name, transport=httpx.MockTransport(handler), token=token)
    monkeypatch.delenv("SPEECH_ENGINE", raising=False)
    monkeypatch.delenv("GOOGLE_SPEECH_RECOGNIZER", raising=False)
    yield calls
    service.reset_for_tests()


async def _tick(db_conn, names):
    await db_conn.execute("""UPDATE app_settings SET value = value || jsonb_build_object(
                                 'speech_engine', 'google', 'google_recognizers', %s::jsonb)
                             WHERE key = 'chat_options'""", (json.dumps(names),))


def _wav():
    import io as _io

    import numpy as _np
    import soundfile as _sf
    buf = _io.BytesIO()
    _sf.write(buf, (0.1 * _np.sin(_np.arange(16000) / 3)).astype(_np.float32), 16000, format="WAV")
    return buf.getvalue()


async def test_google_status_lists_each_language_with_the_recognizer_that_hears_it(db_conn, google):
    await _voice(db_conn)
    await _tick(db_conn, [G_TA, G_SI])
    async with _client(db_conn) as c:
        body = (await c.get(STATUS)).json()
    assert (body["engine"], body["ready"]) == ("google", True)
    assert [(h["code"], h["model"], h["label"]) for h in body["hears"]] == [
        ("ta", "asr-ta-en", "asr-ta-en (chirp_3)"), ("en", "asr-ta-en", "asr-ta-en (chirp_3)"),
        ("si", "asr-si", "asr-si (chirp_2)")]
    assert body["default_model"] == {"id": "asr-ta-en", "label": "asr-ta-en (chirp_3)"}
    assert [lang["code"] for lang in body["languages"]] == ["en", "si", "ta"]   # every ticked one's, not the first's


async def test_a_sinhala_clip_goes_to_the_sinhala_recognizer_on_a_worker_that_never_answered_status(db_conn, google):
    await _voice(db_conn)
    await _tick(db_conn, [G_TA, G_SI])
    async with _client(db_conn) as c:
        r = await _post(c, body=_wav(), language="si", name="a.wav", ctype="audio/wav")
    assert r.status_code == 200, r.text
    assert google["recognize"] == [("asr-si", ["si-LK"])]
    assert r.json()["text"] == "heard by asr-si" and r.json()["model"] == "asr-si (chirp_2)"


async def test_english_goes_to_the_first_recognizer_that_lists_it_in_its_own_code(db_conn, google):
    await _voice(db_conn)
    await _tick(db_conn, [G_SI, G_TA])
    async with _client(db_conn) as c:
        r = await _post(c, body=_wav(), language="en", name="a.wav", ctype="audio/wav")
    assert r.status_code == 200, r.text
    assert google["recognize"] == [("asr-ta-en", ["en-GB"])]


async def test_auto_goes_to_the_first_recognizer(db_conn, google):
    await _voice(db_conn)
    await _tick(db_conn, [G_SI, G_TA])
    async with _client(db_conn) as c:
        r = await _post(c, body=_wav(), name="a.wav", ctype="audio/wav")
    assert r.status_code == 200, r.text
    assert google["recognize"] == [("asr-si", None)]       # no language sent: the recognizer's own apply


async def test_a_parser_error_the_framework_does_not_translate_is_still_400(db_conn, engine, monkeypatch):
    # Starlette 1.0 let python-multipart's own error through (a 500); later versions wrap it. Whichever version
    # is installed, a body the parser rejects is the client's mistake.
    from python_multipart.exceptions import MultipartParseError

    async def parse(self):
        async for _ in self.stream:
            pass
        raise MultipartParseError("Did not find boundary character")
    monkeypatch.setattr(speech_router.MemoryMultipartParser, "parse", parse)
    await _voice(db_conn)
    async with _client(db_conn) as c:
        r = await c.post(URL, content=b"garbage", headers=_multipart_headers())
    assert r.status_code == 400 and _msg(r) == "That recording couldn't be read."
