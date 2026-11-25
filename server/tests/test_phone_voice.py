"""A line's voice at 8 kHz and its pre-rendered clips (spec 5.1, 5.3). No request reaches Google or Kokoro."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import threading
from datetime import datetime, timedelta, timezone

import httpx
import numpy as np
import pytest
import soundfile as sf

from server import chat_voice, endpoints
from server.phone import clips as clips_mod
from server.phone import store, voice
from server.phone.clips import DEFAULT_CLIP_TEXTS, ClipCache, render_clips
from server.phone.store import Clip, Line, LineVoice
from server.schemas import BatchTTSRequest, TTSRequest

CREDS = {"GOOGLE_APPLICATION_CREDENTIALS": "/run/secrets/google.json"}


def _wav(seconds=0.5, rate=8000) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, np.full(int(seconds * rate), 0.25, dtype=np.float32), rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


async def _token():
    return "tok", None


def _line(v=LineVoice("kokoro", "af_heart", None), greeting="Hello, welcome.", line_id="line-1") -> Line:
    return Line(id=line_id, name="Front desk", enabled=True, profile_id=None, project_id="p", model_id="m",
                voice=v, language="en", greeting=greeting, max_call_s=600, max_calls=2, service_user_id="u",
                daily_tokens=None)


def test_voice_key_has_an_empty_accent_when_there_is_none():
    assert voice.voice_key(LineVoice("kokoro", "af_heart", None)) == "kokoro:af_heart:"
    assert voice.voice_key(LineVoice("chirp", "Kore", "en-IN")) == "chirp:Kore:en-IN"


async def test_google_asks_for_8000_hz(monkeypatch):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"audioContent": base64.b64encode(_wav()).decode()})
    wav, _ = await chat_voice.chirp_speak("Hello there.", "Kore", 1.0, CREDS, sample_rate_hz=8000,
                                          transport=httpx.MockTransport(handler), token=_token)
    assert seen[0]["audioConfig"] == {"audioEncoding": "LINEAR16", "sampleRateHertz": 8000}
    wav, _ = await chat_voice.google_speak("Hello there.", "Kore", 1.0, {"GOOGLE_TTS_MODEL": "m"},
                                           sample_rate_hz=8000, transport=httpx.MockTransport(handler), token=_token)
    assert seen[1]["audioConfig"]["sampleRateHertz"] == 8000
    assert sf.info(io.BytesIO(wav)).samplerate == 8000


@pytest.mark.parametrize("engine,name", [("chirp", "chirp_speak"), ("google", "google_speak")])
async def test_render_google_engines_ask_for_8k_at_speed_one(monkeypatch, engine, name):
    seen = {}

    async def fake(text, voice_name, speed, *args, **kw):
        seen.update(text=text, voice=voice_name, speed=speed, **kw)
        return _wav(0.5), 0.5
    monkeypatch.setattr(chat_voice, name, fake)
    pcm = await voice.render("Hi.", LineVoice(engine, "Kore", "en-IN"))
    assert seen["sample_rate_hz"] == 8000 and seen["speed"] == 1.0 and seen["accent"] == "en-IN"
    assert len(pcm) == 8000   # 0.5 s of s16le at 8 kHz
    assert np.frombuffer(pcm, "<i2")[0] == int(0.25 * 32767)


async def test_render_kokoro_resamples_to_8k(monkeypatch):
    seen = {}

    async def fake(text, voice_name, speed):
        seen.update(voice=voice_name, speed=speed)
        return np.zeros(24000, dtype=np.float32), 24000
    monkeypatch.setattr(endpoints, "kokoro_pcm", fake)
    pcm = await voice.render("Hi.", LineVoice("kokoro", "af_heart", None))
    assert len(pcm) == 16000 and seen == {"voice": "af_heart", "speed": 1.0}


async def test_render_unknown_engine_raises():
    with pytest.raises(ValueError):
        await voice.render("Hi.", LineVoice("nope", "x", None))


async def test_a_voice_failure_surfaces_as_the_engines_error(monkeypatch):
    async def boom(*a, **k):
        raise chat_voice.GoogleVoiceError(503, "Ask the admin")
    monkeypatch.setattr(chat_voice, "chirp_speak", boom)
    with pytest.raises(chat_voice.GoogleVoiceError):
        await voice.render("Hi.", LineVoice("chirp", "Kore", None))


class _BlockingSpeak:
    """A `_speak` that holds its executor thread until `release` is set. It counts how many run at once, and
    whether the Kokoro lock was held each time one started."""

    def __init__(self):
        self.release = threading.Event()
        self._mu = threading.Lock()
        self.inside = self.peak = self.entries = self.unlocked = 0

    def __call__(self, text, voice_name, speed):
        with self._mu:
            self.inside += 1
            self.entries += 1
            self.peak = max(self.peak, self.inside)
            self.unlocked += not endpoints.inference_lock.locked()
        try:
            self.release.wait(5)
        finally:
            with self._mu:
                self.inside -= 1
        return np.ones(3, dtype=np.float32), 24000


class _StayingClient:
    """A Request whose client never leaves."""

    async def is_disconnected(self):
        return False


_A_PRINCIPAL = object()
_KOKORO_ROUTES = {
    "kokoro_pcm": lambda: endpoints.kokoro_pcm("A.", "af_heart", 1.0),
    "synthesize": lambda: endpoints.synthesize(TTSRequest(text="A."), _StayingClient(), _A_PRINCIPAL),
    "batch_synthesize": lambda: endpoints.batch_synthesize(
        BatchTTSRequest(sentences=["A.", "B."]), _StayingClient(), _A_PRINCIPAL),
}


async def _until(check, timeout=2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not check():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.01)


@pytest.mark.parametrize("route", list(_KOKORO_ROUTES))
async def test_a_cancelled_render_keeps_the_kokoro_lock_until_its_thread_ends(monkeypatch, route):
    """A barge-in cancels the turn's voice task, but the thread already in Kokoro can't be stopped. The lock must
    stay held until that thread ends, or the next render runs Kokoro beside it (Kokoro isn't thread-safe)."""
    monkeypatch.setattr(endpoints, "inference_lock", asyncio.Lock())
    speak = _BlockingSpeak()
    monkeypatch.setattr(endpoints, "_speak", speak)
    first = asyncio.create_task(_KOKORO_ROUTES[route]())
    try:
        await _until(lambda: speak.entries == 1)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(endpoints.kokoro_pcm("B.", "af_heart", 1.0))
        await asyncio.sleep(0.2)
        assert speak.entries == 1, "a second render entered Kokoro while the cancelled one's thread still ran"
        assert endpoints.inference_lock.locked()
    finally:
        speak.release.set()
    audio, rate = await asyncio.wait_for(second, 5)
    assert rate == 24000 and len(audio) == 3
    assert speak.entries == 2 and speak.peak == 1 and speak.unlocked == 0
    assert not endpoints.inference_lock.locked()


async def test_a_render_cancelled_while_waiting_for_kokoro_holds_nothing(monkeypatch):
    monkeypatch.setattr(endpoints, "inference_lock", asyncio.Lock())
    speak = _BlockingSpeak()
    monkeypatch.setattr(endpoints, "_speak", speak)
    first = asyncio.create_task(endpoints.kokoro_pcm("A.", "af_heart", 1.0))
    try:
        await _until(lambda: speak.entries == 1)
        waiting = asyncio.create_task(endpoints.kokoro_pcm("B.", "af_heart", 1.0))
        await asyncio.sleep(0.05)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
    finally:
        speak.release.set()
    await asyncio.wait_for(first, 5)
    assert speak.entries == 1 and not endpoints.inference_lock.locked()
    await asyncio.wait_for(endpoints.kokoro_pcm("C.", "af_heart", 1.0), 5)
    assert speak.entries == 2 and not endpoints.inference_lock.locked()


def test_default_texts_are_the_rulings():
    assert DEFAULT_CLIP_TEXTS["busy"] == ["Sorry, this line is busy. Please call again later."]
    assert DEFAULT_CLIP_TEXTS["still"] == ["Are you still there?"]
    assert DEFAULT_CLIP_TEXTS["filler"] == ["Let me check.", "One moment."]
    assert set(DEFAULT_CLIP_TEXTS) == {"busy", "closed", "goodbye", "still", "sorry", "filler"}


async def test_render_clips_has_every_kind(monkeypatch):
    async def fake(text, v):
        return text.encode()
    monkeypatch.setattr(voice, "render", fake)
    clips = await render_clips(_line())
    by = {(c.kind, c.ordinal): c for c in clips}
    assert by[("greeting", 0)].text == "Hello, welcome."
    assert {k for k, _ in by} == {"greeting", *DEFAULT_CLIP_TEXTS}
    assert sorted(o for k, o in by if k == "filler") == [0, 1]
    assert {c.voice_key for c in clips} == {"kokoro:af_heart:"}
    assert len({c.rendered_at for c in clips}) == 1


class _Store:
    """A stand-in for the two store reads the cache makes."""
    def __init__(self, clips):
        self.clips, self.loads = clips, 0

    async def stamp(self, conn, line_id):
        return max((c.rendered_at for c in self.clips), default=None)

    async def load(self, conn, line_id):
        self.loads += 1
        return self.clips


def _clip(kind, ordinal, pcm, key="kokoro:af_heart:", at=None):
    return Clip(kind, ordinal, "t", key, pcm, at or datetime(2026, 1, 1, tzinfo=timezone.utc))


def _patched(monkeypatch, fake):
    monkeypatch.setattr(store, "clips_stamp", fake.stamp)
    monkeypatch.setattr(store, "load_clips", fake.load)


async def test_cache_maps_kind_to_pcm_in_ordinal_order(monkeypatch):
    fake = _Store([_clip("filler", 1, b"b"), _clip("filler", 0, b"a"), _clip("busy", 0, b"x")])
    _patched(monkeypatch, fake)
    got = await ClipCache().get(None, _line())
    assert got == {"busy": [b"x"], "filler": [b"a", b"b"]}


async def test_cache_reloads_only_when_the_stamp_or_voice_changes(monkeypatch):
    fake = _Store([_clip("busy", 0, b"x")])
    _patched(monkeypatch, fake)
    cache = ClipCache()
    await cache.get(None, _line())
    await cache.get(None, _line())
    assert fake.loads == 1
    fake.clips = [_clip("busy", 0, b"y", at=datetime(2026, 2, 1, tzinfo=timezone.utc))]
    assert (await cache.get(None, _line()))["busy"] == [b"y"] and fake.loads == 2


async def test_cache_reloads_when_the_voice_changes(monkeypatch):
    fake = _Store([_clip("busy", 0, b"x")])
    _patched(monkeypatch, fake)
    cache = ClipCache()
    await cache.get(None, _line())
    await cache.get(None, _line(LineVoice("chirp", "Kore", None)))
    assert fake.loads == 2


async def test_stale_voice_clips_are_still_used_with_a_warning(monkeypatch, caplog):
    fake = _Store([_clip("busy", 0, b"x", key="chirp:Kore:")])
    _patched(monkeypatch, fake)
    with caplog.at_level(logging.WARNING):
        got = await ClipCache().get(None, _line())
    assert got == {"busy": [b"x"]} and "another voice" in caplog.text


async def test_peek_needs_no_database(monkeypatch):
    fake = _Store([_clip("busy", 0, b"x")])
    _patched(monkeypatch, fake)
    cache = ClipCache()
    assert cache.peek("line-1") is None
    await cache.get(None, _line())
    assert cache.peek("line-1") == {"busy": [b"x"]}
    assert cache.peek("other") is None
