"""Task 6: the speech routes. A fake transcriber stands in for the engine, so no model is loaded and no remote
server is called."""
import logging

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.auth import scopes as token_scopes
from server.auth.users import resolve_or_provision_user
from server.routers import speech as speech_router
from server.speech import service
from server.speech.base import (AudioTooLong, BadAudio, EngineFailed, EngineNotReady, EngineStatus, Transcript)
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


def _app(db_conn, principal):
    app = FastAPI()

    async def _conn():
        yield db_conn

    app.dependency_overrides[deps.get_conn] = _conn
    app.dependency_overrides[deps.get_current_user] = lambda: principal
    app.include_router(speech_router.router)
    return app


def _client(db_conn, principal=None):
    return httpx.AsyncClient(transport=ASGITransport(app=_app(db_conn, principal or _principal())),
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


async def test_language_is_checked_before_the_upload_is_read(db_conn, engine, monkeypatch):
    """G4: capability, then voice off (404), then language (422), then the upload. A bad language never reaches
    the transcriber, and neither does a request while voice is off."""
    await _voice(db_conn, on=None)
    async with _client(db_conn) as c:
        assert (await _post(c, language="xx")).status_code == 404     # off wins over a bad language
    await _voice(db_conn)
    async with _client(db_conn) as c:
        assert (await _post(c, body=b"x" * (11 * 1024 * 1024), language="xx")).status_code == 422


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
