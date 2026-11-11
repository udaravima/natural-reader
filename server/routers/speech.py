"""Speech-to-text for the chat message box (spec §4.2): POST a recording, get the text back.

The recording lives in memory for the length of the request: it is never written to disk, the database or a log,
and neither is the transcript (the per-transcription log line in speech/service.py carries metadata only).

Starlette's default form parsing would spool anything over 1 MB into a temp file before the route runs, so the
POST route parses the multipart body itself (MemoryMultipartParser): the stream is capped as it is read, and the
parser's spool is larger than any body the cap lets through, so it can't roll over to disk.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Callable

from fastapi import APIRouter, Depends, Request
from python_multipart.exceptions import FormParserError
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import ClientDisconnect

from ..auth import deps
from ..db import get_pool
from ..http_errors import refusal
from ..services import chat_options
from ..speech import catalogue, service, window
from ..speech.base import (AudioTooLong, BadAudio, EngineFailed, EngineNotReady, EngineUnavailable, MAX_AUDIO_BYTES)
from ..speech.languages import WHISPER_LANGUAGES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/speech", tags=["speech"])

STATUS_TIMEOUT_S = 5.0   # bounds the engine's probe (a live GET for a remote engine); Overview's budget too
# The largest body the POST route reads: the recording plus room for the multipart framing and the language field.
MAX_BODY_BYTES = MAX_AUDIO_BYTES + 64 * 1024
_BAD_AUDIO = "That recording couldn't be read."
_TOO_LARGE = "The recording is over 10 MB."

MAX_SECONDS = 120   # what the browser records; the server allows a little more (base.MAX_AUDIO_SECONDS)
GOOGLE_MAX_SECONDS = 55   # Google's synchronous recognize takes a minute at most: the browser stops short of it


def _languages() -> list[dict]:
    return [{"code": code, "name": name}
            for code, name in sorted(WHISPER_LANGUAGES.items(), key=lambda kv: kv[1])]


async def _options(conn) -> tuple[chat_options.ChatOptions, bool]:
    """Read per request, as turns do (one primary-key read), so a switch applies at once on every worker. If the
    row can't be read the environment's values apply, and the answer says they aren't the row's."""
    try:
        return await chat_options.load_chat_options(conn), True
    except Exception:  # noqa: BLE001 - the composer's status call must not 500 on a database blip
        logger.warning("Chat options couldn't be read: using the environment's", exc_info=True)
        return chat_options.from_env(), False


def speech_conn() -> Callable[[], AbstractAsyncContextManager]:
    """The seam for the one short connection these routes take: a callable that opens a pooled connection.
    A dependency, so a test can hand over its own."""
    return lambda: get_pool().connection()


@dataclass(frozen=True)
class SpeechAccess:
    principal: deps.Principal
    options: chat_options.ChatOptions
    from_row: bool = True   # False: `options` are the environment's, which know nothing of an admin's choice

    @property
    def voice_on(self) -> bool:
        return self.options.voice_input

    def engine(self, language: str | None = None):
        """The engine that hears `language`: the model the admin offered for it, else the deployment's model (a
        change reaches a worker here). Without the row nobody knows what was chosen, so whatever runs keeps
        running: a database blip must not reload a model."""
        if not self.from_row:
            return service.get_transcriber()
        chosen = service.chosen_engine(self.options)
        if chosen == "google":
            # The first recognizer that lists the language, else the first (spec 2026-11-04-google-recognizers).
            current = service.get_transcriber(engine="google", recognizers=self.recognizer_names)
            ticked = self.recognizers(current)
            return service.google_for(ticked, language) if ticked else current
        return service.get_transcriber(wanted=catalogue.for_language(self.options, language),
                                       engine=chosen, default=catalogue.wanted(self.options))

    @property
    def recognizer_names(self) -> tuple[str, ...]:
        return service.recognizer_names(self.options)

    def recognizers(self, current=None) -> list:
        """The Google recognizers in use, in order; empty unless Google hears speech (or when the running engine was
        put in place from outside, which is kept as it is)."""
        if not self.from_row or service.chosen_engine(self.options) != "google":
            return []
        current = current or service.get_transcriber(engine="google", recognizers=self.recognizer_names)
        if getattr(current, "recognizer", None) is None or not self.recognizer_names:
            return []
        return service.google_engines(self.recognizer_names)

    async def learn_languages(self) -> list:
        """Read each recognizer's languages (cached by each engine, STATUS_TTL_S) so a clip is routed by them, even
        on a worker that never answered a status request. A recognizer that can't answer lists nothing."""
        ticked = self.recognizers()
        await asyncio.gather(*(asyncio.wait_for(r.status(), STATUS_TIMEOUT_S) for r in ticked), return_exceptions=True)
        return ticked


def _model_of(engine) -> str | None:
    """Which model heard a clip: a local engine's catalogue id, a Google recognizer with its model, else the remote
    engine's model name."""
    spec = getattr(engine, "spec", None)
    if spec is not None:
        return spec.id
    return getattr(engine, "label", None) or getattr(engine, "model", None) or None


async def speech_access(request: Request, open_conn=Depends(speech_conn)) -> SpeechAccess:
    """Who is calling, whether they may use chat, and whether voice is on: three reads on ONE short pooled
    connection that is released BEFORE the route reads the upload or calls the engine. (get_current_user holds
    its connection until the route returns, and a transcription can take minutes: ten of those would starve
    every other request on the worker, pool size 10.) The same auth functions as everywhere, the same 401, 403
    (no chat capability, or a token without the chat scope) and 404 (voice off, in the route)."""
    async with open_conn() as conn:
        principal = await deps.resolve_principal(request, conn)
        deps.check_capability(principal, "chat")
        options, from_row = await _options(conn)
    return SpeechAccess(principal, options, from_row)


@router.get("/status")
async def speech_status(access: SpeechAccess = Depends(speech_access)):
    """What the composer needs to draw the microphone: on or off, ready or not, and the languages, with the ones
    the model is made for (`model_languages`, empty for every language). When voice is off no engine is built,
    probed or warmed. Whatever the engine says, this answers 200."""
    base = {"max_seconds": MAX_SECONDS, "languages": _languages(), "window": window.settings(), "auto_hears": None,
            "hears": [], "default_model": None}
    if not access.voice_on:
        return {"enabled": False, "ready": False, "state": "off", **base}
    engine = access.engine()
    # Who hears the person (Talk says so when it isn't this server), and how long one recording may be.
    base["heard_by"] = getattr(engine, "where", "this server")
    base["engine"] = engine.name   # the Speaking switch lists the recognizer's languages for Google
    # The languages the admin offered a model for, each with its model, and the model that hears the rest. Read from
    # the list and the disk: nothing but the default is loaded here, on every page load.
    if engine.name == "local" and access.from_row:
        base["hears"] = catalogue.hears(access.options)
        default = catalogue.wanted(access.options)
        base["default_model"] = {"id": default.id, "label": default.label}
    ticked = []
    if engine.name == "google":
        base["max_seconds"] = GOOGLE_MAX_SECONDS
        ticked = await access.learn_languages()   # every recognizer in use, read at once (each cached)
    try:
        status = await asyncio.wait_for(engine.status(), STATUS_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001 - a config mistake, a server that's down or one that hangs is a state, not a 500
        logger.warning("The speech engine's status failed: %s", type(e).__name__)
        status = None
    # After the check: a remote engine learns its languages from it (Google reads its recognizer). With several
    # Google recognizers, each language is heard by the first that lists it.
    heard = _google_hears(ticked) if ticked else []
    languages = tuple(h["code"] for h in heard) if ticked else tuple(getattr(engine, "languages", ()))
    if ticked:
        base["hears"] = heard
        base["default_model"] = {"id": ticked[0].id, "label": ticked[0].label}
    base["model_languages"] = [WHISPER_LANGUAGES[code] for code in languages if code in WHISPER_LANGUAGES]
    # Google refuses a language its recognizers don't list, so only those are offered; and a recognizer that
    # can't detect the language hears one for Auto, which Settings names.
    if engine.name == "google" and languages:
        base["languages"] = [lang for lang in base["languages"] if lang["code"] in languages]
    hears = getattr(engine, "auto_hears", None)
    base["auto_hears"] = WHISPER_LANGUAGES.get(hears) if hears else None
    if status is None:
        return {"enabled": True, "ready": False, "state": "error", **base}
    service.heal(engine, status)
    return {"enabled": True, "ready": bool(status.ready), "state": status.state, **base}


def _google_hears(ticked) -> list[dict]:
    """Each language a ticked recognizer lists, once, with the first recognizer that lists it."""
    out, done = [], set()
    for recognizer in ticked:
        for code in getattr(recognizer, "languages", ()):
            if code not in done and code in WHISPER_LANGUAGES:
                done.add(code)
                out.append({"code": code, "name": WHISPER_LANGUAGES[code], "model": recognizer.id,
                            "label": recognizer.label})
    return out


class MemoryMultipartParser(MultiPartParser):
    """Starlette's parser with a spool larger than any body the route accepts, plus one file and a few small fields."""
    spool_max_size = MAX_BODY_BYTES


async def _capped(request: Request):
    """The request body, refused with 413 the moment the bytes actually read pass MAX_BODY_BYTES (Content-Length
    can be absent or false)."""
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            raise refusal(413, "too_large", _TOO_LARGE)
        yield chunk


async def _read_upload(request: Request) -> tuple[str | None, UploadFile | None]:
    """Parse the multipart body in memory: (language field, file part). Any parse failure is 400."""
    ctype = request.headers.get("content-type", "")
    if not ctype.lower().startswith("multipart/form-data"):
        raise refusal(400, "bad_audio", _BAD_AUDIO)
    parser = MemoryMultipartParser(request.headers, _capped(request), max_files=1, max_fields=4,
                                   max_part_size=4096)
    try:
        form = await parser.parse()
    except (MultiPartException, FormParserError):   # Starlette 1.0 let the parser's own error through
        raise refusal(400, "bad_audio", _BAD_AUDIO) from None
    except ClientDisconnect:
        # A closed tab mid-upload is a normal event, not a crash: one debug line, no traceback. The client has
        # gone, so the status is never seen. (The parser closes the part it was filling as it unwinds.)
        logger.debug("The client disconnected during a recording upload")
        raise refusal(400, "bad_audio", _BAD_AUDIO) from None
    language = form.get("language")
    file = form.get("file")
    for _, value in form.multi_items():   # close every upload on every path, the one we use included, in the caller
        if isinstance(value, UploadFile) and value is not file:
            await value.close()
    return (language if isinstance(language, str) else None), (file if isinstance(file, UploadFile) else None)


@router.post("/transcriptions")
async def transcribe(request: Request, access: SpeechAccess = Depends(speech_access)):
    # Order matters: nothing is read for a request that can't succeed. Capability (the dependency), voice off, a
    # declared length over the cap; then the body is parsed in memory with a running cap (`language` lives in it,
    # so it can only be checked after), and last the file itself.
    if not access.voice_on:
        raise refusal(404, "voice_off", "Voice input is off.")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise refusal(413, "too_large", _TOO_LARGE)
    language, file = await _read_upload(request)
    try:
        code = None if language in (None, "", "auto") else language
        if code is not None and code not in WHISPER_LANGUAGES:
            raise refusal(422, "bad_language", "That language isn't one Voice input knows.")
        if file is None:
            raise refusal(400, "bad_audio", _BAD_AUDIO)
        audio = await file.read(MAX_AUDIO_BYTES + 1)
        mime = file.content_type or ""
    finally:
        if file is not None:
            await file.close()
    if len(audio) > MAX_AUDIO_BYTES:
        raise refusal(413, "too_large", _TOO_LARGE)
    if not audio:
        raise refusal(400, "bad_audio", _BAD_AUDIO)

    # The model for this clip's language, held to the end of its transcription (speech/service.py): nothing another
    # request does meanwhile can swap it.
    if code is not None:
        await access.learn_languages()   # Google: route by each recognizer's languages; nothing for the others
    engine = access.engine(code)
    if engine.name == "local":   # the local engine's state costs nothing to read; a remote one's is a request per recording
        try:
            service.heal(engine, await asyncio.wait_for(engine.status(), STATUS_TIMEOUT_S))
        except Exception as e:  # noqa: BLE001 - the transcription below reports a broken engine in its own words
            logger.debug("The speech engine's status check before transcribing failed: %s", type(e).__name__)
    try:
        try:
            result = await service.transcribe_for(str(access.principal.user_id), audio, mime, code, engine=engine)
        except EngineUnavailable:
            fallback = access.engine(None)
            if fallback is engine:
                raise
            # The language's own model can't load here: the default hears the clip, in the language asked for.
            logger.warning("speech model %r couldn't hear a clip; the default heard it", _model_of(engine))
            engine = fallback
            result = await service.transcribe_for(str(access.principal.user_id), audio, mime, code, engine=engine)
    except AudioTooLong:
        limit = "a minute" if engine.name == "google" else "2 minutes"
        raise refusal(413, "too_long", f"The recording is over {limit}.") from None
    except BadAudio:
        raise refusal(400, "bad_audio", "That recording couldn't be read.") from None
    except service.AlreadyRunning:
        raise refusal(409, "already_running", "A recording of yours is already being transcribed.") from None
    except service.Busy:
        raise refusal(503, "busy", "Voice input is busy. Try again in a moment.") from None
    except EngineUnavailable:
        raise refusal(503, "unavailable", "Voice input isn't working right now. Ask your admin.") from None
    except EngineNotReady:
        raise refusal(503, "not_ready",
                      "Voice input is still getting ready, try again in a minute") from None
    except EngineFailed as e:
        raise refusal(503, "engine_failed", str(e) or "Voice input failed.") from None
    return {"text": result.text, "language": result.language, "duration_s": result.duration_s,
            "no_speech": result.no_speech, "avg_logprob": result.avg_logprob, "model": _model_of(engine)}
