"""The benchmark's ears and voices (spec 2026-11-17-call-simulation), each over the app's own engine code, so a
change to an engine shows up in the numbers:

- ears: `text` (no hearing: the script's words go straight to the model), `whisper:<model id>` (the speech
  catalogue's local models, built the way server/speech/service.py builds them), `google:<recognizer>` (the app's
  GoogleSpeech, on the app's kept-open Google client);
- voices: `none` (no audio; a sentence is taken to last 0.4 s a word), `kokoro[:<voice>]` (the read-aloud model,
  as /v1/synthesize runs it), `google[:<model>[:<voice>]]` (the app's google_speak, on the same client).

Cloud stages carry a network split (nettrace.py). `seconds` is what the stage cost in this process; in Talk the
browser also uploads the clip and downloads each sentence, which Talk's own timings show."""
from __future__ import annotations

import asyncio
import io
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass

import httpx
import soundfile as sf

from .nettrace import RequestTrace, TracingTransport

WORD_SECONDS = 0.4   # a sentence's length when nothing speaks it: about 150 words a minute


class SetupError(Exception):
    """An engine can't run here; the message says why."""


@dataclass
class Heard:
    text: str
    seconds: float
    net: dict[str, float] | None = None
    new_connection: bool | None = None
    error: str | None = None


@dataclass
class Spoken:
    wav: bytes | None
    audio_s: float
    seconds: float
    net: dict[str, float] | None = None
    new_connection: bool | None = None
    error: str | None = None


def _net(sink: list[RequestTrace], mark: int, ended: float) -> tuple[dict[str, float] | None, bool | None]:
    """The split of the last request made since `mark` (the recognize or synthesize call itself)."""
    made = sink[mark:]
    if not made:
        return None, None
    last = made[-1]
    return last.split(ended - last.started_at), last.new_connection


# ---- ears ----

class TextEars:
    label, where = "text (no hearing)", "-"
    needs_audio = False

    async def ready(self) -> None:
        return None

    async def hear(self, wav: bytes | None, said: str, language: str | None) -> Heard:
        return Heard(said, 0.0)


class WhisperEars:
    needs_audio = True
    where = "this server"

    def __init__(self, model_id: str, env: Mapping[str, str]) -> None:
        from ..speech import catalogue, service
        spec = catalogue.all_models(env).get(model_id)
        if spec is None:
            raise SetupError(f"No speech model {model_id!r}; the catalogue has: "
                             f"{', '.join(sorted(catalogue.all_models(env)))}")
        self.label = f"whisper {model_id}"
        self.engine = service._build_local(spec, env)   # the service's own builder

    async def ready(self, timeout_s: float = 900) -> None:
        self.engine.warm()
        deadline = time.monotonic() + timeout_s
        while True:
            status = await self.engine.status()
            if status.ready:
                return
            if status.state == "error" or time.monotonic() > deadline:
                raise SetupError(f"{self.label} isn't ready: {status.detail}")
            await asyncio.sleep(1)

    async def hear(self, wav: bytes | None, said: str, language: str | None) -> Heard:
        started = time.perf_counter()
        try:
            got = await self.engine.transcribe(wav or b"", "audio/wav", language)
        except Exception as e:
            return Heard("", time.perf_counter() - started, error=f"{type(e).__name__}: {e}")
        return Heard(got.text.strip(), time.perf_counter() - started)


# Google's hearing and voice share the app's one kept-open client (google_cloud.http()). The benchmark installs a
# traced client of the same kind, once per event loop, so every Google request lands in GOOGLE_SINK.
GOOGLE_SINK: list[RequestTrace] = []


def _trace_google() -> None:
    from .. import google_cloud, keepalive
    client = google_cloud.http()
    if not isinstance(client._transport, TracingTransport):
        google_cloud.use_http(keepalive.new_client(transport=TracingTransport(GOOGLE_SINK, limits=keepalive.LIMITS),
                                                   timeout=httpx.Timeout(30.0, connect=10.0)))


class GoogleEars:
    needs_audio = True
    where = "Google Cloud"

    def __init__(self, recognizer: str, env: Mapping[str, str]) -> None:
        from ..speech.google_engine import GoogleSpeech, timeout_from
        self.engine = GoogleSpeech(recognizer, timeout_s=timeout_from(env))
        self.label = f"google {self.engine.id or recognizer}"

    async def ready(self) -> None:
        _trace_google()
        status = await self.engine.status()
        if not status.ready:
            raise SetupError(f"{self.label} isn't usable: {status.detail}")
        self.label = f"google {self.engine.label}"

    async def hear(self, wav: bytes | None, said: str, language: str | None) -> Heard:
        _trace_google()
        mark, started = len(GOOGLE_SINK), time.perf_counter()
        try:
            got = await self.engine.transcribe(wav or b"", "audio/wav", language)
            error, text = None, got.text.strip()
        except Exception as e:
            error, text = f"{type(e).__name__}: {e}", ""
        ended = time.perf_counter()
        net, new = _net(GOOGLE_SINK, mark, ended)
        return Heard(text, ended - started, net, new, error)


def make_ears(spec: str, env: Mapping[str, str] = os.environ):
    kind, _, rest = spec.partition(":")
    if kind == "text":
        return TextEars()
    if kind == "whisper" and rest:
        return WhisperEars(rest, env)
    if kind == "google" and rest:
        return GoogleEars(rest, env)
    raise SetupError(f"--hear {spec!r}: use text, whisper:<model id> or google:<recognizer name>")


# ---- voices ----

def _wav(audio, rate: int) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, audio, rate, format="WAV")
    return buf.getvalue()


class NoVoice:
    label, where, parallel = "none (0.4 s a word)", "-", 1

    async def speak(self, text: str) -> Spoken:
        return Spoken(None, len(text.split()) * WORD_SECONDS, 0.0)


class KokoroVoice:
    where, parallel = "this server", 1   # one at a time: the server's inference lock

    def __init__(self, voice: str = "af_heart", speed: float = 1.0) -> None:
        self.voice, self.speed = voice, speed
        self.label = f"kokoro {voice}"
        self._model = None

    def _load(self):
        if self._model is None:
            from ..model import kokoro
            from ..tts_language import phonemes
            self._model = (kokoro, phonemes)
        return self._model

    async def speak(self, text: str) -> Spoken:
        kokoro, phonemes = await asyncio.to_thread(self._load)
        started = time.perf_counter()
        try:
            audio, rate = await asyncio.to_thread(
                lambda: kokoro.create(phonemes(kokoro.tokenizer, text, self.voice), voice=self.voice,
                                      speed=self.speed, is_phonemes=True))
        except Exception as e:
            return Spoken(None, len(text.split()) * WORD_SECONDS, time.perf_counter() - started,
                          error=f"{type(e).__name__}: {e}")
        seconds = time.perf_counter() - started
        return Spoken(_wav(audio, rate), len(audio) / rate, seconds)


class GoogleVoice:
    where, parallel = "Google Cloud", 2   # Talk asks for two sentences ahead

    def __init__(self, model: str, voice: str, env: Mapping[str, str]) -> None:
        self.model, self.voice = model, voice
        self.env = {**env, "GOOGLE_TTS_MODEL": model}
        self.label = f"google {model} {voice}"

    async def speak(self, text: str) -> Spoken:
        from .. import chat_voice
        _trace_google()
        mark, started = len(GOOGLE_SINK), time.perf_counter()
        try:
            wav, audio_s = await chat_voice.google_speak(text, self.voice, 1.0, self.env)
            error = None
        except chat_voice.GoogleVoiceError as e:
            wav, audio_s, error = None, len(text.split()) * WORD_SECONDS, e.detail
        ended = time.perf_counter()
        net, new = _net(GOOGLE_SINK, mark, ended)
        return Spoken(wav, audio_s, ended - started, net, new, error)


def make_voice(spec: str, env: Mapping[str, str] = os.environ):
    kind, _, rest = spec.partition(":")
    if kind == "none":
        return NoVoice()
    if kind == "kokoro":
        return KokoroVoice(rest or "af_heart")
    if kind == "google":
        model, _, voice = rest.partition(":")
        from ..chat_voice import default_voice
        return GoogleVoice(model or (env.get("GOOGLE_TTS_MODEL") or "gemini-2.5-flash-tts").strip(),
                           voice or default_voice(env), env)
    raise SetupError(f"--voice {spec!r}: use none, kokoro[:<voice>] or google[:<model>[:<voice>]]")
