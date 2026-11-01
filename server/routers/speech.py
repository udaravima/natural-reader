"""Speech-to-text for the chat message box (spec §4.2): POST a recording, get the text back.

The recording lives in memory for the length of the request: it is never written to disk, the database or a log,
and neither is the transcript (the per-transcription log line in speech/service.py carries metadata only).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, UploadFile

from ..auth import deps
from ..http_errors import refusal
from ..services import chat_options
from ..speech import service
from ..speech.base import (AudioTooLong, BadAudio, EngineFailed, EngineNotReady, EngineStatus, MAX_AUDIO_BYTES)
from ..speech.languages import WHISPER_LANGUAGES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/speech", tags=["speech"])

MAX_SECONDS = 120   # what the browser records; the server allows a little more (base.MAX_AUDIO_SECONDS)


def _languages() -> list[dict]:
    return [{"code": code, "name": name}
            for code, name in sorted(WHISPER_LANGUAGES.items(), key=lambda kv: kv[1])]


async def _voice_on(conn) -> bool:
    """Read per request, as turns do (one primary-key read), so a switch applies at once on every worker. If the
    row can't be read the environment's value applies."""
    try:
        return (await chat_options.load_chat_options(conn)).voice_input
    except Exception:  # noqa: BLE001 - the composer's status call must not 500 on a database blip
        logger.warning("Chat options couldn't be read: using CHAT_VOICE_INPUT, else off", exc_info=True)
        return chat_options.from_env().voice_input


def _warm_if_loading(engine, status: EngineStatus) -> None:
    """Every worker process has its own engine and an admin's switch reaches only one of them, so a worker that
    never loaded starts loading on first use. warm() is idempotent while loading. A failed load (`error`) is
    not retried here: that is the admin's choice, made by switching Voice input on."""
    if status.state == "loading":
        try:
            engine.warm()
        except Exception:  # noqa: BLE001 - starting a load must not fail the request that noticed
            logger.warning("The speech engine couldn't start loading", exc_info=True)


@router.get("/status")
async def speech_status(_: deps.Principal = Depends(deps.require_capability("chat")),
                        conn=Depends(deps.get_conn, scope="function")):
    """What the composer needs to draw the microphone: on or off, ready or not, and the languages. When voice is
    off no engine is built, probed or warmed. Whatever the engine says, this answers 200."""
    base = {"max_seconds": MAX_SECONDS, "languages": _languages()}
    if not await _voice_on(conn):
        return {"enabled": False, "ready": False, "state": "off", **base}
    engine = service.get_transcriber()
    try:
        status = await engine.status()
    except Exception as e:  # noqa: BLE001 - a config mistake or a server that's down is a state, not a 500
        logger.warning("The speech engine's status failed: %s", type(e).__name__)
        return {"enabled": True, "ready": False, "state": "error", **base}
    _warm_if_loading(engine, status)
    return {"enabled": True, "ready": bool(status.ready), "state": status.state, **base}


@router.post("/transcriptions")
async def transcribe(file: UploadFile | None = File(None), language: str | None = Form(None),
                     principal: deps.Principal = Depends(deps.require_capability("chat")),
                     conn=Depends(deps.get_conn, scope="function")):
    # Order matters: nothing is read for a request that can't succeed. Capability (the dependency), voice off,
    # the language, then the upload.
    if not await _voice_on(conn):
        raise refusal(404, "voice_off", "Voice input is off.")
    code = None if language in (None, "", "auto") else language
    if code is not None and code not in WHISPER_LANGUAGES:
        raise refusal(422, "bad_language", "That language isn't one Voice input knows.")
    if file is None:
        raise refusal(400, "bad_audio", "That recording couldn't be read.")
    audio = await file.read(MAX_AUDIO_BYTES + 1)
    if len(audio) > MAX_AUDIO_BYTES:
        raise refusal(413, "too_large", "The recording is over 10 MB.")
    if not audio:
        raise refusal(400, "bad_audio", "That recording couldn't be read.")

    engine = service.get_transcriber()
    try:
        _warm_if_loading(engine, await engine.status())
    except Exception:  # noqa: BLE001 - the transcription below reports a broken engine in its own words
        pass
    try:
        result = await service.transcribe_for(str(principal.user_id), audio, file.content_type or "", code)
    except AudioTooLong:
        raise refusal(413, "too_long", "The recording is over 2 minutes.") from None
    except BadAudio:
        raise refusal(400, "bad_audio", "That recording couldn't be read.") from None
    except service.AlreadyRunning:
        raise refusal(409, "already_running", "A recording of yours is already being transcribed.") from None
    except service.Busy:
        raise refusal(503, "busy", "Voice input is busy. Try again in a moment.") from None
    except EngineNotReady:
        raise refusal(503, "not_ready",
                      "Voice input is still getting ready, try again in a minute") from None
    except EngineFailed as e:
        raise refusal(503, "engine_failed", str(e) or "Voice input failed.") from None
    return {"text": result.text, "language": result.language, "duration_s": result.duration_s}
