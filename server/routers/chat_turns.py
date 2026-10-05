"""POST /v1/chat/sessions/{id}/turns — one chat turn, streamed as SSE (spec §6, §7).

Everything that can refuse does so BEFORE the first byte, as an ordinary HTTP
refusal (§7.2): 503 no_providers, 413 too_large, 422 invalid_request /
invalid_attachment / empty_message / model_not_allowed / attachment_unsupported,
404 not_found, 429 budget_exhausted, 409 turn_in_progress. After the first
byte, failures arrive as an `error` event.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import inspect
import json
import logging
from typing import Annotated, Any, AsyncGenerator, AsyncIterator, Awaitable, Callable, Literal

from fastapi import APIRouter, Depends, Path as PathParam, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.types import Receive, Scope, Send

from ..auth.deps import Principal, require_capability
from ..chat import store
from ..chat.config import get_chat_config
from ..chat.orchestrator import TurnRequest, abandon_turn, run_turn
from ..db import get_pool, is_ready
from ..http_body import read_capped_body
from ..http_errors import refusal
from ..llm.router import get_router
from ..llm.types import Attachment, CallSettings, Capabilities
from ..services import inference_budget, inference_limits, model_router

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/chat/sessions", tags=["chat-turns"])

SessionId = Annotated[str, PathParam(pattern=r"^[A-Za-z0-9._-]{1,128}$")]
NOT_FOUND = "This chat doesn't exist or you don't have access."
INTERNAL_ERROR_MESSAGE = "Something went wrong on the server."
# Seconds of silence (a tool running, a long prompt evaluation before the
# first token) after which an SSE comment frame is sent, so a proxy's idle
# timeout (nginx proxy_read_timeout 60 s, Cloudflare 100 s) doesn't cut a
# legitimate reply (final review I4). readEvents drops frames with no `data:`.
SSE_KEEPALIVE_S = 15.0
KEEPALIVE_FRAME = b": keep-alive\n\n"
_STOPPING: set[asyncio.Future] = set()   # run_turn steps finishing their cleanup after a hang-up


class AttachmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = Field(default=None, max_length=100)
    kind: Literal["image", "audio"]
    mime: str = Field(min_length=3, max_length=100)
    name: str = Field(default="", max_length=255)
    size: int = Field(default=0, ge=0)
    base64: str


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(default="", max_length=100_000)
    attachments: list[AttachmentIn] = Field(default_factory=list, max_length=10)


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    think: Literal["off", "on", "low", "medium", "high"] = "off"
    num_ctx: int | None = Field(default=None, ge=256, le=2_097_152)
    keep_alive: str | int | None = None
    num_predict: int | None = Field(default=None, ge=1, le=1_000_000)


def call_settings(s: SettingsIn) -> CallSettings:
    """The turn's settings, with the context size and keep-alive clamped to the
    deployment's limits (v2.4 Task F: on a shared server they are everyone's RAM)."""
    num_ctx, keep_alive = inference_limits.clamp(s.num_ctx, s.keep_alive, inference_limits.get_limits())
    return CallSettings(think=s.think, num_ctx=num_ctx, keep_alive=keep_alive, num_predict=s.num_predict)


class ContextIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    timezone: str | None = Field(default=None, max_length=64)


class NewSessionIn(BaseModel):
    """Ruling R4: used only when this turn creates the session."""
    model_config = ConfigDict(extra="forbid")
    pins: list[dict[str, Any]] = Field(default_factory=list, max_length=6)


class TurnIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: MessageIn
    model: str = Field(min_length=1, max_length=300)
    settings: SettingsIn = Field(default_factory=SettingsIn)
    context: ContextIn = Field(default_factory=ContextIn)
    session: NewSessionIn | None = None


def _decode(attachments: list[AttachmentIn]) -> list[store.NewAttachment]:
    out = []
    for a in attachments:
        label = a.name or "An attachment"
        if not a.mime.startswith(f"{a.kind}/"):
            raise refusal(422, "invalid_attachment", f"{label} is not a valid {a.kind} file.")
        try:
            data = base64.b64decode(a.base64, validate=True)
        except (binascii.Error, ValueError):
            data = b""
        if not data:
            raise refusal(422, "invalid_attachment", f"{label} could not be read.")
        out.append(store.NewAttachment(kind=a.kind, mime=a.mime, name=a.name, data=data, client_id=a.id))
    return out


async def _budget_state_if_over(user_id: str, budget: int | None) -> dict | None:
    try:
        async with get_pool().connection() as conn:
            if await inference_budget.over_budget(conn, user_id, budget):
                return await inference_budget.budget_state(conn, user_id, budget)
    except Exception:
        # Fail open — chat must survive a Postgres hiccup here (today's gateway rule).
        logger.warning("Budget pre-check failed (fail-open)", exc_info=True)
    return None


async def _capabilities(llm: Any, model_id: str) -> Capabilities:
    try:
        return await llm.capabilities(model_id)
    except Exception:  # noqa: BLE001 — unknown means nothing is refused
        logger.warning("Capability lookup failed for %s (fail-open)", model_id, exc_info=True)
        return Capabilities()


def _frame(ev: dict) -> bytes:
    return f"data: {json.dumps(ev, ensure_ascii=False, separators=(',', ':'))}\n\n".encode()


async def _stop(pending: asyncio.Future | None, events: AsyncGenerator[dict, None]) -> None:
    """Close run_turn as a disconnect does: a hang-up saves `aborted`.

    `pending` is the __anext__ still in flight, if any. Cancelling it raises
    CancelledError inside run_turn where it waits, and run_turn's own finally
    saves `aborted` and releases the claim; then it is closed. Otherwise
    run_turn is suspended at a yield (or already finished) and aclose() does
    the same from there. Trap: a cancelled request scope (anyio) re-cancels
    every await. If that interrupts the wait below, run_turn is still running
    its own cleanup in `pending`; calling aclose() on it then would raise
    "already running", so the cancellation propagates instead and `pending`
    finishes the cleanup on its own."""
    if pending is not None:
        if not pending.done():
            pending.cancel()
            _STOPPING.add(pending)   # a strong reference, in case the wait below is interrupted
            pending.add_done_callback(_STOPPING.discard)
            await asyncio.wait({pending})
        if not pending.cancelled():
            pending.exception()   # retrieved: an unread failure would log "never retrieved"
    await events.aclose()


async def _sse(events: AsyncGenerator[dict, None]) -> AsyncIterator[bytes]:
    """Frame each orchestrator event as one SSE `data:` line, always ending
    with `[DONE]` (ruling R5). `error` is terminal in `events` itself, but if
    the orchestrator raises OUT of its own handling (a bug, not a refusal) we
    still owe the client a terminal frame instead of a bare disconnect — never
    the exception's text, only a generic internal_error (spec §10).

    Keep-alive (final review I4): after SSE_KEEPALIVE_S seconds with no event,
    a comment frame. Trap: the wait for the next event must never be
    cancelled by that timer, because cancelling an async generator's pending
    __anext__ closes the generator, i.e. ends the turn. So each __anext__ runs
    as its own task, and a timeout just waits on the SAME task again. The
    first event is awaited directly: run_turn reaches its first yield without
    suspending, so once _sse has started, run_turn has too, and its own finally
    owns the end of the turn (see _EagerCloseStreamingResponse)."""
    seq = 0
    pending: asyncio.Future | None = None
    try:
        try:
            ev = await events.__anext__()
            while True:
                seq = ev.get("seq", seq + 1)
                yield _frame(ev)
                pending = asyncio.ensure_future(events.__anext__())
                while not (await asyncio.wait({pending}, timeout=SSE_KEEPALIVE_S))[0]:
                    yield KEEPALIVE_FRAME
                step, pending = pending, None
                try:
                    ev = step.result()
                except StopAsyncIteration:
                    break
        finally:
            # A client that hangs up closes run_turn too, which saves `aborted`.
            await _stop(pending, events)
    except StopAsyncIteration:
        pass   # run_turn ended before its first event (it never does today)
    except Exception:
        logger.exception("Chat turn stream failed unexpectedly")
        yield _frame({"type": "error", "seq": seq + 1, "code": "internal_error",
                      "message": INTERNAL_ERROR_MESSAGE})
    yield b"data: [DONE]\n\n"


class _EagerCloseStreamingResponse(StreamingResponse):
    """Controller ruling: Starlette 0.52.1's StreamingResponse never acloses
    its body_iterator on a client disconnect. On disconnect it cancels a task
    group racing `listen_for_disconnect` against `stream_response` (or, on
    newer ASGI spec versions, lets a failed `send` raise OSError) — and ASGI
    delivers the disconnect on its own schedule, independent of what the SSE
    generator happens to be doing at that instant. Land it while
    `stream_response` is between chunks (awaiting `send`, not awaiting the
    generator's own `__anext__`) and the generator is simply abandoned,
    still suspended at its last `yield`, with nothing left to resume `_sse` /
    `run_turn` and release the turn's claim — until CPython's GC happens to
    finalize the orphaned generator, which is unbounded, and long enough in
    practice that every later message in that chat gets 409 turn_in_progress
    meanwhile (the heartbeat task keeps the claim looking alive). Force it
    deterministically instead: whatever `__call__` returns through (normal
    completion, the disconnect race described above, or another exception),
    close the body iterator ourselves. A second close (`_sse` already closed
    run_turn) is a no-op on an exhausted or already-closed async generator.

    Final review I1: the disconnect can also land before `_sse` ever starts
    (while `http.response.start` is being sent). Closing a generator that
    never started runs none of its body, so run_turn never starts either and
    nothing would release the claim begin_turn already committed. If run_turn
    is still unstarted after the close, `abandon` ends the turn instead.
    Never both: once run_turn has started, its own finally owns that.
    """

    def __init__(self, content: AsyncIterator[bytes], *, turn: AsyncGenerator[dict, None],
                 abandon: Callable[[], Awaitable[None]], **kwargs: Any) -> None:
        super().__init__(content, **kwargs)
        self._turn = turn
        self._abandon = abandon

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.body_iterator.aclose()
            if inspect.getasyncgenstate(self._turn) == inspect.AGEN_CREATED:
                await self._turn.aclose()   # so nothing can start it later
                await self._abandon()


@router.post("/{session_id}/turns")
async def post_turn(session_id: SessionId, request: Request,
                    principal: Principal = Depends(require_capability("chat"))):
    if not is_ready():
        raise refusal(503, "db_unavailable", "Chat is offline: the database is unavailable.")
    llm = get_router()
    if not llm.has_providers():
        raise refusal(503, "no_providers", "No model provider is configured.")
    cfg = get_chat_config()
    raw = await read_capped_body(request, cfg.max_request_mb)
    try:
        body = TurnIn.model_validate_json(raw)
    except ValidationError as e:
        raise refusal(422, "invalid_request", "The request is malformed.",
                      errors=e.errors(include_url=False, include_input=False, include_context=False))

    # Ruling R4/ownership order: session_info() only informs refusals BEFORE
    # begin_turn; begin_turn's own NotOwner (below) is the actual authority,
    # so a non-owner never learns whether the chat exists either way.
    info = await store.session_info(session_id)
    if info is not None and info.owner_id != principal.user_id:
        raise refusal(404, "not_found", NOT_FOUND)
    attachments = _decode(body.message.attachments)
    pins = info.pins if info is not None else (body.session.pins if body.session else [])
    if not body.message.content.strip() and not attachments and not pins:
        raise refusal(422, "empty_message", "Type a message or attach something first.")
    if not llm.is_allowed(body.model):
        raise refusal(422, "model_not_allowed", "That model isn't available on this server.")
    model_id = llm.canonical_id(body.model)
    if not await llm.is_listed(model_id):
        raise refusal(422, "model_not_allowed", "This model isn't available.")
    caps = await _capabilities(llm, model_id)
    for kind, flag, word in (("image", caps.vision, "images"), ("audio", caps.audio, "audio")):
        if flag is False and any(a.kind == kind for a in attachments):
            raise refusal(422, "attachment_unsupported", f"This model can't read {word}.", kind=kind)
    budget = model_router.get_config().daily_token_budget
    state = await _budget_state_if_over(principal.user_id, budget)
    if state is not None:
        raise refusal(429, "budget_exhausted", "Daily inference budget exhausted.", **state)
    try:
        claim = await store.begin_turn(
            session_id=session_id, user_id=principal.user_id, model_id=model_id,
            text=body.message.content, attachments=attachments,
            new_session_pins=body.session.pins if body.session else None)
    except store.NotOwner:
        raise refusal(404, "not_found", NOT_FOUND)
    except store.TurnInProgress:
        raise refusal(409, "turn_in_progress", "A reply is still being written in this chat.")

    req = TurnRequest(
        user_id=principal.user_id, session_id=session_id, model_id=model_id, text=body.message.content,
        attachments=tuple(Attachment(a.kind, a.mime, base64.b64encode(a.data).decode("ascii"), a.name)
                          for a in attachments),
        settings=call_settings(body.settings),
        doc_id=body.context.doc_id, timezone=body.context.timezone)
    events = run_turn(req, claim, router=llm, cfg=cfg, deployment_budget=budget)
    return _EagerCloseStreamingResponse(_sse(events), turn=events, abandon=lambda: abandon_turn(req, claim),
                                        media_type="text/event-stream",
                                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
