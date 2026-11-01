"""Task 5: the speech engines. No model is downloaded and no real server is called."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from types import SimpleNamespace

import httpx
import numpy as np
import pytest

from server.speech import local_whisper, openai_engine
from server.speech.base import (AudioTooLong, BadAudio, EngineFailed, EngineNotReady, EngineStatus, Transcript)
from server.speech.languages import WHISPER_LANGUAGES
from server.speech.local_whisper import LocalWhisper
from server.speech.openai_engine import OpenAICompatible

SECRET = "sk-test-SECRET-key-123"
AUDIO = b"RIFF-not-really-audio-bytes"


def _samples(seconds):
    return np.zeros(int(seconds * 16000), dtype=np.float32)


class FakeModel:
    instances: list = []
    gate: threading.Event | None = None
    boom: Exception | None = None

    def __init__(self, name, **kw):
        if FakeModel.gate is not None:
            FakeModel.gate.wait(5)
        if FakeModel.boom is not None:
            raise FakeModel.boom
        self.name, self.kw, self.calls = name, kw, []
        FakeModel.instances.append(self)

    def transcribe(self, audio, **kw):
        self.calls.append(kw)
        return [SimpleNamespace(text=" Hel"), SimpleNamespace(text="lo ")], SimpleNamespace(language="en", duration=1.5)


@pytest.fixture
def fake(monkeypatch):
    FakeModel.instances, FakeModel.gate, FakeModel.boom = [], None, None
    monkeypatch.setattr(local_whisper, "WhisperModel", FakeModel)
    monkeypatch.setattr(local_whisper, "decode_audio", lambda f, sampling_rate=16000: _samples(3))
    return FakeModel


async def _ready(eng):
    eng.warm()
    for _ in range(200):
        if (await eng.status()).state != "loading":
            return
        await asyncio.sleep(0.01)
    raise AssertionError("never finished loading")


async def test_local_returns_joined_text_language_duration(fake):
    eng = LocalWhisper("small", cpu_threads=3)
    await _ready(eng)
    assert await eng.transcribe(AUDIO, "audio/webm;codecs=opus", None) == Transcript("Hello", "en", 1.5)
    m = fake.instances[0]
    assert m.name == "small" and m.kw == {"device": "cpu", "compute_type": "int8", "cpu_threads": 3}
    assert m.calls[0]["vad_filter"] is True


async def test_local_refuses_over_125_seconds(fake, monkeypatch):
    eng = LocalWhisper("small", 1)
    await _ready(eng)
    monkeypatch.setattr(local_whisper, "decode_audio", lambda f, sampling_rate=16000: _samples(126))
    with pytest.raises(AudioTooLong):
        await eng.transcribe(AUDIO, "audio/webm", None)
    assert fake.instances[0].calls == []
    monkeypatch.setattr(local_whisper, "decode_audio", lambda f, sampling_rate=16000: _samples(125))
    assert (await eng.transcribe(AUDIO, "audio/webm", None)).text == "Hello"


async def test_local_undecodable_audio_is_bad_audio(fake, monkeypatch):
    eng = LocalWhisper("small", 1)
    await _ready(eng)

    def boom(f, sampling_rate=16000):
        raise ValueError("garbage")
    monkeypatch.setattr(local_whisper, "decode_audio", boom)
    with pytest.raises(BadAudio):
        await eng.transcribe(AUDIO, "audio/webm", None)


async def test_local_status_loading_then_ready(fake):
    fake.gate = threading.Event()
    eng = LocalWhisper("small", 1)
    assert (await eng.status()).ready is False
    eng.warm()
    eng.warm()  # a second call starts nothing
    assert (await eng.status()) == EngineStatus(False, "loading", "Loading the model")
    with pytest.raises(EngineNotReady):
        await eng.transcribe(AUDIO, "audio/webm", None)
    fake.gate.set()
    await _ready(eng)
    assert await eng.status() == EngineStatus(True, "ready", "")
    assert len(fake.instances) == 1


async def test_local_load_failure_is_an_error_status_with_the_type_name(fake):
    fake.boom = OSError("secret path /x/y")
    eng = LocalWhisper("small", 1)
    await _ready(eng)
    st = await eng.status()
    assert (st.ready, st.state, st.detail) == (False, "error", "OSError")
    with pytest.raises(EngineNotReady) as e:
        await eng.transcribe(AUDIO, "audio/webm", None)
    assert e.value.detail == "OSError"


async def test_local_passes_language_or_none(fake):
    eng = LocalWhisper("small", 1)
    await _ready(eng)
    await eng.transcribe(AUDIO, "audio/webm", "si")
    await eng.transcribe(AUDIO, "audio/webm", None)
    assert [c["language"] for c in fake.instances[0].calls] == ["si", None]


def test_local_attributes():
    eng = LocalWhisper("small", 2)
    assert (eng.name, eng.model, eng.where) == ("local", "small", "this server")


def test_whisper_languages_match_faster_whisper():
    from faster_whisper.tokenizer import _LANGUAGE_CODES
    assert set(WHISPER_LANGUAGES) == set(_LANGUAGE_CODES)
    assert WHISPER_LANGUAGES["si"] == "Sinhala" and WHISPER_LANGUAGES["en"] == "English"


# ---- openai ----


def _engine(handler, url="https://stt.example.com/v1", model="whisper-1", key=SECRET):
    return OpenAICompatible(url, key, model, transport=httpx.MockTransport(handler))


async def test_openai_engine_posts_multipart():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"text": " Hi there ", "language": "english", "duration": 2.5})

    eng = _engine(handler, url="https://stt.example.com/v1/")  # trailing slash
    t = await eng.transcribe(AUDIO, "audio/webm;codecs=opus", "en")
    assert t == Transcript("Hi there", "en", 2.5)
    req = seen[0]
    assert req.method == "POST" and str(req.url) == "https://stt.example.com/v1/audio/transcriptions"
    assert req.headers["authorization"] == f"Bearer {SECRET}"
    assert req.headers["content-type"].startswith("multipart/form-data")
    body = req.content
    for needle in (b'name="model"', b"whisper-1", b'name="language"', b"verbose_json", b'name="file"',
                   b'filename="audio.webm"', b"Content-Type: audio/webm\r\n", AUDIO):
        assert needle in body, needle


async def test_openai_omits_language_when_auto():
    seen = []

    def handler(req):
        seen.append(req.content)
        return httpx.Response(200, json={"text": "x", "language": "si", "duration": 1})

    t = await _engine(handler).transcribe(AUDIO, "audio/webm", None)
    assert b'name="language"' not in seen[0] and t.language == "si"


async def test_openai_without_a_key_sends_no_authorization():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"text": "x"})

    await _engine(handler, key="").transcribe(AUDIO, "audio/webm", None)
    assert "authorization" not in seen[0].headers


async def test_openai_retries_once_with_json_when_verbose_json_is_refused(monkeypatch):
    monkeypatch.setattr(local_whisper, "decode_audio", lambda f, sampling_rate=16000: _samples(4))
    formats = []

    def handler(req):
        formats.append(b"verbose_json" in req.content)
        if len(formats) == 1:
            return httpx.Response(400, json={"error": {"message": "unsupported response_format verbose_json"}})
        return httpx.Response(200, json={"text": "plain"})

    t = await _engine(handler, ).transcribe(AUDIO, "audio/webm", "en")
    assert t == Transcript("plain", None, 4.0)
    assert formats == [True, False]


async def test_openai_a_400_about_something_else_is_not_retried():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(400, json={"error": "bad audio"})

    with pytest.raises(EngineFailed):
        await _engine(handler).transcribe(AUDIO, "audio/webm", None)
    assert len(calls) == 1


async def test_openai_401_and_connect_errors_and_timeout_are_failures():
    with pytest.raises(EngineFailed) as e:
        await _engine(lambda r: httpx.Response(401)).transcribe(AUDIO, "audio/webm", None)
    assert str(e.value) == "The transcription server refused this server's key"

    def refuse(req):
        raise httpx.ConnectError("no route", request=req)
    with pytest.raises(EngineFailed) as e:
        await _engine(refuse).transcribe(AUDIO, "audio/webm", None)
    assert str(e.value) == "The transcription server didn't answer"

    def hang(req):
        raise httpx.ReadTimeout("slow", request=req)
    with pytest.raises(EngineFailed) as e:
        await _engine(hang).transcribe(AUDIO, "audio/webm", None)
    assert str(e.value) == "The transcription server didn't answer"


def test_openai_timeout_is_finite():
    for t in (openai_engine.TIMEOUT, openai_engine.PROBE_TIMEOUT):
        assert None not in (t.connect, t.read, t.write, t.pool)


async def test_openai_unreadable_answer_is_a_failure():
    with pytest.raises(EngineFailed):
        await _engine(lambda r: httpx.Response(200, text="<html>")).transcribe(AUDIO, "audio/webm", None)


async def test_openai_key_never_in_logs_errors_or_repr(caplog):
    caplog.set_level(logging.DEBUG)

    def refuse(req):
        raise httpx.ConnectError("no route", request=req)
    eng = _engine(refuse)
    for h in (refuse, lambda r: httpx.Response(401), lambda r: httpx.Response(500, text=SECRET)):
        e = _engine(h)
        with pytest.raises(EngineFailed) as ex:
            await e.transcribe(AUDIO, "audio/webm", None)
        assert SECRET not in str(ex.value)
        await e.status()
    assert SECRET not in repr(eng) and SECRET not in str(vars(eng).get("where"))
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert SECRET not in text and AUDIO.decode() not in text


async def test_openai_status_probes_models():
    seen = []

    def ok(req):
        seen.append(req)
        return httpx.Response(200, json={"data": []})
    st = await _engine(ok, url="https://stt.example.com/v1/").status()
    assert st == EngineStatus(True, "ready", "")
    assert str(seen[0].url) == "https://stt.example.com/v1/models" and seen[0].method == "GET"

    st = await _engine(lambda r: httpx.Response(401)).status()
    assert (st.ready, st.state) == (False, "error") and "key" in st.detail

    def refuse(req):
        raise httpx.ConnectError("x", request=req)
    st = await _engine(refuse).status()
    assert (st.ready, st.state, st.detail) == (False, "error", "The transcription server didn't answer")


async def test_openai_config_mistakes_are_reported_not_raised():
    for kw, word in (({"url": ""}, "SPEECH_URL"), ({"url": None}, "SPEECH_URL"), ({"model": ""}, "SPEECH_MODEL")):
        eng = OpenAICompatible(**{"url": "https://x.example.com/v1", "api_key": "k", "model": "m", **kw})
        st = await eng.status()
        assert (st.ready, st.state) == (False, "error") and word in st.detail
        with pytest.raises(EngineNotReady) as e:
            await eng.transcribe(AUDIO, "audio/webm", None)
        assert e.value.detail == st.detail


def test_openai_attributes():
    eng = OpenAICompatible("https://stt.example.com:8443/v1", "k", "whisper-1")
    assert (eng.name, eng.model, eng.where) == ("openai", "whisper-1", "stt.example.com")
