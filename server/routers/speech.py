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
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import ClientDisconnect

from ..auth import deps
from ..db import get_pool
from ..http_errors import refusal
from ..services import chat_options
from ..speech import catalogue, service
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


def _languages() -> list[dict]:
    return [{"code": code, "name": name}
            for code, name in sorted(WHISPER_LANGUAGES.items(), key=lambda kv: kv[1])]


async def _options(conn) -> chat_options.ChatOptions:
    """Read per request, as turns do (one primary-key read), so a switch applies at once on every worker. If the
    row can't be read the environment's values apply."""
    try:
        return await chat_options.load_chat_options(conn)
    except Exception:  # noqa: BLE001 - the composer's status call must not 500 on a database blip
        logger.warning("Chat options couldn't be read: using the environment's", exc_info=True)
        return chat_options.from_env()


def speech_conn() -> Callable[[], AbstractAsyncContextManager]:
    """The seam for the one short connection these routes take: a callable that opens a pooled connection.
    A dependency, so a test can hand over its own."""
    return lambda: get_pool().connection()


@dataclass(frozen=True)
class SpeechAccess:
    principal: deps.Principal
    options: chat_options.ChatOptions

    @property
    def voice_on(self) -> bool:
        return self.options.voice_input

    def engine(self):
        """This worker's engine, on the model the deployment chose (a change reaches a worker here)."""
        return service.get_transcriber(wanted=catalogue.wanted(self.options))


async def speech_access(request: Request, open_conn=Depends(speech_conn)) -> SpeechAccess:
    """Who is calling, whether they may use chat, and whether voice is on: three reads on ONE short pooled
    connection that is released BEFORE the route reads the upload or calls the engine. (get_current_user holds
    its connection until the route returns, and a transcription can take minutes: ten of those would starve
    every other request on the worker, pool size 10.) The same auth functions as everywhere, the same 401, 403
    (no chat capability, or a token without the chat scope) and 404 (voice off, in the route)."""
    async with open_conn() as conn:
        principal = await deps.resolve_principal(request, conn)
        deps.check_capability(principal, "chat")
        options = await _options(conn)
    return SpeechAccess(principal, options)


@router.get("/status")
async def speech_status(access: SpeechAccess = Depends(speech_access)):
    """What the composer needs to draw the microphone: on or off, ready or not, and the languages, with the ones
    the model is made for (`model_languages`, empty for every language). When voice is off no engine is built,
    probed or warmed. Whatever the engine says, this answers 200."""
    base = {"max_seconds": MAX_SECONDS, "languages": _languages()}
    if not access.voice_on:
        return {"enabled": False, "ready": False, "state": "off", **base}
    engine = access.engine()
    base["model_languages"] = [WHISPER_LANGUAGES[code] for code in getattr(engine, "languages", ())]
    try:
        status = await asyncio.wait_for(engine.status(), STATUS_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001 - a config mistake, a server that's down or one that hangs is a state, not a 500
        logger.warning("The speech engine's status failed: %s", type(e).__name__)
        return {"enabled": True, "ready": False, "state": "error", **base}
    service.heal(engine, status)
    return {"enabled": True, "ready": bool(status.ready), "state": status.state, **base}


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
    except MultiPartException:
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

    engine = access.engine()
    if engine.name == "local":   # the local engine's state costs nothing to read; a remote one's is a request per recording
        try:
            service.heal(engine, await asyncio.wait_for(engine.status(), STATUS_TIMEOUT_S))
        except Exception as e:  # noqa: BLE001 - the transcription below reports a broken engine in its own words
            logger.debug("The speech engine's status check before transcribing failed: %s", type(e).__name__)
    try:
        result = await service.transcribe_for(str(access.principal.user_id), audio, mime, code)
    except AudioTooLong:
        raise refusal(413, "too_long", "The recording is over 2 minutes.") from None
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
    return {"text": result.text, "language": result.language, "duration_s": result.duration_s}
