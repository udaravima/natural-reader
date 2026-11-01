"""The `local` engine: faster-whisper on CPU, int8, inside the backend process (spec §4.3)."""
from __future__ import annotations

import asyncio
import io
import logging
import threading

from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio

from .base import (MAX_AUDIO_SECONDS, AudioTooLong, BadAudio, EngineNotReady, EngineStatus,
                   Transcript)

logger = logging.getLogger("server.speech")

SAMPLE_RATE = 16000  # decode_audio's output rate


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
            if self._started:
                return
            self._started = True
            self._detail = "Loading the model"
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

    def _run(self, audio: bytes, language: str | None, model) -> Transcript:
        try:
            samples = decode_audio(io.BytesIO(audio), sampling_rate=SAMPLE_RATE)
        except Exception as e:  # noqa: BLE001 - PyAV raises many types for undecodable input
            raise BadAudio("The recording could not be read") from e
        if len(samples) / SAMPLE_RATE > MAX_AUDIO_SECONDS:
            raise AudioTooLong(f"A recording can be at most {MAX_AUDIO_SECONDS} seconds")
        segments, info = model.transcribe(samples, language=language, vad_filter=True)
        text = "".join(s.text for s in segments).strip()
        return Transcript(text, info.language, float(info.duration))

    async def transcribe(self, audio: bytes, mime: str, language: str | None) -> Transcript:
        with self._lock:
            model, state, detail = self._model, self._state, self._detail
        if model is None:
            raise EngineNotReady(detail if state == "error" else "The speech model is still loading")
        return await asyncio.to_thread(self._run, audio, language, model)
