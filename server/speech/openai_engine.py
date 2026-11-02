"""The `openai` engine: any server that speaks POST {SPEECH_URL}/audio/transcriptions (spec §4.3).

The API key is only ever put in a request header: it is not logged, not in exception messages, not in repr()."""
from __future__ import annotations

import asyncio
import io
import logging
from urllib.parse import urlparse

import httpx

from .languages import WHISPER_LANGUAGES
from .audio import SAMPLE_RATE, decode_capped
from .base import EngineFailed, EngineStatus, EngineUnavailable, Transcript

logger = logging.getLogger("server.speech")

# Finite on purpose: a hung server must end in "didn't answer", not a request that waits forever.
# Reading allows for a slow server transcribing a two-minute recording.
TIMEOUT = httpx.Timeout(120.0, connect=10.0)
PROBE_TIMEOUT = httpx.Timeout(10.0)

_DIDNT_ANSWER = "The transcription server didn't answer"
_REFUSED = "The transcription server refused this server's key"
_EXT = {"webm": "webm", "ogg": "ogg", "mp4": "mp4", "m4a": "m4a", "x-m4a": "m4a", "wav": "wav", "x-wav": "wav",
        "wave": "wav", "mpeg": "mp3", "mp3": "mp3", "flac": "flac"}


def _base_mime(mime: str) -> str:
    return (mime or "").split(";")[0].strip().lower() or "application/octet-stream"


_CODE_BY_NAME = {n.lower(): c for c, n in WHISPER_LANGUAGES.items()}


def _language_code(value) -> str | None:
    """OpenAI answers with a name ("english"); other servers with a code ("en"). Return a code when we can."""
    v = str(value or "").strip().lower()
    if not v:
        return None
    return v if v in WHISPER_LANGUAGES else _CODE_BY_NAME.get(v, v)


def _filename(mime: str) -> str:
    sub = _base_mime(mime).rpartition("/")[2]
    return f"audio.{_EXT.get(sub, 'webm')}"


class OpenAICompatible:
    name = "openai"

    def __init__(self, url: str | None, api_key: str | None, model: str | None,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._url = (url or "").strip().rstrip("/")
        self._key = (api_key or "").strip()
        self.model = (model or "").strip()
        self._transport = transport
        self.where = urlparse(self._url).hostname or self._url or "an unset server"
        # A config mistake must not crash the status route: it is reported through status()/transcribe().
        problems = []
        if not self._url:
            problems.append("SPEECH_URL is not set")
        if not self.model:
            problems.append("SPEECH_MODEL is not set")
        self._problem = "; ".join(problems)

    def __repr__(self) -> str:
        return f"OpenAICompatible(where={self.where!r}, model={self.model!r})"

    def warm(self) -> None:
        return None

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key}"} if self._key else {}

    def _client(self, timeout: httpx.Timeout) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self._transport)

    async def status(self) -> EngineStatus:
        if self._problem:
            return EngineStatus(False, "error", self._problem)
        try:
            async with self._client(PROBE_TIMEOUT) as c:
                r = await c.get(f"{self._url}/models", headers=self._headers())
        except httpx.HTTPError:
            return EngineStatus(False, "error", _DIDNT_ANSWER)
        if r.status_code in (401, 403):
            return EngineStatus(False, "error", _REFUSED)
        if r.status_code != 200:
            return EngineStatus(False, "error", f"The transcription server answered {r.status_code}")
        return EngineStatus(True, "ready", "")

    async def _post(self, c: httpx.AsyncClient, audio: bytes, mime: str, language: str | None,
                    response_format: str) -> httpx.Response:
        data = {"model": self.model, "response_format": response_format}
        if language:
            data["language"] = language
        return await c.post(f"{self._url}/audio/transcriptions", headers=self._headers(), data=data,
                            files={"file": (_filename(mime), io.BytesIO(audio), _base_mime(mime))})

    async def transcribe(self, audio: bytes, mime: str, language: str | None) -> Transcript:
        if self._problem:
            raise EngineUnavailable(self._problem)
        # Bounded decode first, off the event loop: an over-long or unreadable recording never reaches the server.
        samples = await asyncio.to_thread(decode_capped, audio)
        decoded_s = len(samples) / SAMPLE_RATE
        try:
            async with self._client(TIMEOUT) as c:
                r = await self._post(c, audio, mime, language, "verbose_json")
                if r.status_code == 400 and "response_format" in r.text:
                    r = await self._post(c, audio, mime, language, "json")
                    return self._parse(r, None, decoded_s, force_duration=True)
        except httpx.HTTPError as e:
            logger.warning("transcription request failed: %s", type(e).__name__)
            raise EngineFailed(_DIDNT_ANSWER) from None
        return self._parse(r, language, decoded_s)

    def _parse(self, r: httpx.Response, language: str | None, decoded_s: float,
               force_duration: bool = False) -> Transcript:
        if r.status_code in (401, 403):
            raise EngineFailed(_REFUSED)
        if r.status_code != 200:
            logger.warning("transcription server answered %s", r.status_code)
            raise EngineFailed(f"The transcription server answered {r.status_code}")
        try:
            body = r.json()
            text = str(body["text"]).strip()
        except (ValueError, KeyError, TypeError):
            raise EngineFailed("The transcription server sent an answer this server can't read") from None
        duration = decoded_s
        if not force_duration:
            raw = body.get("duration")
            if isinstance(raw, (int, float)):
                duration = float(raw)
            language = _language_code(body.get("language")) or language
        return Transcript(text, language or None, duration)
