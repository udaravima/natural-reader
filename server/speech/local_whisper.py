"""The `local` engine: faster-whisper on CPU, int8, inside the backend process (spec §4.3)."""
from __future__ import annotations

import asyncio
import logging
import threading

from faster_whisper import WhisperModel

from .audio import SAMPLE_RATE, decode_capped
from .base import EngineNotReady, EngineStatus, Transcript

logger = logging.getLogger("server.speech")


class LocalWhisper:
    name = "local"
    where = "this server"

    def __init__(self, model: str, cpu_threads: int) -> None:
        self.model = model
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

    def _load(self) -> None:
        try:
            model = WhisperModel(self.model, device="cpu", compute_type="int8", cpu_threads=self.cpu_threads)
        except Exception as e:  # noqa: BLE001 - surfaced through status(), never raised into the thread
            logger.warning("speech model %r failed to load: %s", self.model, type(e).__name__)
            with self._lock:
                self._state, self._detail = "error", type(e).__name__
            return
        with self._lock:
            self._model, self._state, self._detail = model, "ready", ""
        logger.info("speech model %r ready", self.model)

    async def status(self) -> EngineStatus:
        with self._lock:
            return EngineStatus(self._state == "ready", self._state, self._detail)

    @staticmethod
    def _run(samples, language: str | None, model) -> Transcript:
        segments, info = model.transcribe(samples, language=language, vad_filter=True)
        text = "".join(seg.text for seg in segments).strip()
        return Transcript(text, info.language, float(info.duration))

    async def transcribe(self, audio: bytes, mime: str, language: str | None) -> Transcript:
        with self._lock:
            model, state, detail = self._model, self._state, self._detail
        if model is None:
            raise EngineNotReady(detail if state == "error" else "The speech model is still loading")
        samples = await asyncio.to_thread(decode_capped, audio)  # bounded; raises AudioTooLong / BadAudio
        return await asyncio.to_thread(self._run, samples, language, model)
