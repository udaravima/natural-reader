"""One transcriber per process, and the rules around using it: one running transcription per person, and the
local engine one at a time per process with a bounded wait (spec §5.4)."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Mapping

from .base import (MAX_AUDIO_BYTES, MAX_AUDIO_SECONDS, EngineStatus, EngineUnavailable, Transcriber,
                   Transcript)
# The engines import faster-whisper, PyAV and numpy. They are imported inside get_transcriber, so a deployment
# that pulled new code without `pip install -r requirements.txt` still starts, and says so in the voice status.

logger = logging.getLogger("server.speech")

LOCAL_WAIT_S = 60  # how long a second recording waits for the local engine before "busy"
DEFAULT_LOCAL_MODEL = "small"
RETRY_AFTER_S = 300  # a worker whose engine failed to load tries again by itself at most this often (seconds)

__all__ = ["AlreadyRunning", "Busy", "LOCAL_WAIT_S", "MAX_AUDIO_BYTES", "MAX_AUDIO_SECONDS", "RETRY_AFTER_S",
           "get_transcriber", "heal", "reset_for_tests", "transcribe_for"]


class AlreadyRunning(Exception):
    """This person already has a transcription in progress."""


class Busy(Exception):
    """The local engine's slot was not free within LOCAL_WAIT_S."""


_transcriber: Transcriber | None = None
_running: set[str] = set()
_slot: asyncio.Semaphore | None = None


def _workers(env: Mapping[str, str]) -> int:
    """WORKERS the way run.py reads it, tolerating a bad value (one worker)."""
    try:
        return max(1, int(env.get("WORKERS", "1")))
    except ValueError:
        return 1


def _cpu_threads(env: Mapping[str, str]) -> int:
    # Every worker process has its own engine: half the cores, shared out between them, so all the workers
    # together leave the other half to everything else (spec 4.3). SPEECH_CPU_THREADS, when set, wins as is.
    default = max(1, (os.cpu_count() or 2) // (2 * _workers(env)))
    raw = (env.get("SPEECH_CPU_THREADS") or "").strip()
    try:
        return max(1, int(raw)) if raw else default
    except ValueError:
        logger.warning("SPEECH_CPU_THREADS=%r is not a number; using %d", raw, default)
        return default


class _MissingPackage:
    """Stands in for an engine whose Python package isn't installed (a `git pull` without `pip install -r
    requirements.txt`): the app runs, voice input says why it can't work, and a recording is refused."""
    where = "this server"

    def __init__(self, name: str, model: str, package: str) -> None:
        self.name, self.model = name, model
        self._detail = f"The Python package {package} is not installed: run pip install -r requirements.txt"

    async def status(self) -> EngineStatus:
        return EngineStatus(False, "error", self._detail)

    async def transcribe(self, audio: bytes, mime: str, language: str | None) -> Transcript:
        raise EngineUnavailable(self._detail)

    def warm(self) -> None:
        return None


def get_transcriber(env: Mapping[str, str] = os.environ) -> Transcriber:
    """Build (once) from SPEECH_ENGINE. Never raises for a config mistake: a bad setting shows up in the
    engine's status() instead, so the status route and the admin Overview can't be crashed by a typo. A missing
    Python package is such a mistake: the engine is a stand-in whose status is `error` naming the package."""
    global _transcriber
    if _transcriber is not None:
        return _transcriber
    engine = (env.get("SPEECH_ENGINE") or "local").strip().lower()
    model = (env.get("SPEECH_MODEL") or "").strip()
    if engine == "openai":
        try:
            from .openai_engine import OpenAICompatible
        except ImportError as e:
            _transcriber = _MissingPackage("openai", model, e.name or "a Python package")
        else:
            _transcriber = OpenAICompatible(env.get("SPEECH_URL"), env.get("SPEECH_API_KEY"), model)
    else:
        if engine != "local":
            logger.warning("SPEECH_ENGINE=%r is not 'local' or 'openai'; using local", engine)
        try:
            from .local_whisper import LocalWhisper
        except ImportError as e:
            _transcriber = _MissingPackage("local", model or DEFAULT_LOCAL_MODEL, e.name or "a Python package")
        else:
            _transcriber = LocalWhisper(model or DEFAULT_LOCAL_MODEL, _cpu_threads(env))
    if isinstance(_transcriber, _MissingPackage):
        logger.warning("Voice input can't start: %s", _transcriber._detail)
    return _transcriber


def _now() -> float:
    return time.monotonic()


_attempt_at: float | None = None   # when this worker last started (or first noticed it had failed) a load


def heal(engine: Transcriber, status: EngineStatus) -> None:
    """Every worker process has its own engine and an admin's switch reaches only one of them, so a worker that
    never loaded starts loading on first use (warm() is idempotent while loading), and one whose load FAILED
    tries again by itself, but at most once every RETRY_AFTER_S: a failing download can't restart on every page
    load, and a transient failure heals on every worker within about five minutes. The clock starts when the
    load starts, or, for a failure first seen here, when it is first seen. Switching Voice input off then on
    still retries at once on the worker that takes the switch (admin route, not this function)."""
    global _attempt_at
    if status.state == "error":
        now = _now()
        if _attempt_at is None:
            _attempt_at = now
            return
        if now - _attempt_at < RETRY_AFTER_S:
            return
    elif status.state == "loading":
        now = _now()
    else:
        return
    try:
        engine.warm()
    except Exception:  # noqa: BLE001 - starting a load must not fail the request that noticed
        logger.warning("The speech engine couldn't start loading", exc_info=True)
        return
    _attempt_at = now


def reset_for_tests() -> None:
    global _transcriber, _slot, _attempt_at
    _transcriber = None
    _slot = None
    _attempt_at = None
    _running.clear()


async def transcribe_for(user_id: str, audio: bytes, mime: str, language: str | None) -> Transcript:
    global _slot
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
