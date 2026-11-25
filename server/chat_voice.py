"""Who speaks the chat's replies and Talk (spec 2026-11-03-google-speech): Kokoro on this server, Google Gemini-TTS,
or Google's Chirp 3 HD voices (spec 2026-11-18-chirp-voice), both through Cloud Text-to-Speech. Documents,
audiobooks and the extension never come here: they keep /v1/synthesize and Kokoro.

Which one applies, first match wins: the admin's choice (`chat_options.chat_voice`), while it can still run here;
CHAT_VOICE (kokoro|google|chirp); Kokoro. Gemini-TTS needs GOOGLE_TTS_MODEL, so it is off until a deployer sets it;
Chirp needs Google set up (GOOGLE_APPLICATION_CREDENTIALS, or GOOGLE_TTS_MODEL), and has no model to name.

With Google, everyone hears the admin's voice (default GOOGLE_TTS_VOICE, else Kore) unless they picked their own,
and each sentence's language comes from its script: mostly Sinhala letters is si-LK, mostly Tamil is ta-IN, anything
else GOOGLE_TTS_LANGUAGE (en-US). The two engines share their 30 voice names: Chirp's are `<language>-Chirp3-HD-<name>`.
Chirp has no Sinhala voice (Google listed none, 2026-11-18), so with Chirp a Sinhala sentence is read by Gemini-TTS
when it is set up, and refused in words when not; text mixing languages is spoken one language at a time.
The token is only ever put in a request header: it is not logged and not in exception messages."""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

import httpx
import numpy as np
import soundfile as sf

from . import google_cloud

logger = logging.getLogger(__name__)

ENGINES = ("kokoro", "google", "chirp")
GOOGLE_ENGINES = ("google", "chirp")   # the engines that speak with GOOGLE_VOICES
LABELS = {"kokoro": "Kokoro on this server", "google": "Google Gemini-TTS", "chirp": "Google Chirp 3 HD"}
# Gemini-TTS's voices (Cloud Text-to-Speech, checked 2026-11-03), and Chirp 3 HD's names (checked 2026-11-18).
GOOGLE_VOICES = (
    "Achernar", "Achird", "Algenib", "Algieba", "Alnilam", "Aoede", "Autonoe", "Callirrhoe", "Charon", "Despina",
    "Enceladus", "Erinome", "Fenrir", "Gacrux", "Iapetus", "Kore", "Laomedeia", "Leda", "Orus", "Pulcherrima",
    "Puck", "Rasalgethi", "Sadachbia", "Sadaltager", "Schedar", "Sulafat", "Umbriel", "Vindemiatrix", "Zephyr",
    "Zubenelgenubi",
)
DEFAULT_VOICE = "Kore"
# The English accents both Google voices take (Chirp 3 HD lists 30 voices in each; Gemini-TTS accepted each code;
# live 2026-11-18). The admin picks the default, each person may pick their own; Sinhala and Tamil are unaffected.
ENGLISH_ACCENTS = (("en-US", "American English"), ("en-GB", "British English"), ("en-IN", "Indian English"),
                   ("en-AU", "Australian English"))
ACCENT_CODES = frozenset(code for code, _ in ENGLISH_ACCENTS)
MAX_TEXT_BYTES = 4000   # Google's limit on one call's text
ENDPOINT = "https://texttospeech.googleapis.com/v1/text:synthesize"

_DIDNT_ANSWER = "Google's voice didn't answer"
_REFUSED = "Google refused this server's key"
_FORBIDDEN = "The service account can't use Gemini-TTS: give it Vertex AI User on its project"
_FORBIDDEN_CHIRP = ("Google refused the service account for Chirp 3 HD: check that Cloud Text-to-Speech is on in "
                    "its project and that the account may use it")
_QUOTA = "Google's quota for voices is used up for now"
_ASK_ADMIN = "Google's voice isn't working right now. Ask your admin."   # what a person sees for a set-up problem
_NO_SINHALA = "Google's Chirp voices don't speak Sinhala, and no other Google voice is set up for it. Ask your admin."
_NO_SINHALA_DETAIL = ("Chirp 3 HD has no Sinhala voice: set GOOGLE_TTS_MODEL (such as "
                      "gemini-2.5-flash-lite-preview-tts) so Gemini-TTS reads the Sinhala sentences")
GEMINI, CHIRP = "Gemini-TTS", "Chirp 3 HD"   # what the log calls each


@dataclass(frozen=True)
class ChatVoice:
    engine: str        # one of ENGINES
    voice: str | None  # Google's voice; None for Kokoro, which reads with each person's own
    source: str        # "admin" | "env" | "default"


class GoogleVoiceError(Exception):
    """Google didn't speak. `status` is what the route answers; the message is for the person, `detail` for the
    admin (the log, and the Overview for credentials). Neither carries a credential."""

    def __init__(self, status: int, message: str, detail: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail or message
        logger.warning("Google's voice didn't speak: %s", self.detail)


def needs(engine: str, env: Mapping[str, str] = os.environ) -> str:
    """What an engine still needs from the environment before it can speak; empty when nothing."""
    gemini = (env.get("GOOGLE_TTS_MODEL") or "").strip()
    if engine == "google" and not gemini:
        return "GOOGLE_TTS_MODEL"
    if engine == "chirp" and not (gemini or (env.get("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()):
        return "GOOGLE_APPLICATION_CREDENTIALS"
    return ""


def engines(env: Mapping[str, str] = os.environ) -> list[dict]:
    return [{"id": e, "label": LABELS[e], "available": not needs(e, env), "needs": needs(e, env)} for e in ENGINES]


def default_voice(env: Mapping[str, str] = os.environ) -> str:
    wanted = (env.get("GOOGLE_TTS_VOICE") or "").strip()
    return wanted if wanted in GOOGLE_VOICES else DEFAULT_VOICE


def current(options, env: Mapping[str, str] = os.environ) -> ChatVoice:
    choice = getattr(options, "chat_voice", None) or {}
    engine = choice.get("engine")
    if engine in ENGINES and not needs(engine, env):
        voice = choice.get("voice")
        return ChatVoice(engine, (voice if voice in GOOGLE_VOICES else default_voice(env))
                         if engine in GOOGLE_ENGINES else None, "admin")
    raw = (env.get("CHAT_VOICE") or "").strip().lower()
    if raw in ENGINES and not needs(raw, env):
        return ChatVoice(raw, default_voice(env) if raw in GOOGLE_ENGINES else None, "env")
    return ChatVoice("kokoro", None, "default")


def accents() -> list[dict]:
    return [{"code": code, "label": label} for code, label in ENGLISH_ACCENTS]


def accent_label(code: str) -> str:
    return dict(ENGLISH_ACCENTS).get(code, code)


def current_accent(options, env: Mapping[str, str] = os.environ) -> str:
    """The English accent everyone hears unless they pick their own: the admin's, then GOOGLE_TTS_LANGUAGE (taken
    as it is, as before), then American."""
    chosen = (getattr(options, "chat_voice", None) or {}).get("accent")
    if chosen in ACCENT_CODES:
        return chosen
    return (env.get("GOOGLE_TTS_LANGUAGE") or "").strip() or "en-US"


def language_of(text: str, env: Mapping[str, str] = os.environ, english: str | None = None) -> str:
    """The language Google is told, from the letters: whichever script has the most of them. Anything else is
    English, in `english` (the accent) when given, else GOOGLE_TTS_LANGUAGE, else en-US."""
    sinhala = sum(1 for ch in text if "඀" <= ch <= "෿")
    tamil = sum(1 for ch in text if "஀" <= ch <= "௿")
    other = sum(1 for ch in text if ch.isalpha() and not ("඀" <= ch <= "෿" or "஀" <= ch <= "௿"))
    if sinhala and sinhala >= tamil and sinhala > other:
        return "si-LK"
    if tamil and tamil > sinhala and tamil > other:
        return "ta-IN"
    return english or (env.get("GOOGLE_TTS_LANGUAGE") or "").strip() or "en-US"


def _timeout(env: Mapping[str, str]) -> httpx.Timeout:
    try:
        seconds = max(1.0, float(env.get("GOOGLE_TIMEOUT_S") or 30))
    except ValueError:
        seconds = 30.0
    return httpx.Timeout(seconds, connect=10.0)


async def _default_token() -> tuple[str, str | None]:
    return await google_cloud.access_token()


async def google_speak(text: str, voice: str, speed: float, env: Mapping[str, str] = os.environ, *,
                       accent: str | None = None, sample_rate_hz: int | None = None,
                       transport: httpx.AsyncBaseTransport | None = None,
                       token: Callable[[], Awaitable[tuple[str, str | None]]] = _default_token,
                       ) -> tuple[bytes, float]:
    """One sentence, spoken by Gemini-TTS: (WAV bytes, seconds). `sample_rate_hz` asks Google for that rate (the
    phone lines ask for 8000); left out, the request is the chat's and Talk's, unchanged."""
    body = {"input": {"text": text},
            "voice": {"languageCode": language_of(text, env, accent), "name": voice,
                      "modelName": (env.get("GOOGLE_TTS_MODEL") or "").strip()},
            "audioConfig": _audio_config(speed, sample_rate_hz)}
    return await _synthesize(body, env, transport, token, GEMINI)


async def chirp_speak(text: str, voice: str, speed: float, env: Mapping[str, str] = os.environ, *,
                      accent: str | None = None, sample_rate_hz: int | None = None,
                      transport: httpx.AsyncBaseTransport | None = None,
                      token: Callable[[], Awaitable[tuple[str, str | None]]] = _default_token,
                      ) -> tuple[bytes, float]:
    """One piece of a reply, spoken by Chirp 3 HD: (WAV bytes, seconds). Each run of sentences in one language is
    one request, in its language's voice, all asked for at once (Gemini-TTS's Sinhala takes seconds) and joined in
    order; a Sinhala run goes to Gemini-TTS (the same voice name), and when that isn't set up nothing is sent."""
    runs = language_runs(text, env, accent)
    if any(language == "si-LK" for language, _ in runs) and needs("google", env):
        raise GoogleVoiceError(503, _NO_SINHALA, _NO_SINHALA_DETAIL)

    async def run(language: str, part: str) -> tuple[bytes, float]:
        if language == "si-LK":
            return await google_speak(part, voice, speed, env, accent=accent, sample_rate_hz=sample_rate_hz,
                                      transport=transport, token=token)
        body = {"input": {"text": part}, "voice": {"languageCode": language, "name": f"{language}-Chirp3-HD-{voice}"},
                "audioConfig": _audio_config(speed, sample_rate_hz)}
        return await _synthesize(body, env, transport, token, CHIRP)
    if len(runs) == 1:
        return await run(*runs[0])
    tasks = [asyncio.ensure_future(run(language, part)) for language, part in runs]
    try:
        pieces = await asyncio.gather(*tasks)
    except BaseException:   # one failed (or the request was cancelled): the others' audio is no use
        for task in tasks:
            task.cancel()
        raise
    return _joined(list(pieces))


_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def language_runs(text: str, env: Mapping[str, str] = os.environ, english: str | None = None
                  ) -> list[tuple[str, str]]:
    """The text as runs of whole sentences in one language each, in order: [(language, text)]. A sentence with no
    letters (a number, a dash) joins the run before it, or the first one after it."""
    runs: list[tuple[str, list[str]]] = []
    waiting: list[str] = []   # letterless sentences before the first run
    for sentence in (s for s in _SENTENCE_END.split(text.strip()) if s):
        if not any(ch.isalpha() for ch in sentence):
            (runs[-1][1] if runs else waiting).append(sentence)
            continue
        language = language_of(sentence, env, english)
        if runs and runs[-1][0] == language:
            runs[-1][1].append(sentence)
        else:
            runs.append((language, [*waiting, sentence]))
            waiting = []
    if not runs:
        return [(language_of(text, env, english), text)]
    return [(language, " ".join(sentences)) for language, sentences in runs]


def _audio_config(speed: float, sample_rate_hz: int | None = None) -> dict:
    config: dict = {"audioEncoding": "LINEAR16"}
    if sample_rate_hz:
        config["sampleRateHertz"] = sample_rate_hz
    if speed and abs(speed - 1.0) > 1e-6:
        config["speakingRate"] = speed
    return config


async def _synthesize(body: dict, env: Mapping[str, str], transport: httpx.AsyncBaseTransport | None,
                      token: Callable[[], Awaitable[tuple[str, str | None]]], name: str) -> tuple[bytes, float]:
    """One text:synthesize call: (WAV bytes, seconds). `name` is the engine, for the log and the admin's advice."""
    try:
        bearer, project = await token()
    except google_cloud.GoogleAuthError as e:
        raise GoogleVoiceError(503, _ASK_ADMIN, str(e)) from None
    headers = {"Authorization": f"Bearer {bearer}"}
    if project:
        headers["x-goog-user-project"] = project
    try:
        if transport is not None:   # tests: a client of its own
            async with httpx.AsyncClient(timeout=_timeout(env), transport=transport) as c:
                r = await c.post(ENDPOINT, headers=headers, json=body)
        else:                       # Google's kept-open client: no new connection per sentence
            r = await google_cloud.http().post(ENDPOINT, headers=headers, json=body, timeout=_timeout(env))
    except httpx.HTTPError as e:
        logger.warning("%s request failed: %s", name, type(e).__name__)
        raise GoogleVoiceError(504, _DIDNT_ANSWER) from None
    if r.status_code != 200:
        logger.warning("%s answered %s", name, r.status_code)
        raise _refusal(r, _FORBIDDEN_CHIRP if name == CHIRP else _FORBIDDEN)
    try:
        wav = base64.b64decode(r.json()["audioContent"])
        seconds = sf.info(io.BytesIO(wav)).duration
    except (ValueError, KeyError, TypeError, RuntimeError):
        raise GoogleVoiceError(502, "Google sent audio this server can't read") from None
    return wav, seconds


def _joined(pieces: list[tuple[bytes, float]]) -> tuple[bytes, float]:
    """Several WAVs as one, at the first one's rate (Google's are 24 kHz; another is stretched to it)."""
    rate, parts = None, []
    try:
        for wav, _ in pieces:
            audio, r = sf.read(io.BytesIO(wav), dtype="float32", always_2d=True)
            audio = audio.mean(axis=1)
            if rate is None:
                rate = r
            elif r != rate and len(audio):
                n = max(1, int(round(len(audio) * rate / r)))
                audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
            parts.append(audio)
    except (RuntimeError, ValueError):
        raise GoogleVoiceError(502, "Google sent audio this server can't read") from None
    joined = np.concatenate(parts)
    out = io.BytesIO()
    sf.write(out, joined, rate, format="WAV", subtype="PCM_16")
    return out.getvalue(), len(joined) / rate


def _refusal(r: httpx.Response, forbidden: str = _FORBIDDEN) -> GoogleVoiceError:
    if r.status_code == 400:
        try:
            message = str(r.json().get("error", {}).get("message") or "")
        except ValueError:
            message = ""
        return GoogleVoiceError(422, f"Google refused the sentence: {(message or 'status 400')[:300]}")
    if r.status_code == 401:
        return GoogleVoiceError(503, _ASK_ADMIN, _REFUSED)
    if r.status_code == 403:
        return GoogleVoiceError(503, _ASK_ADMIN, google_cloud.service_off(r) or forbidden)
    if r.status_code == 429:
        return GoogleVoiceError(503, _QUOTA)
    return GoogleVoiceError(502, _DIDNT_ANSWER)
