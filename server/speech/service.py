"""The transcribers of this process, and the rules around using them: one running transcription per person, and
the local engines one at a time per process with a bounded wait (spec §5.4).

A remote engine is one object. Local models live in a pool (spec 2026-11-04-speech-choices-per-person, Part 2): up to
SPEECH_MODELS_LOADED of them per worker, so people speaking different languages are each heard by their own model.
A request resolves its engine once and holds it to the end (transcribe_for(engine=...)); a model in use is never
dropped; the default keeps a place while there are two or more; the least recently used other goes first, and its
memory is handed back explicitly (_release_memory). A recording for a model still loading waits for it."""
from __future__ import annotations

import asyncio
import ctypes
import gc
import logging
import os
import time
from collections import Counter, OrderedDict
from collections.abc import Mapping

from . import catalogue
from .base import (MAX_AUDIO_BYTES, MAX_AUDIO_SECONDS, EngineNotReady, EngineStatus, EngineUnavailable,
                   Transcriber, Transcript)
from .catalogue import SpeechModel
# The engines import faster-whisper, PyAV and numpy. They are imported inside get_transcriber, so a deployment
# that pulled new code without `pip install -r requirements.txt` still starts, and says so in the voice status.

logger = logging.getLogger("server.speech")

LOCAL_WAIT_S = 60  # how long a second recording waits for the local engine before "busy"
RETRY_AFTER_S = 300  # a worker whose engine failed to load tries again by itself at most this often (seconds)
MODELS_LOADED_DEFAULT, MODELS_LOADED_RANGE = 3, (1, 4)   # local models kept loaded per worker (SPEECH_MODELS_LOADED)
LOAD_POLL_S = 0.1   # how often a recording waiting for its model asks whether it has loaded

__all__ = ["AlreadyRunning", "Busy", "ENGINES", "LOCAL_WAIT_S", "MAX_AUDIO_BYTES", "MAX_AUDIO_SECONDS",
           "RETRY_AFTER_S", "chosen_engine", "engine_name", "engine_needs", "engine_source", "engines",
           "get_transcriber", "heal", "models_loaded", "reset_for_tests", "transcribe_for"]

ENGINES = ("local", "openai", "google")
_LABELS = {"local": "Whisper on this server", "openai": "An OpenAI-compatible server", "google": "Google Cloud"}


class AlreadyRunning(Exception):
    """This person already has a transcription in progress."""


class Busy(Exception):
    """The local engine's slot was not free within LOCAL_WAIT_S."""


class _NoChoice:
    """Chat options with no admin choice: what get_transcriber uses when a caller passes no `wanted`."""
    speech_model: str | None = None
    added_speech_models: dict = {}


_NO_CHOICE = _NoChoice()
_transcriber: Transcriber | None = None   # the remote engine, or the local engine last asked for
_pool: OrderedDict[str, Transcriber] = OrderedDict()   # local engines by model id, least recently used first
_in_use: Counter = Counter()   # model id -> transcriptions holding it
_default_id: str | None = None   # the deployment's model: it keeps its place in the pool
_running: set[str] = set()
_slot: asyncio.Semaphore | None = None
_warned: set[str] = set()


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
    languages: tuple[str, ...] = ()

    def __init__(self, name: str, model: str, package: str, spec: SpeechModel | None = None) -> None:
        self.name, self.model, self.spec = name, model, spec
        self._detail = f"The Python package {package} is not installed: run pip install -r requirements.txt"

    async def status(self) -> EngineStatus:
        return EngineStatus(False, "error", self._detail)

    async def transcribe(self, audio: bytes, mime: str, language: str | None) -> Transcript:
        raise EngineUnavailable(self._detail)

    def warm(self) -> None:
        return None


def engine_name(env: Mapping[str, str] = os.environ) -> str:
    return (env.get("SPEECH_ENGINE") or "local").strip().lower()


def engine_needs(name: str, env: Mapping[str, str] = os.environ) -> str:
    """What an engine still needs from the environment before it can be chosen; empty when nothing."""
    if name == "openai":
        missing = [k for k in ("SPEECH_URL", "SPEECH_MODEL") if not (env.get(k) or "").strip()]
        return " and ".join(missing)
    if name == "google":
        from .google_engine import is_recognizer_name
        return "" if is_recognizer_name(env.get("GOOGLE_SPEECH_RECOGNIZER")) else "GOOGLE_SPEECH_RECOGNIZER"
    return ""


def engines(env: Mapping[str, str] = os.environ) -> list[dict]:
    return [{"id": name, "label": _LABELS[name], "available": not engine_needs(name, env),
             "needs": engine_needs(name, env)} for name in ENGINES]


def chosen_engine(options, env: Mapping[str, str] = os.environ) -> str:
    """The engine for hearing speech: the admin's choice while it can still run here, else SPEECH_ENGINE (or
    local). A choice whose setting a deployer has since removed falls back rather than breaking voice input."""
    choice = getattr(options, "speech_engine", None)
    if choice in ENGINES and not engine_needs(choice, env):
        return choice
    return engine_name(env)


def engine_source(options, env: Mapping[str, str] = os.environ) -> str:
    choice = getattr(options, "speech_engine", None)
    if choice in ENGINES and not engine_needs(choice, env):
        return "admin"
    return "env" if (env.get("SPEECH_ENGINE") or "").strip() else "default"


def _build_local(spec: SpeechModel, env: Mapping[str, str]) -> Transcriber:
    try:
        if spec.engine == catalogue.TRANSFORMERS:
            from .hf_whisper import TransformersWhisper as Engine
        else:
            from .local_whisper import LocalWhisper as Engine
    except ImportError as e:
        return _MissingPackage("local", spec.id, e.name or "a Python package", spec)
    return Engine(spec, _cpu_threads(env))


def models_loaded(env: Mapping[str, str] = os.environ) -> int:
    """SPEECH_MODELS_LOADED: how many local models a worker keeps loaded, 1 to 4 (default 3). It is a ceiling: a
    model loads only when someone speaks its language."""
    raw = (env.get("SPEECH_MODELS_LOADED") or "").strip()
    if not raw:
        return MODELS_LOADED_DEFAULT
    low, high = MODELS_LOADED_RANGE
    try:
        value = int(raw)
    except ValueError:
        value = None
    if value is None or not low <= value <= high:
        if raw not in _warned:
            _warned.add(raw)
            logger.warning("SPEECH_MODELS_LOADED=%r isn't a whole number from %d to %d; using %d", raw, low, high,
                           MODELS_LOADED_DEFAULT)
        return MODELS_LOADED_DEFAULT
    return value


def _release_memory() -> None:
    """Hand a dropped model's memory back: collect it, then ask glibc to return its free pages. Without the trim
    about 250 MB of each dropped model stays with the process (measured 2026-11-04, spec Part 2)."""
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):   # not glibc (macOS, Windows, musl): nothing to ask
        pass


def _shrink(env: Mapping[str, str], keep: str | None = None) -> None:
    """Drop least recently used local models until the pool fits: never one in use, nor `keep` (the model just asked
    for), nor the default while there are two or more places."""
    global _transcriber
    limit = models_loaded(env)
    dropped = False
    for model_id in list(_pool):
        if len(_pool) <= limit:
            break
        if _in_use[model_id] or model_id == keep or (limit >= 2 and model_id == _default_id):
            continue
        gone = _pool.pop(model_id)
        _attempt_at.pop(model_id, None)
        if gone is _transcriber:
            _transcriber = next(reversed(_pool.values()), None)
        dropped = True
    if dropped:
        _release_memory()


def _drop_local_models() -> None:
    """A remote engine now hears speech: the local models not in use go."""
    for model_id in [i for i in _pool if not _in_use[i]]:
        _pool.pop(model_id)
        _attempt_at.pop(model_id, None)
    _release_memory()


def get_transcriber(env: Mapping[str, str] = os.environ, wanted: SpeechModel | None = None,
                    engine: str | None = None, default: SpeechModel | None = None) -> Transcriber:
    """Build (once) from SPEECH_ENGINE. Never raises for a config mistake: a bad setting shows up in the
    engine's status() instead, so the status route and the admin Overview can't be crashed by a typo. A missing
    Python package is such a mistake: the engine is a stand-in whose status is `error` naming the package.

    `wanted` is the model the deployment's options ask for (catalogue.wanted). If this worker's local engine runs
    another, it's replaced now and loads on first use: an admin's switch reaches one worker, and every other
    worker finds out here, on the next speech request. Local models stay in the pool (up to models_loaded), so
    switching back is quick; a recording already running finishes on the engine it started on.

    `engine` is the engine the options ask for (chosen_engine); None keeps whatever runs, or SPEECH_ENGINE.
    `default` is the deployment's model (catalogue.wanted), which keeps its place in the pool; None: `wanted` is it."""
    global _transcriber, _default_id
    if _transcriber is not None:
        same_engine = engine is None or _transcriber.name == engine
        running = getattr(_transcriber, "spec", None)   # only a local engine has one
        if same_engine and (wanted is None or running is None or running == wanted):
            return _transcriber
    engine = engine or engine_name(env)
    model = (env.get("SPEECH_MODEL") or "").strip()
    if engine in ("google", "openai") and _pool:
        _drop_local_models()
    if engine == "google":
        from .google_engine import GoogleSpeech, timeout_from
        _transcriber = GoogleSpeech(env.get("GOOGLE_SPEECH_RECOGNIZER"), timeout_s=timeout_from(env))
    elif engine == "openai":
        try:
            from .openai_engine import OpenAICompatible
        except ImportError as e:
            _transcriber = _MissingPackage("openai", model, e.name or "a Python package")
        else:
            _transcriber = OpenAICompatible(env.get("SPEECH_URL"), env.get("SPEECH_API_KEY"), model)
    else:
        if engine != "local":
            logger.warning("SPEECH_ENGINE=%r is not 'local', 'openai' or 'google'; using local", engine)
        spec = wanted or catalogue.wanted(_NO_CHOICE, env)
        _default_id = (default or spec).id
        held = getattr(_transcriber, "spec", None)
        if held is not None and getattr(_transcriber, "name", None) == "local":
            _pool.setdefault(held.id, _transcriber)   # one put in place from outside (a test, a hot reload)
        found = _pool.get(spec.id)
        if found is None or getattr(found, "spec", None) != spec:
            found = _build_local(spec, env)
            _pool[spec.id] = found
            _attempt_at.pop(spec.id, None)
        _pool.move_to_end(spec.id)
        _transcriber = found
        _shrink(env, keep=spec.id)
    if isinstance(_transcriber, _MissingPackage):
        logger.warning("Voice input can't start: %s", _transcriber._detail)
    return _transcriber


def _now() -> float:
    return time.monotonic()


_attempt_at: dict[str, float] = {}   # model -> when this worker last started (or first saw it had failed) a load


def heal(engine: Transcriber, status: EngineStatus) -> None:
    """Every worker process has its own engine and an admin's switch reaches only one of them, so a worker that
    never loaded starts loading on first use (warm() is idempotent while loading), and one whose load FAILED
    tries again by itself, but at most once every RETRY_AFTER_S: a failing download can't restart on every page
    load, and a transient failure heals on every worker within about five minutes. The clock starts when the
    load starts, or, for a failure first seen here, when it is first seen. Switching Voice input off then on
    still retries at once on the worker that takes the switch (admin route, not this function)."""
    key = _key_of(engine) or getattr(engine, "name", "")
    if status.state == "error":
        now = _now()
        if key not in _attempt_at:
            _attempt_at[key] = now
            return
        if now - _attempt_at[key] < RETRY_AFTER_S:
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
    _attempt_at[key] = now


def reset_for_tests() -> None:
    global _transcriber, _slot, _default_id
    _transcriber = None
    _slot = None
    _default_id = None
    _attempt_at.clear()
    _pool.clear()
    _in_use.clear()
    _running.clear()
    _warned.clear()


def _key_of(engine) -> str | None:
    """A pooled local engine's model id; None for a remote engine or a stand-in without a model."""
    spec = getattr(engine, "spec", None)
    return spec.id if spec is not None and getattr(engine, "name", None) == "local" else None


async def _until_ready(engine: Transcriber, deadline: float) -> None:
    """Wait for a local model that is still loading, starting its load if nothing has. Its load failing is
    EngineUnavailable; still loading at the deadline is EngineNotReady."""
    engine.warm()
    while True:
        status = await engine.status()
        if status.state == "ready":
            return
        if status.state == "error":
            raise EngineUnavailable(status.detail or "The speech model couldn't load")
        if time.monotonic() >= deadline:
            raise EngineNotReady("The speech model is still loading")
        await asyncio.sleep(LOAD_POLL_S)


async def _on_the_slot(engine: Transcriber, audio: bytes, mime: str, language: str | None, deadline: float):
    """One local transcription, in the one-at-a-time slot, waiting for it no later than `deadline`."""
    global _slot
    if _slot is None:
        _slot = asyncio.Semaphore(1)
    slot = _slot
    try:
        await asyncio.wait_for(slot.acquire(), max(0.0, deadline - time.monotonic()))
    except asyncio.TimeoutError:
        raise Busy() from None
    # A thread can't be stopped: if the caller cancels, the slot stays held until the work really ends.
    work = asyncio.ensure_future(engine.transcribe(audio, mime, language))
    work.add_done_callback(lambda f: (f.cancelled() or f.exception(), slot.release()))
    return await asyncio.shield(work)


async def transcribe_for(user_id: str, audio: bytes, mime: str, language: str | None,
                         engine: Transcriber | None = None) -> Transcript:
    """Transcribe on `engine`, the one the request resolved (None: this worker's current one). The engine is held
    from here to the end, so it can't be dropped from the pool meanwhile. A local model still loading is waited
    for, outside the slot, then tried once more; the slot and the load share one wait of LOCAL_WAIT_S."""
    engine = engine or get_transcriber()
    if user_id in _running:
        raise AlreadyRunning()
    _running.add(user_id)
    key = _key_of(engine)
    if key is not None:
        _in_use[key] += 1
        if key in _pool:
            _pool.move_to_end(key)
    try:
        started = time.monotonic()
        if engine.name == "local":
            deadline = started + LOCAL_WAIT_S
            try:
                result = await _on_the_slot(engine, audio, mime, language, deadline)
            except EngineNotReady:
                await _until_ready(engine, deadline)
                result = await _on_the_slot(engine, audio, mime, language, deadline)
        else:
            result = await engine.transcribe(audio, mime, language)
    finally:
        _running.discard(user_id)
        if key is not None:
            _in_use[key] -= 1
            if _in_use[key] <= 0:
                del _in_use[key]
            _shrink(os.environ)
    logger.info("transcribed user=%s engine=%s model=%s seconds=%.1f language=%s elapsed_ms=%d", user_id,
                engine.name, getattr(engine, "model", "") or "", result.duration_s, result.language,
                int((time.monotonic() - started) * 1000))
    return result
