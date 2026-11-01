"""One transcriber per process, and the rules around using it: one running transcription per person, and the
local engine one at a time per process with a bounded wait (spec §5.4)."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Mapping

from .base import (MAX_AUDIO_BYTES, MAX_AUDIO_SECONDS, AudioTooLong, Transcriber, Transcript)
from .local_whisper import LocalWhisper
from .openai_engine import OpenAICompatible

logger = logging.getLogger("server.speech")

LOCAL_WAIT_S = 60  # how long a second recording waits for the local engine before "busy"
DEFAULT_LOCAL_MODEL = "small"

__all__ = ["AlreadyRunning", "Busy", "LOCAL_WAIT_S", "MAX_AUDIO_BYTES", "MAX_AUDIO_SECONDS", "get_transcriber",
           "reset_for_tests", "transcribe_for"]


class AlreadyRunning(Exception):
    """This person already has a transcription in progress."""


class Busy(Exception):
    """The local engine's slot was not free within LOCAL_WAIT_S."""


_transcriber: Transcriber | None = None
_running: set[str] = set()
_slot: asyncio.Semaphore | None = None


def _cpu_threads(env: Mapping[str, str]) -> int:
    default = max(1, (os.cpu_count() or 2) // 2)
    raw = (env.get("SPEECH_CPU_THREADS") or "").strip()
    try:
        return max(1, int(raw)) if raw else default
    except ValueError:
        logger.warning("SPEECH_CPU_THREADS=%r is not a number; using %d", raw, default)
        return default


def get_transcriber(env: Mapping[str, str] = os.environ) -> Transcriber:
    """Build (once) from SPEECH_ENGINE. Never raises for a config mistake: a bad setting shows up in the
    engine's status() instead, so the status route and the admin Overview can't be crashed by a typo."""
    global _transcriber
    if _transcriber is not None:
        return _transcriber
    engine = (env.get("SPEECH_ENGINE") or "local").strip().lower()
    model = (env.get("SPEECH_MODEL") or "").strip()
    if engine == "openai":
        _transcriber = OpenAICompatible(env.get("SPEECH_URL"), env.get("SPEECH_API_KEY"), model)
    else:
        if engine != "local":
            logger.warning("SPEECH_ENGINE=%r is not 'local' or 'openai'; using local", engine)
        _transcriber = LocalWhisper(model or DEFAULT_LOCAL_MODEL, _cpu_threads(env))
    return _transcriber


def reset_for_tests() -> None:
    global _transcriber, _slot
    _transcriber = None
    _slot = None
    _running.clear()


async def transcribe_for(user_id: str, audio: bytes, mime: str, language: str | None) -> Transcript:
    global _slot
    if len(audio) > MAX_AUDIO_BYTES:
        raise AudioTooLong(f"A recording can be at most {MAX_AUDIO_BYTES // (1024 * 1024)} MB")
    engine = get_transcriber()
    if user_id in _running:
        raise AlreadyRunning()
    _running.add(user_id)
    try:
        started = time.monotonic()
        if engine.name == "local":
            if _slot is None:
                _slot = asyncio.Semaphore(1)
            slot = _slot
            try:
                await asyncio.wait_for(slot.acquire(), LOCAL_WAIT_S)
            except asyncio.TimeoutError:
                raise Busy() from None
            # A thread can't be stopped: if the caller cancels, the slot stays held until the work really ends.
            work = asyncio.ensure_future(engine.transcribe(audio, mime, language))
            work.add_done_callback(lambda f: (f.cancelled() or f.exception(), slot.release()))
            result = await asyncio.shield(work)
        else:
            result = await engine.transcribe(audio, mime, language)
    finally:
        _running.discard(user_id)
    logger.info("transcribed user=%s engine=%s seconds=%.1f language=%s elapsed_ms=%d", user_id, engine.name,
                result.duration_s, result.language, int((time.monotonic() - started) * 1000))
    return result
