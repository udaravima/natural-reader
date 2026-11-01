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


class EngineFailed(SpeechError):
    pass


class Transcriber(Protocol):
    name: str  # "local" | "openai"
    model: str
    where: str  # "this server", or the remote URL's host

    async def transcribe(self, audio: bytes, mime: str, language: str | None) -> Transcript: ...

    async def status(self) -> EngineStatus: ...

    def warm(self) -> None:
        """Start whatever the engine needs (load a model) without blocking; a no-op if nothing."""
