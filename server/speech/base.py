"""The speech seam: one small interface, so an engine that takes audio natively can sit behind it later
(spec §4.3, §6). Nothing here knows about HTTP routes or any particular engine."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

MAX_AUDIO_BYTES = 10 * 1024 * 1024  # 10 MB per recording
MAX_AUDIO_SECONDS = 125  # the browser stops at 120 s; the server allows a little slack


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str | None  # what the engine detected or was told; None when it didn't say
    duration_s: float
    # Whisper's own view of the most speech-like part: `no_speech` 0 to 1, `avg_logprob` 0 and down. None: no scores.
    no_speech: float | None = None
    avg_logprob: float | None = None


@dataclass(frozen=True)
class EngineStatus:
    ready: bool
    state: str  # "ready" | "loading" | "error"
    detail: str  # a short human reason; empty when ready


class SpeechError(Exception):
    """Base of every error an engine raises on purpose; messages are safe to show a person."""


class AudioTooLong(SpeechError):
    pass


class BadAudio(SpeechError):
    pass


class EngineNotReady(SpeechError):
    @property
    def detail(self) -> str:
        return str(self.args[0]) if self.args else ""


class EngineUnavailable(EngineNotReady):
    """The engine is not merely loading: a load failed, or it is set up wrong (no URL, no model, a package that
    isn't installed). Waiting won't fix it, so the person is told to ask their admin, not to try again."""


class EngineFailed(SpeechError):
    pass


class ConversionFailed(SpeechError):
    """A Transformers-format model couldn't be converted (speech/convert.py). Its message goes to the admin."""


class Transcriber(Protocol):
    name: str  # "local" | "openai"
    model: str
    languages: tuple[str, ...]  # what the model is made for; empty: every language
    where: str  # "this server", or the remote URL's host

    async def transcribe(self, audio: bytes, mime: str, language: str | None) -> Transcript: ...

    async def status(self) -> EngineStatus: ...

    def warm(self) -> None:
        """Start whatever the engine needs (load a model) without blocking; a no-op if nothing."""
