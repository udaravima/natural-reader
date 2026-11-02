"""The `local` engine: faster-whisper on CPU, int8, inside the backend process (spec §4.3)."""
from __future__ import annotations

import asyncio
import logging
import threading

from faster_whisper import WhisperModel

from . import catalogue, convert
from .audio import decode_capped
from .base import EngineFailed, EngineNotReady, EngineStatus, EngineUnavailable, SpeechError, Transcript
from .catalogue import SpeechModel

logger = logging.getLogger("server.speech")

# How Whisper listens, measured on real Sinhala, Tamil and English recordings (docs/DEPLOYMENT.md, "How it listens").
# The VAD isn't optional: without it Whisper invents text from silence, and loses a whole sentence after a 3 s pause.
# Its default threshold of 0.5 is too strict, though: it cut real speech and doubled the error on volunteer recordings.
# Below 0.35, loud steady noise starts to come out as invented words.
VAD_THRESHOLD = 0.4
# temperature 0 stops the retries a model makes when it doesn't understand a clip: stock `small` took 171 s on 8 Sinhala
# clips, and takes 27 s now. A 269-word English recording came out word for word the same. The other two common
# speed-ups stay off because each cost words:
# beam_size=1 turned "balance" into "field", and condition_on_previous_text=False dropped English at the end of a
# recording.
DECODE = {"vad_filter": True, "vad_parameters": {"threshold": VAD_THRESHOLD}, "temperature": 0}

NO_DETECTION = "This speech model can't detect the language. Choose one in Settings → Chat → Speech language."


class LocalWhisper:
    name = "local"
    where = "this server"

    def __init__(self, model: str | SpeechModel, cpu_threads: int) -> None:
        self.spec = model if isinstance(model, SpeechModel) else SpeechModel(model, model, catalogue.FASTER_WHISPER, model)
        self.model = self.spec.id
        self.languages = self.spec.languages
        self.cpu_threads = cpu_threads
        self._model = None
        self._state = "loading"
        self._detail = "The model has not started loading yet"
        self._lock = threading.Lock()
        self._started = False

    def __repr__(self) -> str:
        return f"LocalWhisper(model={self.model!r}, cpu_threads={self.cpu_threads})"

    def warm(self) -> None:
        """Load the model in a daemon thread (downloads into HF_HOME the first time). Safe to call repeatedly."""
        with self._lock:
            if self._started and self._state != "error":
                return  # loading or ready; a failed load is retried
            self._started = True
            self._state, self._detail = "loading", "Loading the model"
        threading.Thread(target=self._load, name="speech-warm", daemon=True).start()

    def _open(self):
        """The loaded model. Runs in the loading thread; a converted model is converted here the first time."""
        path = self.spec.repo
        kwargs = {"revision": self.spec.revision} if self.spec.revision else {}
        if self.spec.engine == catalogue.CONVERTED:
            path, kwargs = str(convert.ensure_converted(self.spec, self.cpu_threads)), {}
        return WhisperModel(path, device="cpu", compute_type="int8", cpu_threads=self.cpu_threads, **kwargs)

    def _load(self) -> None:
        try:
            reason = catalogue.why_not(self.spec)
            if reason:
                raise SpeechError(reason)
            model = self._open()
        except Exception as e:  # noqa: BLE001 - surfaced through status(), never raised into the thread
            logger.warning("speech model %r failed to load: %s", self.model, type(e).__name__)
            with self._lock:
                self._state, self._detail = "error", str(e) if isinstance(e, SpeechError) else type(e).__name__
            return
        with self._lock:
            self._model, self._state, self._detail = model, "ready", ""
        logger.info("speech model %r ready", self.model)

    async def status(self) -> EngineStatus:
        with self._lock:
            return EngineStatus(self._state == "ready", self._state, self._detail)

    @staticmethod
    def _run(samples, language: str | None, model) -> Transcript:
        segments, info = model.transcribe(samples, language=language, **DECODE)
        text = "".join(seg.text for seg in segments).strip()
        return Transcript(text, info.language, float(info.duration))

    async def transcribe(self, audio: bytes, mime: str, language: str | None) -> Transcript:
        with self._lock:
            model, state, detail = self._model, self._state, self._detail
        if model is None:
            if state == "error":
                raise EngineUnavailable(detail)
            raise EngineNotReady("The speech model is still loading")
        # With Auto, a model made for one language is told it: Whisper's detection is unreliable on a fine-tune. A
        # language the person chose stays theirs, because some fine-tunes still read other languages.
        language = language or (self.languages[0] if len(self.languages) == 1 else None)
        samples = await asyncio.to_thread(decode_capped, audio)  # bounded; raises AudioTooLong / BadAudio
        try:
            return await asyncio.to_thread(self._run, samples, language, model)
        except SpeechError:
            raise
        except Exception as e:  # noqa: BLE001 - a model that fails on one recording is a message, not a 500
            logger.warning("speech model %r failed on a recording: %s", self.model, type(e).__name__)
            # Auto is the likely cause: a fine-tune whose config lists no languages gives Whisper nothing to detect.
            raise EngineFailed(NO_DETECTION if language is None else "The speech model failed on that recording.") from e
