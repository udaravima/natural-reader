"""The `google` engine: Google Cloud Speech-to-Text v2, through a recognizer the deployer made (spec
2026-11-03-google-speech).

A recognizer is named in full, `projects/<p>/locations/<l>/recognizers/<id>`: an admin ticks recognizers in the
console from a listing this module reads from Google (spec 2026-11-04-google-recognizers), else
GOOGLE_SPEECH_RECOGNIZER names one. Its location picks the endpoint, so a name must match a strict pattern before any
request is built from it: a location such as `evil.example#` would otherwise send the token to another host. Every clip is decoded here first and sent as 16 kHz mono WAV, so what the browser recorded
doesn't matter to Google. The recognizer's own settings apply (model, punctuation, confidence and the rest are the
deployer's choice in Google's console); the only overrides are the decoding, which describes the audio sent, and
the language, when the person named one. Google gives no Whisper scores: `no_speech` and `avg_logprob` stay None.

The token is only ever put in a request header: it is not logged, not in exception messages, not in repr()."""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field

import httpx
import numpy as np
import soundfile as sf

from .. import google_cloud
from .audio import SAMPLE_RATE, decode_capped
from .base import EngineFailed, EngineStatus, EngineUnavailable, Transcript
from .google_names import NAME, PART_RE, endpoint, is_recognizer_name, split_name

__all__ = ["GoogleSpeech", "Listing", "canonical", "is_recognizer_name", "list_recognizers", "split_name",
           "timeout_from"]

logger = logging.getLogger("server.speech")

MAX_SECONDS = 60   # Google's synchronous recognize takes at most a minute of audio (or 10 MB)
STATUS_TTL_S = 60  # how long a status answer is kept: the composer asks on every page load
LIST_CONCURRENCY = 20   # locations listed at once: Speech v2 has 17, each its own host (6 at once took 5.5 s, all 3 s)
DETECTS_LANGUAGE = frozenset({"chirp_3"})   # models that pick among a recognizer's languages, measured live
# The app's two-letter codes, for a recognizer that doesn't list the language itself.
USUAL_CODES = {"en": "en-US", "si": "si-LK", "ta": "ta-IN"}

_NOT_SET = ("No recognizer: tick one under Admin → Chat options → Speech, or set GOOGLE_SPEECH_RECOGNIZER to a full "
            "name, projects/<project>/locations/<location>/recognizers/<id>")
_DIDNT_ANSWER = "Google didn't answer"
_REFUSED = "Google refused this server's key"
_FORBIDDEN = "The service account can't use this recognizer: give it Cloud Speech Client on the recognizer's project"
_MISSING = ("No such recognizer: deleted in Google, or GOOGLE_SPEECH_RECOGNIZER names it wrongly (project and "
            "location included)")
_QUOTA = "Google's quota for speech is used up for now"


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
        match = NAME.match(self.recognizer)   # strict (google_names.py): a location can't pick another host
        self._location = match.group(2) if match else ""
        self.id = match.group(3) if match else ""
        self.model = self.id                             # the recognizer's id, until its model is read
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
        return endpoint(self._location or "global")

    @property
    def label(self) -> str:
        """The recognizer and its model, as people see it: `asr-ta-en (chirp_3)`; the id alone until it's read."""
        return f"{self.id} ({self.model})" if self.model and self.model != self.id else self.id

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
        # 2026-11-03: English came back as Sinhala). chirp_3 picks among its languages (live 2026-11-04: ta-IN and
        # en-GB, an English clip came back en-GB and a Tamil one ta-IN), as `auto` does.
        detects = "auto" in codes or self.model in DETECTS_LANGUAGE
        self.auto_hears = self.languages[0] if len(self.languages) > 1 and not detects else None
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
            # Google's own words (they carry no credential): a recognizer that reads fine can still refuse every clip,
            # and only this says why (live 2026-11-04: chirp_3 "no longer generally available" in asia-southeast1).
            logger.warning("Google recognize answered %s for %s: %s", r.status_code, self.id, self._message(r))
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


# ---------- the recognizers in a project, for the console (spec 2026-11-04-google-recognizers) ----------

@dataclass
class Listing:
    recognizers: list[dict] = field(default_factory=list)   # {name, id, project, location, model, codes, languages}
    problems: list[str] = field(default_factory=list)       # Google's refusals, in its words, for the admin
    projects: dict[str, str] = field(default_factory=dict)  # a project asked for by id -> its number, as Google names it


def canonical(name: str, listing: Listing) -> str:
    """The name as Google lists it: Google writes a recognizer's project as its number (live, 2026-11-04), so a name
    typed with the project's id is turned into the listed one."""
    parts = split_name(name)
    if parts is None:
        return name
    project, location, rid = parts
    return f"projects/{listing.projects.get(project, project)}/locations/{location}/recognizers/{rid}"


def _row(item: dict) -> dict | None:
    parts = split_name(item.get("name"))
    if parts is None or (item.get("state") or "ACTIVE") != "ACTIVE":
        return None
    config = item.get("defaultRecognitionConfig") or {}
    codes = [str(c) for c in (item.get("languageCodes") or config.get("languageCodes") or []) if c]
    return {"name": item["name"], "id": parts[2], "project": parts[0], "location": parts[1],
            "model": str(item.get("model") or config.get("model") or ""), "codes": codes,
            "languages": list(dict.fromkeys(_primary(c) for c in codes if c != "auto"))}


async def _pages(client: httpx.AsyncClient, url: str, headers: dict, key: str) -> list[dict]:
    """Every item of a paged Speech v2 list. A refusal raises _Refused with Google's own words."""
    out, token = [], None
    while True:
        r = await client.get(url, headers=headers, params={"pageToken": token} if token else None)
        if r.status_code != 200:
            raise _Refused(f"{r.status_code}: {GoogleSpeech._message(r)}")
        body = r.json()
        out += body.get(key) or []
        token = body.get("nextPageToken")
        if not token:
            return out


class _Refused(Exception):
    pass


async def list_recognizers(projects: Iterable[str] | None, *, transport: httpx.AsyncBaseTransport | None = None,
                           token: Callable[[], Awaitable[tuple[str, str | None]]] = _default_token,
                           timeout_s: float = 15.0) -> Listing:
    """Every ACTIVE recognizer in the key's own project and in `projects` (those of recognizers already named), in
    every location Google lists, every page of each. Never raises for Google's answer: a refusal is a problem line naming where, in Google's
    words (which carry no credential). Read-only."""
    listing = Listing()
    try:
        bearer, own = await token()
    except google_cloud.GoogleAuthError as e:
        listing.problems.append(str(e))
        return listing
    wanted = [p for p in dict.fromkeys([own, *(projects or ())]) if p]
    if not wanted:
        listing.problems.append("Google didn't say which project the key belongs to: set GOOGLE_CLOUD_PROJECT.")
        return listing
    headers = {"Authorization": f"Bearer {bearer}"}
    limit = asyncio.Semaphore(LIST_CONCURRENCY)
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, connect=10.0), transport=transport) as client:

        async def one(project: str, location: str) -> list[dict]:
            async with limit:
                try:
                    items = await _pages(client, f"{endpoint(location)}/v2/projects/{project}/locations/{location}"
                                                 "/recognizers", headers, "recognizers")
                except _Refused as e:
                    listing.problems.append(f"Google refused to list recognizers in {project}, {location}: {e}")
                    return []
                except (httpx.HTTPError, ValueError) as e:
                    logger.warning("Listing Google recognizers in %s failed: %s", location, type(e).__name__)
                    listing.problems.append(f"Google didn't answer for {project}, {location}")
                    return []
            return [row for row in map(_row, items) if row]

        seen: set[str] = set()   # projects listed, by the id asked for and by the number Google answers with
        for project in wanted:
            if project in seen:
                continue
            try:
                locations = await _pages(client, f"{endpoint('global')}/v2/projects/{project}/locations", headers,
                                         "locations")
            except _Refused as e:
                listing.problems.append(f"Google refused to list the locations of {project}: {e}")
                continue
            except (httpx.HTTPError, ValueError) as e:
                logger.warning("Listing Google speech locations failed: %s", type(e).__name__)
                listing.problems.append(f"Google didn't answer for {project}")
                continue
            seen.add(project)
            number = next((str(loc.get("name", "")).split("/")[1] for loc in locations
                           if str(loc.get("name", "")).startswith("projects/")), project)
            if number != project:
                listing.projects[project] = number
                if number in seen:   # the same project, already listed by its number
                    continue
                seen.add(number)
            ids = [str(loc.get("locationId")) for loc in locations if PART_RE.fullmatch(str(loc.get("locationId")))]
            for rows in await asyncio.gather(*(one(project, loc) for loc in ids)):
                listing.recognizers += [r for r in rows if r["name"] not in {x["name"] for x in listing.recognizers}]
    return listing

