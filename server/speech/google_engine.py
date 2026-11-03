"""The `google` engine: Google Cloud Speech-to-Text v2, through a recognizer the deployer made (spec
2026-11-03-google-speech).

GOOGLE_SPEECH_RECOGNIZER is the recognizer's full name, `projects/<p>/locations/<l>/recognizers/<id>`; its location
picks the endpoint. Every clip is decoded here first and sent as 16 kHz mono WAV, so what the browser recorded
doesn't matter to Google. The recognizer's own settings apply (model, punctuation, confidence and the rest are the
deployer's choice in Google's console); the only overrides are the decoding, which describes the audio sent, and
the language, when the person named one. Google gives no Whisper scores: `no_speech` and `avg_logprob` stay None.

The token is only ever put in a request header: it is not logged, not in exception messages, not in repr()."""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import re
import time
from collections.abc import Awaitable, Callable

import httpx
import numpy as np
import soundfile as sf

from .. import google_cloud
from .audio import SAMPLE_RATE, decode_capped
from .base import EngineFailed, EngineStatus, EngineUnavailable, Transcript

logger = logging.getLogger("server.speech")

MAX_SECONDS = 60   # Google's synchronous recognize takes at most a minute of audio (or 10 MB)
STATUS_TTL_S = 60  # how long a status answer is kept: the composer asks on every page load
_NAME = re.compile(r"^projects/([^/]+)/locations/([^/]+)/recognizers/([^/]+)$")
# The app's two-letter codes, for a recognizer that doesn't list the language itself.
USUAL_CODES = {"en": "en-US", "si": "si-LK", "ta": "ta-IN"}

_NOT_SET = ("GOOGLE_SPEECH_RECOGNIZER isn't a full recognizer name: it must look like "
            "projects/<project>/locations/<location>/recognizers/<id>")
_DIDNT_ANSWER = "Google didn't answer"
_REFUSED = "Google refused this server's key"
_FORBIDDEN = "The service account can't use this recognizer: give it Cloud Speech Client on the recognizer's project"
_MISSING = "No such recognizer: check GOOGLE_SPEECH_RECOGNIZER, project and location included"
_QUOTA = "Google's quota for speech is used up for now"


def is_recognizer_name(value: str | None) -> bool:
    return bool(_NAME.match((value or "").strip()))


def timeout_from(env) -> float:
    """GOOGLE_TIMEOUT_S, in seconds; a bad value keeps the default."""
    try:
        return max(1.0, float(env.get("GOOGLE_TIMEOUT_S") or 30))
    except ValueError:
        return 30.0


async def _default_token() -> tuple[str, str | None]:
    return await google_cloud.access_token()


def _primary(code: str) -> str:
    return code.split("-")[0].lower()


class GoogleSpeech:
    name = "google"

    def __init__(self, recognizer: str | None, *, transport: httpx.AsyncBaseTransport | None = None,
                 token: Callable[[], Awaitable[tuple[str, str | None]]] = _default_token,
                 timeout_s: float = 30.0) -> None:
        self.recognizer = (recognizer or "").strip()
        match = _NAME.match(self.recognizer)
        self._location = match.group(2) if match else ""
        self.model = match.group(3) if match else ""    # the recognizer's id, until its model is read
        self.languages: tuple[str, ...] = ()            # the app's codes, from the recognizer: empty is every one
        self.auto_hears: str | None = None              # the one language Auto gets, when it can't detect (below)
        self.where = "Google Cloud"
        self._codes: tuple[str, ...] = ()               # the recognizer's own codes, such as en-GB
        self._transport = transport
        self._token = token
        self._timeout = httpx.Timeout(timeout_s, connect=10.0)
        self._status: tuple[float, EngineStatus] | None = None

    def __repr__(self) -> str:
        return f"GoogleSpeech(recognizer={self.recognizer!r})"

    @property
    def endpoint(self) -> str:
        if self._location == "global":
            return "https://speech.googleapis.com"
        return f"https://{self._location}-speech.googleapis.com"

    def warm(self) -> None:
        return None

    async def _headers(self) -> dict[str, str]:
        try:
            token, _project = await self._token()
        except google_cloud.GoogleAuthError as e:
            raise EngineUnavailable(str(e)) from None
        return {"Authorization": f"Bearer {token}"}

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, transport=self._transport)

    async def status(self) -> EngineStatus:
        if not self._location:
            return EngineStatus(False, "error", _NOT_SET)
        if self._status and time.monotonic() - self._status[0] < STATUS_TTL_S:
            return self._status[1]
        status = await self._probe()
        self._status = (time.monotonic(), status)
        return status

    async def _probe(self) -> EngineStatus:
        try:
            headers = await self._headers()
            async with self._client() as c:
                r = await c.get(f"{self.endpoint}/v2/{self.recognizer}", headers=headers)
        except EngineUnavailable as e:
            return EngineStatus(False, "error", str(e))
        except httpx.HTTPError as e:
            logger.warning("Google recognizer probe failed: %s", type(e).__name__)
            return EngineStatus(False, "error", _DIDNT_ANSWER)
        if r.status_code != 200:
            return EngineStatus(False, "error", self._refusal(r)[1])
        try:
            body = r.json()
        except ValueError:
            return EngineStatus(False, "error", "Google sent an answer this server can't read")
        self.model = str(body.get("model") or self.model)
        codes = tuple(str(c) for c in body.get("languageCodes") or () if c)
        self._codes = codes
        self.languages = tuple(dict.fromkeys(_primary(c) for c in codes if c != "auto"))
        # Asked for no language, a recognizer with several hears its first one, whatever is spoken (`long`, live
        # 2026-11-03: English came back as Sinhala). Only `auto` (chirp_3) detects the language.
        self.auto_hears = self.languages[0] if len(self.languages) > 1 and "auto" not in codes else None
        return EngineStatus(True, "ready", "")

    @staticmethod
    def _refusal(r: httpx.Response) -> tuple[type, str]:
        code = r.status_code
        if code == 401:
            return EngineUnavailable, _REFUSED
        if code == 403:
            off = google_cloud.service_off(r)
            return EngineUnavailable, f"{off}, or check the project in GOOGLE_SPEECH_RECOGNIZER" if off else _FORBIDDEN
        if code == 404:
            return EngineUnavailable, _MISSING
        if code == 429:
            return EngineFailed, _QUOTA
        return EngineFailed, _DIDNT_ANSWER

    def _code_for(self, language: str) -> str:
        for code in self._codes:
            if _primary(code) == language:
                return code
        return USUAL_CODES.get(language, language)

    async def transcribe(self, audio: bytes, mime: str, language: str | None) -> Transcript:
        if not self._location:
            raise EngineUnavailable(_NOT_SET)
        # Bounded decode first, off the event loop: an over-long or unreadable recording never reaches Google.
        samples = await asyncio.to_thread(decode_capped, audio, MAX_SECONDS)
        duration = len(samples) / SAMPLE_RATE
        wav = io.BytesIO()
        sf.write(wav, np.asarray(samples, dtype=np.float32), SAMPLE_RATE, format="WAV", subtype="PCM_16")
        config: dict = {"autoDecodingConfig": {}}
        mask = ["autoDecodingConfig"]
        if language:
            config["languageCodes"] = [self._code_for(language)]
            mask.append("languageCodes")
        body = {"config": config, "configMask": ",".join(mask),
                "content": base64.b64encode(wav.getvalue()).decode("ascii")}
        headers = await self._headers()
        try:
            async with self._client() as c:
                r = await c.post(f"{self.endpoint}/v2/{self.recognizer}:recognize", headers=headers, json=body)
        except httpx.HTTPError as e:
            logger.warning("Google recognize failed: %s", type(e).__name__)
            raise EngineFailed(_DIDNT_ANSWER) from None
        if r.status_code == 400:
            raise EngineFailed(f"Google refused the recording: {self._message(r)}")
        if r.status_code != 200:
            logger.warning("Google recognize answered %s", r.status_code)
            kind, words = self._refusal(r)
            raise kind(words)
        try:
            results = r.json().get("results") or []
        except ValueError:
            raise EngineFailed("Google sent an answer this server can't read") from None
        parts, detected = [], None
        for result in results:
            alternatives = result.get("alternatives") or []
            text = str(alternatives[0].get("transcript") or "").strip() if alternatives else ""
            if text:
                parts.append(text)
                detected = detected or result.get("languageCode")
        return Transcript(" ".join(parts), _primary(detected) if detected else None, duration)

    @staticmethod
    def _message(r: httpx.Response) -> str:
        """Google's own words for a bad request (they carry no credentials), cut short."""
        try:
            message = str(r.json().get("error", {}).get("message") or "")
        except ValueError:
            message = ""
        return (message or f"status {r.status_code}")[:300]
