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

from server.speech import audio as audio_mod, catalogue, convert, hf_whisper, local_whisper, openai_engine
from server.speech.audio import decode_capped
from server.speech.catalogue import SpeechModel
from server.speech.base import (AudioTooLong, BadAudio, ConversionFailed, EngineFailed, EngineNotReady, EngineStatus, Transcript)
from server.speech.languages import WHISPER_LANGUAGES
from server.speech.local_whisper import LocalWhisper
from server.speech.openai_engine import OpenAICompatible

SECRET = "sk-test-SECRET-key-123"
AUDIO = b"RIFF-not-really-audio-bytes"


def _samples(seconds):
    return np.zeros(int(seconds * 16000), dtype=np.float32)


def _wav(seconds, rate=16000):
    """Real audio, encoded in memory with PyAV: a quiet sine as WAV."""
    import io

    import av
    n = int(seconds * rate)
    t = np.arange(n) / rate
    pcm = (0.1 * np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with av.open(buf, "w", format="wav") as out:
        st = out.add_stream("pcm_s16le", rate=rate)
        st.layout = "mono"
        for i in range(0, n, 1024):
            frame = av.AudioFrame.from_ndarray(pcm[i:i + 1024].reshape(1, -1), format="s16", layout="mono")
            frame.sample_rate = rate
            for p in st.encode(frame):
                out.mux(p)
        for p in st.encode(None):
            out.mux(p)
    return buf.getvalue()


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
    monkeypatch.setattr(local_whisper, "decode_capped", lambda a: _samples(3))
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
    def too_long(a):
        raise AudioTooLong("too long")
    monkeypatch.setattr(local_whisper, "decode_capped", too_long)
    with pytest.raises(AudioTooLong):
        await eng.transcribe(AUDIO, "audio/webm", None)
    assert fake.instances[0].calls == []


async def test_local_decodes_real_audio_with_the_bounded_helper(monkeypatch):
    """No decode patch: the engine's real path goes through decode_capped."""
    FakeModel.instances, FakeModel.gate, FakeModel.boom = [], None, None
    monkeypatch.setattr(local_whisper, "WhisperModel", FakeModel)
    eng = LocalWhisper("small", 1)
    await _ready(eng)
    assert (await eng.transcribe(_wav(2), "audio/wav", None)).text == "Hello"
    with pytest.raises(BadAudio):
        await eng.transcribe(b"garbage" * 50, "audio/webm", None)
    assert len(FakeModel.instances[0].calls) == 1


async def test_local_undecodable_audio_is_bad_audio(fake, monkeypatch):
    eng = LocalWhisper("small", 1)
    await _ready(eng)

    def boom(a):
        raise BadAudio("garbage")
    monkeypatch.setattr(local_whisper, "decode_capped", boom)
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


_REAL_DECODE = {"test_openai_over_the_cap_never_reaches_the_transport",
                "test_openai_garbage_audio_never_reaches_the_transport",
                "test_openai_real_audio_uses_its_length_when_the_server_gives_no_duration"}


@pytest.fixture(autouse=True)
def _fake_decode_for_openai(request, monkeypatch):
    """The openai tests send placeholder bytes, so they stub the decoder; the ones about decoding use the real one."""
    if request.node.name not in _REAL_DECODE:
        monkeypatch.setattr(openai_engine, "decode_capped", lambda a: _samples(2))


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
    monkeypatch.setattr(openai_engine, "decode_capped", lambda a: _samples(4))
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


# ---- decode_capped ----


def test_decode_capped_returns_16k_mono_float32():
    a = decode_capped(_wav(3, rate=44100))  # resampled from 44.1 kHz
    assert a.dtype == np.float32 and a.ndim == 1
    assert abs(len(a) - 3 * 16000) < 400
    assert 0.01 < float(np.abs(a).max()) <= 1.0


def test_decode_capped_over_the_cap_raises_audio_too_long():
    with pytest.raises(AudioTooLong):
        decode_capped(_wav(4), max_seconds=1)
    assert len(decode_capped(_wav(2), max_seconds=2.5)) > 0


def test_decode_capped_stops_decoding_early(monkeypatch):
    import av
    calls = []

    real = av.AudioResampler

    class Counting:  # AudioResampler can't be subclassed, so wrap it
        def __init__(self, **kw):
            self._r = real(**kw)

        def resample(self, frame):
            calls.append(frame)
            return self._r.resample(frame)

    monkeypatch.setattr(audio_mod.av, "AudioResampler", Counting)
    long = _wav(30)
    decode_capped(long, max_seconds=100)
    full = len(calls)
    calls.clear()
    with pytest.raises(AudioTooLong):
        decode_capped(long, max_seconds=1)
    assert 0 < len(calls) < full / 5


def test_decode_capped_garbage_and_no_audio_are_bad_audio():
    with pytest.raises(BadAudio):
        decode_capped(b"this is not audio" * 100)
    with pytest.raises(BadAudio):
        decode_capped(b"")


async def test_openai_over_the_cap_never_reaches_the_transport(monkeypatch):
    import functools
    monkeypatch.setattr(openai_engine, "decode_capped", functools.partial(decode_capped, max_seconds=1))
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={"text": "x"})

    with pytest.raises(AudioTooLong):
        await _engine(handler).transcribe(_wav(3), "audio/wav", None)
    assert calls == []


async def test_openai_garbage_audio_never_reaches_the_transport():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={"text": "x"})

    with pytest.raises(BadAudio):
        await _engine(handler).transcribe(b"garbage" * 50, "audio/webm", None)
    assert calls == []


async def test_openai_real_audio_uses_its_length_when_the_server_gives_no_duration():
    t = await _engine(lambda r: httpx.Response(200, json={"text": "hi"})).transcribe(_wav(2), "audio/wav", None)
    assert abs(t.duration_s - 2.0) < 0.05


async def test_openai_decodes_off_the_event_loop(monkeypatch):
    import threading
    where = []

    def spy(a):
        where.append(threading.current_thread())
        return _samples(1)
    monkeypatch.setattr(openai_engine, "decode_capped", spy)
    await _engine(lambda r: httpx.Response(200, json={"text": "x"})).transcribe(AUDIO, "audio/webm", None)
    assert where and where[0] is not threading.main_thread()


async def test_local_a_failed_load_is_retried_by_the_next_warm(fake):
    fake.boom = OSError("offline")
    eng = LocalWhisper("small", 1)
    await _ready(eng)
    assert (await eng.status()).state == "error"
    fake.boom = None
    fake.gate = threading.Event()  # hold the second load open so "loading" is observable
    eng.warm()
    assert (await eng.status()).state == "loading"
    fake.gate.set()
    await _ready(eng)
    assert await eng.status() == EngineStatus(True, "ready", "")
    eng.warm()  # ready: idempotent, no second model
    assert len(fake.instances) == 1


# ---- final fix wave, I5: "not working" (waiting won't help) is a different error from "still loading" ----

async def test_local_in_error_raises_unavailable_but_loading_raises_plain_not_ready(fake):
    from server.speech.base import EngineNotReady, EngineUnavailable
    eng = LocalWhisper("small", 1)
    with pytest.raises(EngineNotReady) as loading:
        await eng.transcribe(AUDIO, "audio/webm", None)
    assert not isinstance(loading.value, EngineUnavailable)
    eng._state, eng._detail = "error", "OSError"
    with pytest.raises(EngineUnavailable):
        await eng.transcribe(AUDIO, "audio/webm", None)


async def test_openai_config_mistake_raises_unavailable():
    from server.speech.base import EngineUnavailable
    with pytest.raises(EngineUnavailable):
        await OpenAICompatible(None, None, None).transcribe(b"a", "audio/webm", None)


# ---------- the chosen model's own rules (spec 2026-11-02-speech-model-choice) ----------

class _Recording:
    """A stand-in faster-whisper model that records what it was asked and can fail."""
    def __init__(self, error=None):
        self.language = "unset"
        self.error = error

    def transcribe(self, samples, language=None, **kw):
        self.language = language
        self.kw = kw
        if self.error:
            raise self.error
        return [SimpleNamespace(text=" hi")], SimpleNamespace(language=language or "en", duration=1.0)


def _running(spec, model, monkeypatch):
    monkeypatch.setattr(local_whisper, "decode_capped", lambda audio: np.zeros(16000, dtype=np.float32))
    eng = LocalWhisper(spec, 1)
    eng._model, eng._state = model, "ready"
    return eng


async def test_auto_tells_a_one_language_model_its_language(monkeypatch):
    model = _Recording()
    spec = SpeechModel("m", "M", "converted-whisper", "a/b", "r", ("si",))
    assert (await _running(spec, model, monkeypatch).transcribe(b"x", "audio/webm", None)).language == "si"


async def test_every_model_listens_the_way_the_recordings_said_to(monkeypatch):
    model = _Recording()
    await _running(SpeechModel("m", "M", "faster-whisper", "m"), model, monkeypatch).transcribe(b"x", "audio/webm", "en")
    assert model.kw["vad_filter"] is True and model.kw["vad_parameters"] == {"threshold": 0.4}
    assert model.kw["temperature"] == 0
    # Whisper's defaults, because the common speed-ups cost real words: see DECODE.
    assert "beam_size" not in model.kw and "condition_on_previous_text" not in model.kw


async def test_a_language_the_person_chose_stays_theirs(monkeypatch):
    model = _Recording()
    spec = SpeechModel("m", "M", "converted-whisper", "a/b", "r", ("si",))
    await _running(spec, model, monkeypatch).transcribe(b"x", "audio/webm", "en")
    assert model.language == "en"


async def test_auto_stays_auto_on_a_model_for_every_language(monkeypatch):
    model = _Recording()
    await _running(SpeechModel("m", "M", "faster-whisper", "m"), model, monkeypatch).transcribe(b"x", "audio/webm", None)
    assert model.language is None


async def test_a_model_that_cant_detect_a_language_says_what_to_do(monkeypatch):
    # faster-whisper's own behaviour on a fine-tune whose config lists no languages: all_language_probs[0].
    model = _Recording(IndexError("list index out of range"))
    eng = _running(SpeechModel("m", "M", "converted-whisper", "a/b"), model, monkeypatch)
    with pytest.raises(EngineFailed) as e:
        await eng.transcribe(b"x", "audio/webm", None)
    assert "Speech language" in str(e.value)


async def test_a_model_failing_on_a_recording_is_engine_failed_not_a_crash(monkeypatch):
    model = _Recording(RuntimeError("ctranslate2 said no"))
    eng = _running(SpeechModel("m", "M", "faster-whisper", "m"), model, monkeypatch)
    with pytest.raises(EngineFailed) as e:
        await eng.transcribe(b"x", "audio/webm", "en")
    assert "ctranslate2" not in str(e.value)


def _load(eng):
    eng._load()
    return eng._state, eng._detail


def test_the_load_passes_the_pinned_revision(monkeypatch):
    seen = {}

    def fake(path, **kw):
        seen.update(path=path, **kw)
        return "loaded"

    monkeypatch.setattr(local_whisper, "WhisperModel", fake)
    assert _load(LocalWhisper(SpeechModel("m", "M", "faster-whisper", "a/b", "abc"), 1))[0] == "ready"
    assert seen["path"] == "a/b" and seen["revision"] == "abc"


def test_a_converted_model_loads_from_its_converted_directory(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(catalogue, "_installed", lambda name: True)
    monkeypatch.setattr(convert, "ensure_converted", lambda spec, threads: tmp_path)
    monkeypatch.setattr(local_whisper, "WhisperModel", lambda path, **kw: seen.update(path=path, **kw) or "loaded")
    assert _load(LocalWhisper(SpeechModel("m", "M", "converted-whisper", "a/b", "abc"), 1))[0] == "ready"
    assert seen["path"] == str(tmp_path) and "revision" not in seen


def test_a_failed_conversion_shows_its_reason_to_the_admin(monkeypatch):
    def fail(spec, threads):
        raise ConversionFailed("Converting a/b failed: no such commit")
    monkeypatch.setattr(catalogue, "_installed", lambda name: True)
    monkeypatch.setattr(convert, "ensure_converted", fail)
    state, detail = _load(LocalWhisper(SpeechModel("m", "M", "converted-whisper", "a/b", "abc"), 1))
    assert state == "error" and "no such commit" in detail


def test_a_model_that_needs_torch_says_so_and_starts_nothing(monkeypatch):
    monkeypatch.setattr(catalogue, "_installed", lambda name: False)
    monkeypatch.setattr(convert, "ensure_converted", lambda *a: pytest.fail("must not convert"))
    state, detail = _load(LocalWhisper(SpeechModel("m", "M", "converted-whisper", "a/b", "abc"), 1))
    assert state == "error" and "transformers" in detail


class _Pipe:
    def __init__(self):
        self.calls = []

    def __call__(self, audio, generate_kwargs=None):
        self.calls.append((audio, generate_kwargs))
        return {"text": " hello "}


def test_the_unconverted_engine_passes_speech_and_language(monkeypatch):
    pipe = _Pipe()
    monkeypatch.setattr(hf_whisper, "speech_only", lambda samples: samples[:8000])
    eng = hf_whisper.TransformersWhisper(SpeechModel("m", "M", "transformers-whisper", "a/b", "r", ("si",)), 2)
    got = eng._run(np.zeros(16000, dtype=np.float32), "si", pipe)
    assert (got.text, got.language, got.duration_s) == ("hello", "si", 1.0)
    audio, kwargs = pipe.calls[0]
    assert audio["raw"].size == 8000 and audio["sampling_rate"] == 16000
    assert kwargs == {"task": "transcribe", "language": "si"}


def test_both_paths_share_one_vad_threshold(monkeypatch):
    seen = {}
    monkeypatch.setattr(hf_whisper, "get_speech_timestamps", lambda samples, options: seen.update(o=options) or [])
    hf_whisper.speech_only(np.zeros(160, dtype=np.float32))
    assert seen["o"].threshold == local_whisper.VAD_THRESHOLD


def test_vad_silence_never_reaches_the_unconverted_model():
    """The real Silero VAD on real silence: Whisper would invent text from it."""
    pipe = _Pipe()
    eng = hf_whisper.TransformersWhisper(SpeechModel("m", "M", "transformers-whisper", "a/b", "r", ("si",)), 2)
    got = eng._run(np.zeros(16000 * 3, dtype=np.float32), "si", pipe)
    assert got.text == "" and got.duration_s == 3.0 and pipe.calls == []
