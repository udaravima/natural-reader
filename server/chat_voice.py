"""Who speaks the chat's replies and Talk (spec 2026-11-03-google-speech): Kokoro on this server, or Google
Gemini-TTS through Cloud Text-to-Speech. Documents, audiobooks and the extension never come here: they keep
/v1/synthesize and Kokoro.

Which one applies, first match wins: the admin's choice (`chat_options.chat_voice`), while it can still run here;
CHAT_VOICE (kokoro|google); Kokoro. Google needs GOOGLE_TTS_MODEL, so it is off until a deployer sets it.

With Google, everyone hears the admin's voice (default GOOGLE_TTS_VOICE, else Kore), and each sentence's language
comes from its script: mostly Sinhala letters is si-LK, mostly Tamil is ta-IN, anything else GOOGLE_TTS_LANGUAGE
(en-US). The token is only ever put in a request header: it is not logged and not in exception messages."""
from __future__ import annotations

import base64
import io
import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

import httpx
import soundfile as sf

from . import google_cloud

logger = logging.getLogger(__name__)

ENGINES = ("kokoro", "google")
LABELS = {"kokoro": "Kokoro on this server", "google": "Google Gemini-TTS"}
# Gemini-TTS's voices (Cloud Text-to-Speech, checked 2026-11-03). Each speaks every language the model does.
GOOGLE_VOICES = (
    "Achernar", "Achird", "Algenib", "Algieba", "Alnilam", "Aoede", "Autonoe", "Callirrhoe", "Charon", "Despina",
    "Enceladus", "Erinome", "Fenrir", "Gacrux", "Iapetus", "Kore", "Laomedeia", "Leda", "Orus", "Pulcherrima",
    "Puck", "Rasalgethi", "Sadachbia", "Sadaltager", "Schedar", "Sulafat", "Umbriel", "Vindemiatrix", "Zephyr",
    "Zubenelgenubi",
)
DEFAULT_VOICE = "Kore"
MAX_TEXT_BYTES = 4000   # Google's limit on one call's text
ENDPOINT = "https://texttospeech.googleapis.com/v1/text:synthesize"

_DIDNT_ANSWER = "Google's voice didn't answer"
_REFUSED = "Google refused this server's key"
_FORBIDDEN = "The service account can't use Gemini-TTS: give it Vertex AI User on its project"
_QUOTA = "Google's quota for voices is used up for now"
_ASK_ADMIN = "Google's voice isn't working right now. Ask your admin."   # what a person sees for a set-up problem


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
    if engine == "google" and not (env.get("GOOGLE_TTS_MODEL") or "").strip():
        return "GOOGLE_TTS_MODEL"
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
                         if engine == "google" else None, "admin")
    raw = (env.get("CHAT_VOICE") or "").strip().lower()
    if raw in ENGINES and not needs(raw, env):
        return ChatVoice(raw, default_voice(env) if raw == "google" else None, "env")
    return ChatVoice("kokoro", None, "default")


def language_of(text: str, env: Mapping[str, str] = os.environ) -> str:
    """The language Google is told, from the letters: whichever script has the most of them."""
    sinhala = sum(1 for ch in text if "඀" <= ch <= "෿")
    tamil = sum(1 for ch in text if "஀" <= ch <= "௿")
    other = sum(1 for ch in text if ch.isalpha() and not ("඀" <= ch <= "෿" or "஀" <= ch <= "௿"))
    if sinhala and sinhala >= tamil and sinhala > other:
        return "si-LK"
    if tamil and tamil > sinhala and tamil > other:
        return "ta-IN"
    return (env.get("GOOGLE_TTS_LANGUAGE") or "").strip() or "en-US"


def _timeout(env: Mapping[str, str]) -> httpx.Timeout:
    try:
        seconds = max(1.0, float(env.get("GOOGLE_TIMEOUT_S") or 30))
    except ValueError:
        seconds = 30.0
    return httpx.Timeout(seconds, connect=10.0)


async def _default_token() -> tuple[str, str | None]:
    return await google_cloud.access_token()


async def google_speak(text: str, voice: str, speed: float, env: Mapping[str, str] = os.environ, *,
                       transport: httpx.AsyncBaseTransport | None = None,
                       token: Callable[[], Awaitable[tuple[str, str | None]]] = _default_token,
                       ) -> tuple[bytes, float]:
    """One sentence, spoken by Gemini-TTS: (WAV bytes, seconds)."""
    try:
        bearer, project = await token()
    except google_cloud.GoogleAuthError as e:
        raise GoogleVoiceError(503, _ASK_ADMIN, str(e)) from None
    headers = {"Authorization": f"Bearer {bearer}"}
    if project:
        headers["x-goog-user-project"] = project
    audio_config: dict = {"audioEncoding": "LINEAR16"}
    if speed and abs(speed - 1.0) > 1e-6:
        audio_config["speakingRate"] = speed
    body = {"input": {"text": text},
            "voice": {"languageCode": language_of(text, env), "name": voice,
                      "modelName": (env.get("GOOGLE_TTS_MODEL") or "").strip()},
            "audioConfig": audio_config}
    try:
        async with httpx.AsyncClient(timeout=_timeout(env), transport=transport) as c:
            r = await c.post(ENDPOINT, headers=headers, json=body)
    except httpx.HTTPError as e:
        logger.warning("Gemini-TTS request failed: %s", type(e).__name__)
        raise GoogleVoiceError(504, _DIDNT_ANSWER) from None
    if r.status_code != 200:
        logger.warning("Gemini-TTS answered %s", r.status_code)
        raise _refusal(r)
    try:
        wav = base64.b64decode(r.json()["audioContent"])
        seconds = sf.info(io.BytesIO(wav)).duration
    except (ValueError, KeyError, TypeError, RuntimeError):
        raise GoogleVoiceError(502, "Google sent audio this server can't read") from None
    return wav, seconds


def _refusal(r: httpx.Response) -> GoogleVoiceError:
    if r.status_code == 400:
        try:
            message = str(r.json().get("error", {}).get("message") or "")
        except ValueError:
            message = ""
        return GoogleVoiceError(422, f"Google refused the sentence: {(message or 'status 400')[:300]}")
    if r.status_code == 401:
        return GoogleVoiceError(503, _ASK_ADMIN, _REFUSED)
    if r.status_code == 403:
        return GoogleVoiceError(503, _ASK_ADMIN, google_cloud.service_off(r) or _FORBIDDEN)
    if r.status_code == 429:
        return GoogleVoiceError(503, _QUOTA)
    return GoogleVoiceError(502, _DIDNT_ANSWER)
