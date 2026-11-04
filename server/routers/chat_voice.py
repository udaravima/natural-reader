"""The chat's voice (spec 2026-11-03-google-speech): POST /v1/chat/voice reads one piece of a reply aloud with
whichever engine the deployment chose for the chat and Talk, and GET says which. The answer has the same shape as
/v1/synthesize, so the browser plays either the same way."""
from __future__ import annotations

import base64
import logging
import os

from fastapi import APIRouter, Depends, Request

from .. import chat_voice, endpoints
from ..auth import deps
from ..http_errors import refusal
from ..schemas import ChatVoiceRequest
from ..services import chat_options
from .speech import speech_conn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/chat", tags=["chat"])


async def voice_access(request: Request, open_conn=Depends(speech_conn)):
    """Who is calling and the chat options, on one short connection released before anything is spoken: a
    Google call can take seconds, and must not hold one of the pool's connections meanwhile."""
    async with open_conn() as conn:
        principal = await deps.resolve_principal(request, conn)
        deps.check_capability(principal, "chat")
        try:
            options = await chat_options.load_chat_options(conn)
        except Exception:  # noqa: BLE001 - a database blip reads with the environment's choice
            logger.warning("Chat options couldn't be read: using the environment's", exc_info=True)
            options = chat_options.from_env()
    return principal, options


@router.get("/voice")
async def who_speaks(access=Depends(voice_access)):
    voice = chat_voice.current(access[1])
    out = {"engine": voice.engine, "voice": voice.voice,
           "where": "Google Cloud" if voice.engine == "google" else "this server"}
    if voice.engine == "google":
        out["voices"] = list(chat_voice.GOOGLE_VOICES)   # each person picks one in Settings → Chat
    return out


@router.post("/voice")
async def speak(body: ChatVoiceRequest, http_request: Request, access=Depends(voice_access)):
    principal, options = access
    if not principal.can_read_aloud:
        raise refusal(403, "missing_capability", "Reading aloud needs read-aloud access.", capability="reader")
    voice = chat_voice.current(options)
    if voice.engine == "kokoro":
        return await endpoints.synthesize(body, http_request, principal)
    if len(body.text.encode("utf-8")) > chat_voice.MAX_TEXT_BYTES:
        raise refusal(413, "too_long", "That's more than Google's voice reads at once.")
    try:
        # The person's own Google voice when it is one of Google's; the admin's otherwise.
        chosen = body.google_voice if body.google_voice in chat_voice.GOOGLE_VOICES else voice.voice
        wav, seconds = await chat_voice.google_speak(body.text, chosen, body.speed, os.environ)
    except chat_voice.GoogleVoiceError as e:
        raise refusal(e.status, "voice_failed", str(e)) from None
    return {"audio_base64": base64.b64encode(wav).decode("ascii"), "duration_seconds": seconds}
