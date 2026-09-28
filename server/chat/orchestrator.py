"""One chat turn, server-side (spec §5.1).

run_turn is an async generator of event dicts (spec §6); routers/chat_turns.py
frames them as SSE. Per step: stream one model call; if it asked for tools, run
them and go again; after CHAT_MAX_TOOL_ROUNDS rounds, one last step with tools
OFF, so the turn always ends in an answer (the OpenAI Agents SDK max_turns
idea). Provider and tool failures become an `error` event or a tool error the
model sees; they never escape. A disconnect (the Stop button, a closed tab)
arrives as cancellation: the partial reply is saved `aborted` and the claim is
released, even though the task is being torn down (spec §5.4).
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from contextlib import aclosing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from ..db import get_pool
from ..llm.router import UnknownModel
from ..llm.types import (Attachment, CallSettings, Capabilities, FeatureDropped, Finish, Message,
                         ProviderError, ProviderTimeout, ProviderUnavailable, ReasoningDelta,
                         TextDelta, ToolCall, ToolCallReady, Usage)
from ..services import inference_budget
from ..services.doc_search import ReadableDoc, readable_doc
from . import store
from .config import ChatConfig
from .context import TurnInput, build_context
from .store import TurnClaim, title_from_prompt
from .tools import ToolContext, available_tools, run_tool

logger = logging.getLogger(__name__)

ERROR_TEXT = {
    "provider_unavailable": "Can't reach the model provider.",
    "provider_timeout": "The model provider stopped responding.",
    "budget_exhausted": "Daily inference budget exhausted.",
    "model_not_allowed": "That model isn't available on this server.",
    "internal_error": "Something went wrong on the server.",
}
# Ruling R1: what today's toasts said when a model rejected a feature.
_FEATURE_NOTICE = {
    "tools": ("tools_unsupported", "This model rejected tools — answered without them."),
    "thinking": ("thinking_unsupported", "This model rejected thinking — answered without it."),
    "think_level": ("think_level_unsupported", "This model rejected the thinking level — used plain thinking."),
}
_FEATURE_LOG = {"tools": "tool-fallback", "thinking": "think-fallback", "think_level": "think-fallback"}
_BACKGROUND: set[asyncio.Task] = set()   # end-of-turn saves that outlive a cancelled request
# A small model (llama3.2:3b) sometimes writes its tool call as reply text,
# `{"name": ..., "parameters": {...}}`, often in a ```json fence. A step's
# opening text is held while it could still be one, up to this many characters.
TEXT_TOOLCALL_PROBE_CHARS = 2000
_FENCE_OPEN = re.compile(r"```[\w+-]*")
_FENCED = re.compile(r"```[\w+-]*\s*(.*?)\s*(?:```)?", re.DOTALL)


@dataclass(frozen=True)
class TurnRequest:
    user_id: str
    session_id: str
    model_id: str                         # canonical provider:name
    text: str
    attachments: tuple[Attachment, ...]
    settings: CallSettings
    doc_id: str | None
    timezone: str | None


@dataclass
class _State:
    content: str = ""
    thinking: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    doc_context: dict | None = None
    steps: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated: bool = False
    last_usage: Usage | None = None
    last_finish: str = "stop"
    finish_reason: str = "stop"
    # The step currently in flight (fix round 1, item 2): a turn cut short by
    # cancellation or a provider failure never reaches the normal end-of-step
    # _record_usage call below, so its tokens would otherwise be free (spec
    # §5.6). _finalize charges this instead, once, from whichever of these is
    # freshest; step_recorded=True means there's nothing left to charge.
    pending_messages: list | None = None
    pending_output: str = ""
    pending_usage: Usage | None = None
    step_recorded: bool = True

    def add(self, usage: Usage, finish: str) -> None:
        self.prompt_tokens += usage.prompt_tokens
        self.completion_tokens += usage.completion_tokens
        self.estimated = self.estimated or usage.estimated
        self.last_usage, self.last_finish = usage, finish

    def usage_json(self) -> dict[str, Any]:
        return {"promptTokens": self.prompt_tokens, "completionTokens": self.completion_tokens,
                "estimated": self.estimated}

    def stats(self, model_id: str, started: float) -> dict[str, Any]:
        """The message's stats disclosure. Per-request figures are the LAST
        step's, as today (the truncation notice and tokens/s read them)."""
        u = self.last_usage
        d = u.detail if u else {}
        return {"model": model_id, "doneReason": self.last_finish if self.steps else None,
                "promptEvalCount": u.prompt_tokens if u else None,
                "evalCount": u.completion_tokens if u else None,
                "totalNs": int((time.monotonic() - started) * 1e9),
                "loadNs": d.get("load_duration"), "promptEvalNs": d.get("prompt_eval_duration"),
                "evalNs": d.get("eval_duration"), "steps": self.steps, "usageEstimated": self.estimated}


def _usage_json(u: Usage) -> dict[str, Any]:
    return {"promptTokens": u.prompt_tokens, "completionTokens": u.completion_tokens, "estimated": u.estimated}


class _TextProbe:
    """Holds a step's opening text while it could still be a tool call
    written as JSON: only whitespace so far, or `{`, or a code fence heading
    for `{`. Anything else (and anything past TEXT_TOOLCALL_PROBE_CHARS) is
    let through at once, so ordinary prose is never delayed."""

    def __init__(self, active: bool) -> None:
        self.active = active
        self.held = ""

    def feed(self, delta: str) -> str:
        """The text to show now: "" while holding, else everything held so far."""
        if not self.active:
            return delta
        self.held += delta
        if len(self.held) <= TEXT_TOOLCALL_PROBE_CHARS and _may_be_json_object(self.held):
            return ""
        return self.release()

    def release(self) -> str:
        self.active = False
        out, self.held = self.held, ""
        return out


def _may_be_json_object(text: str) -> bool:
    s = text.lstrip()
    if not s or s[0] == "{":
        return True
    if not s.startswith("```"):
        return "```".startswith(s)   # "`" or "``": maybe a fence yet
    rest = s[_FENCE_OPEN.match(s).end():].lstrip()   # past the fence and its language tag
    return not rest or rest[0] == "{"


def _text_tool_call(held: str, offered: list, step: int) -> ToolCall | None:
    """`held` as a call to one of this step's tools, if it is exactly one JSON
    object {"name": <tool>, "parameters" | "arguments": {...}} (an optional
    "type": "function" too), fenced or not; else None."""
    s = held.strip()
    if s.startswith("```"):
        s = _FENCED.fullmatch(s).group(1)
    try:
        obj = json.loads(s)
    except ValueError:
        return None
    if not isinstance(obj, dict) or obj.get("type", "function") != "function":
        return None
    arg_keys = [k for k in ("parameters", "arguments") if k in obj]
    if set(obj) - {"name", "parameters", "arguments", "type"} or len(arg_keys) != 1:
        return None
    name, args = obj.get("name"), obj[arg_keys[0]]
    if not isinstance(args, dict) or not isinstance(name, str) or name not in {t.name for t in offered}:
        return None
    return ToolCall(id=f"text_call_{step}", name=name, arguments=args)


async def run_turn(req: TurnRequest, claim: TurnClaim, *, router: Any, cfg: ChatConfig,
                   deployment_budget: int | None) -> AsyncIterator[dict[str, Any]]:
    seq = 0

    def ev(kind: str, **fields: Any) -> dict[str, Any]:
        nonlocal seq
        seq += 1
        return {"type": kind, "seq": seq, **fields}

    state = _State()
    started = time.monotonic()
    outcome = "error"   # complete | aborted | error
    beat = asyncio.create_task(_heartbeat(claim))
    logger.info("chat turn start user=%s session=%s model=%s", req.user_id, req.session_id, req.model_id)
    try:
        yield ev("start", runId=claim.turn_id, sessionId=claim.session_id,
                 userMessageId=claim.user_message_id, messageId=claim.assistant_message_id)
        await _log_sent(claim, req)
        caps = await _capabilities(router, req.model_id)
        doc = await _open_doc(req)
        pins, history = await store.load_turn_context(
            claim.session_id, exclude=(claim.user_message_id, claim.assistant_message_id))
        built = await build_context(TurnInput(
            user_id=req.user_id, text=req.text, attachments=req.attachments, doc=doc,
            timezone=req.timezone, pins=pins, history=history,
            window=_window(router, req, caps), now=datetime.now(timezone.utc)), cfg)
        if built.notes:
            state.doc_context = {"notes": built.notes}
            yield ev("data-context", items=built.notes)
        tool_ctx = ToolContext(req.user_id, doc)
        offered = [] if caps.tools is False else available_tools(tool_ctx)
        messages = list(built.messages)
        rounds = 0

        def say(delta: str) -> list[dict[str, Any]]:
            """Show `delta` as this step's text: text-start before its first
            piece. State is updated before the caller yields the events: a
            cancellation lands AT a yield (fix round 1, item 2)."""
            nonlocal text, open_text
            if not delta:
                return []
            out = []
            if not open_text:
                open_text = True
                # A later step's text is a new paragraph, not a run-on
                # ("search.The answer"); chatEvents.js adds the same
                # separator on text-start (final review M5).
                if state.content:
                    state.content += "\n\n"
                out.append(ev("text-start", id=f"t{step}"))
            text += delta
            state.content += delta
            state.pending_output = text + probe.held + reasoning
            out.append(ev("text-delta", id=f"t{step}", delta=delta))
            return out

        while True:
            if await _over_budget(req.user_id, deployment_budget):
                yield ev("error", code="budget_exhausted", message=ERROR_TEXT["budget_exhausted"])
                await _log_event(claim.session_id, "budget", "daily token budget exhausted")
                return
            state.steps += 1
            step = state.steps
            tools_now = offered if rounds < cfg.max_tool_rounds else []
            # rounds > 0: "max-steps" means a tool round actually ran and got
            # capped, not merely that CHAT_MAX_TOOL_ROUNDS=0 kept tools off
            # from the first step (fix round 1, item 3).
            capped = bool(offered) and rounds > 0 and not tools_now
            state.pending_messages = messages
            state.pending_output = ""
            state.pending_usage = None
            state.step_recorded = False
            yield ev("start-step", step=step)
            text = reasoning = ""
            calls: list = []
            usage: Usage | None = None
            finish = "stop"
            open_text = open_reasoning = tools_dropped = False
            probe = _TextProbe(active=bool(tools_now))   # nothing to recover on a tools-off step
            last_flush = time.monotonic()
            async with aclosing(router.stream_chat(req.model_id, messages, [t.spec for t in tools_now],
                                                   req.settings)) as stream:
                async for chunk in stream:
                    if isinstance(chunk, ReasoningDelta):
                        if not open_reasoning:
                            open_reasoning = True
                            yield ev("reasoning-start", id=f"r{step}")
                        reasoning += chunk.text
                        state.thinking += chunk.text
                        # Set before the yield, not after: a cancellation lands
                        # AT a yield, and by then this chunk's text must already
                        # be reflected — same reason state.content is set before
                        # its yield too (fix round 1, item 2).
                        state.pending_output = text + probe.held + reasoning
                        yield ev("reasoning-delta", id=f"r{step}", delta=chunk.text)
                    elif isinstance(chunk, TextDelta):
                        if open_reasoning:
                            open_reasoning = False
                            yield ev("reasoning-end", id=f"r{step}")
                        shown = probe.feed(chunk.text)
                        state.pending_output = text + probe.held + reasoning
                        for e in say(shown):
                            yield e
                    elif isinstance(chunk, ToolCallReady):
                        if tools_now:   # a tools-off step cannot call tools
                            calls.append(chunk.call)
                    elif isinstance(chunk, Usage):
                        usage = chunk
                        state.pending_usage = chunk
                    elif isinstance(chunk, Finish):
                        finish = chunk.reason
                    elif isinstance(chunk, FeatureDropped):
                        if chunk.feature == "tools":   # this step's reply can't be a tool call
                            tools_dropped = True
                            for e in say(probe.release()):
                                yield e
                        code, notice = _FEATURE_NOTICE[chunk.feature]
                        await _log_event(claim.session_id, _FEATURE_LOG[chunk.feature], notice)
                        yield ev("data-notice", code=code, message=notice)
                    if time.monotonic() - last_flush >= store.FLUSH_EVERY_S:
                        await _save(claim, state)   # a reload mid-reply shows the text so far
                        last_flush = time.monotonic()
            if open_reasoning:
                yield ev("reasoning-end", id=f"r{step}")
            recovered = (None if calls or tools_dropped or not probe.held
                         else _text_tool_call(probe.held, tools_now, step))
            if recovered:
                # Run it as the native call it was meant to be; its JSON is
                # never shown or saved (probe.held is still billed below).
                logger.debug("recovered text tool call name=%s", recovered.name)
                calls.append(recovered)
                finish = "tool_calls"
            else:
                for e in say(probe.release()):
                    yield e
            if open_text:
                yield ev("text-end", id=f"t{step}")
            # Mark first, then shield the write: a cancel landing mid-write (e.g.
            # during the pool's commit) can't stop the row landing, and _finalize
            # must never charge this step a second time.
            state.step_recorded = True
            usage = await asyncio.shield(_record_usage(req.user_id, usage, messages,
                                                       text + probe.held + reasoning))
            state.add(usage, finish)
            yield ev("finish-step", step=step, usage=_usage_json(usage), finishReason=finish)
            await _save(claim, state)
            if not calls:
                state.finish_reason = ("max-steps" if capped and not text.strip()
                                       else "length" if finish == "length" else "stop")
                break
            rounds += 1
            messages.append(Message("assistant", text, tool_calls=tuple(calls)))
            await _log_event(claim.session_id, "tool-call",
                             f"{len(calls)} tool call{'s' if len(calls) > 1 else ''}")
            for call in calls:
                yield ev("tool-input-available", toolCallId=call.id, toolName=call.name, input=call.arguments)
                run = await run_tool(call, tool_ctx, tools_now)
                state.tool_calls.append(run.summary)
                if run.ok:
                    yield ev("tool-output-available", toolCallId=call.id, output=run.summary["result_summary"])
                else:
                    yield ev("tool-output-error", toolCallId=call.id, errorText=run.result["error"])
                messages.append(Message("tool", json.dumps(run.result, indent=2, ensure_ascii=False),
                                        tool_call_id=call.id, name=call.name))
            await _save(claim, state)
        # Spec §5.2.1: did the model still search after a prefetch hit? Lets a
        # deployer tune CHAT_PREFETCH_MIN_SCORE. DEBUG, and no user text.
        logger.debug("chat turn %s prefetch_hit=%s search_document_called=%s", claim.turn_id,
                     built.prefetch_hit, any(c["name"] == "search_document" for c in state.tool_calls))
        if state.last_finish == "length":
            await _log_event(claim.session_id, "truncated", "reply hit the length limit")
        await _log_event(claim.session_id, "received", f"assistant reply ({len(state.content)} chars)")
        outcome = "complete"
        yield ev("finish", usage=state.usage_json(), finishReason=state.finish_reason,
                 stats=state.stats(req.model_id, started))
    except (asyncio.CancelledError, GeneratorExit):
        if outcome != "complete":   # a disconnect while sending `finish` doesn't undo a finished reply
            outcome = "aborted"
        raise
    except (ProviderUnavailable, ProviderTimeout, ProviderError, UnknownModel) as e:
        code, message = _provider_error(e)
        # The SPA must get its `error` event even if the DB that add_event
        # would write to is the very thing that's down (fix round 1, item 1).
        yield ev("error", code=code, message=message)
        await _log_event(claim.session_id, "error", message)
    except Exception:
        logger.exception("chat turn failed user=%s session=%s", req.user_id, req.session_id)
        yield ev("error", code="internal_error", message=ERROR_TEXT["internal_error"])
        await _log_event(claim.session_id, "error", ERROR_TEXT["internal_error"])
    finally:
        beat.cancel()
        await _finalize(claim, state, outcome, req, started)


async def abandon_turn(req: TurnRequest, claim: TurnClaim) -> None:
    """End a turn whose run_turn never started (final review I1): the client
    left before the response began, so run_turn's own finally never runs. The
    same end as a Stop before the first token: the empty reply is saved
    `aborted`, the `aborted` log line is written and the claim is released,
    all shielded as in _finalize. Only for a run_turn generator that never
    started; once it has, its own finally owns this."""
    await _finalize(claim, _State(), "aborted", req, time.monotonic())


def _provider_error(e: Exception) -> tuple[str, str]:
    if isinstance(e, ProviderTimeout):
        return "provider_timeout", ERROR_TEXT["provider_timeout"]
    if isinstance(e, ProviderUnavailable):
        return "provider_unavailable", ERROR_TEXT["provider_unavailable"]
    if isinstance(e, ProviderError):
        return "provider_error", f"The model provider returned an error: {e.safe_message}"
    return "model_not_allowed", ERROR_TEXT["model_not_allowed"]


async def _finalize(claim: TurnClaim, state: _State, outcome: str, req: TurnRequest, started: float) -> None:
    """Write the end of the turn, even from a cancelled task: the write runs in
    its own task under asyncio.shield, so a second cancellation (anyio re-raises
    on every await in a cancelled scope) can't stop it releasing the claim."""
    finish_reason = state.finish_reason if outcome == "complete" else outcome

    async def write() -> None:
        if outcome != "complete" and not state.step_recorded and state.pending_messages is not None:
            # The step in flight when the turn was cut short never reached the
            # normal end-of-step _record_usage call: charge it now, from the
            # provider's own count if one arrived, else the same chars/4
            # estimate _record_usage already uses (fix round 1, item 2). But
            # only if the provider actually did something billable: it sent a
            # Usage chunk, or produced output, or the user pulled the plug
            # (aborted) after the prompt was already processed. A provider
            # that refuses before generating anything (e.g. HTTP 400 on an
            # invalid model) leaves nothing to charge (walk fix 2).
            if outcome == "aborted" or state.pending_usage is not None or state.pending_output:
                await _record_usage(req.user_id, state.pending_usage, state.pending_messages, state.pending_output)
            state.step_recorded = True
        if outcome == "aborted":
            await _log_event(claim.session_id, "aborted", "user stopped the stream")
        await store.finish_turn(claim, status=outcome, finish_reason=finish_reason, content=state.content,
                                thinking=state.thinking, tool_calls=state.tool_calls or None,
                                doc_context=state.doc_context, stats=state.stats(req.model_id, started))

    def _on_done(t: asyncio.Task) -> None:
        # Once this coroutine stops awaiting `task` below (a second
        # cancellation), nothing else ever reads its outcome — read it here so
        # a failure that lands after that point is still logged, not just
        # silently dropped by asyncio's default "exception never retrieved"
        # handler (fix round 1, item 4). No message content, just the turn id.
        _BACKGROUND.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.warning("Background end-of-turn write for turn %s failed", claim.turn_id, exc_info=exc)

    task = asyncio.ensure_future(write())
    _BACKGROUND.add(task)
    task.add_done_callback(_on_done)
    try:
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # A cancellation landed here specifically (not the pending one
            # from run_turn's own except-clause, if any): shield kept `task`
            # itself running, so the claim will still be released in the
            # background — but this await must not swallow the cancellation,
            # or a caller that cancelled us would wrongly see a normal return
            # (fix round 1, item 5).
            logger.warning("Turn %s: cancelled again while saving; the write continues in the background",
                           claim.turn_id)
            raise
        except Exception:
            logger.warning("Saving the end of turn %s failed", claim.turn_id, exc_info=True)
    finally:
        logger.info("chat turn end user=%s session=%s model=%s status=%s reason=%s steps=%d tokens=%d+%d "
                    "duration_ms=%d", req.user_id, req.session_id, req.model_id, outcome, finish_reason,
                    state.steps, state.prompt_tokens, state.completion_tokens,
                    int((time.monotonic() - started) * 1000))


async def _heartbeat(claim: TurnClaim) -> None:
    """Refresh the claim on a timer, not per token: a long prompt can take
    minutes before the first token arrives (spec §5.5)."""
    while True:
        await asyncio.sleep(store.HEARTBEAT_S)
        try:
            if not await store.heartbeat(claim.session_id, claim.turn_id):
                logger.warning("Turn %s no longer holds its session's claim", claim.turn_id)
                return
        except Exception:
            logger.warning("Heartbeat for turn %s failed", claim.turn_id, exc_info=True)


async def _log_event(session_id: str, kind: str, message: str) -> None:
    """The sidebar's Log is advisory: a blip writing one line of it must never
    surface as a second, unrelated failure — or, worse, escape uncaught and
    cost the SPA both `finish` and `error` (fix round 1, item 1). Never logs
    the message text itself, only which kind of line failed (spec §10)."""
    try:
        await store.add_event(session_id, kind, message)
    except Exception:
        logger.warning("Logging chat event kind=%s for session=%s failed", kind, session_id, exc_info=True)


async def _save(claim: TurnClaim, state: _State) -> None:
    await store.save_progress(claim.assistant_message_id, content=state.content, thinking=state.thinking,
                              tool_calls=state.tool_calls or None, doc_context=state.doc_context)


async def _log_sent(claim: TurnClaim, req: TurnRequest) -> None:
    await _log_event(claim.session_id, "sent",
                     f"prompt: {title_from_prompt(req.text or '(attachment only)')}")
    if req.attachments:
        images = sum(1 for a in req.attachments if a.kind == "image")
        audio = len(req.attachments) - images
        parts = ([f"{images} image{'s' if images > 1 else ''}"] if images else []) + \
                ([f"{audio} audio"] if audio else [])
        await _log_event(claim.session_id, "attached", ", ".join(parts))


async def _capabilities(router: Any, model_id: str) -> Capabilities:
    try:
        return await router.capabilities(model_id)
    except Exception:  # noqa: BLE001 — unknown capabilities are safe: nothing is refused
        logger.warning("Capability lookup failed for %s", model_id, exc_info=True)
        return Capabilities()


def _window(router: Any, req: TurnRequest, caps: Capabilities) -> int | None:
    """The trimming window (ruling R14): num_ctx is an Ollama option, so it
    counts only when the model's provider is Ollama. A value left over from an
    Ollama model must not trim an OpenAI-kind chat (final review M4)."""
    if req.settings.num_ctx and router.provider_kind(req.model_id) == "ollama":
        return req.settings.num_ctx
    return caps.context_window


async def _open_doc(req: TurnRequest) -> ReadableDoc | None:
    if not req.doc_id:
        return None
    try:
        async with get_pool().connection() as conn:
            return await readable_doc(conn, req.doc_id, req.user_id)
    except Exception:  # noqa: BLE001 — no document context is a safe fallback
        logger.warning("Open-document lookup failed for %s", req.doc_id, exc_info=True)
        return None


async def _over_budget(user_id: str, deployment_budget: int | None) -> bool:
    try:
        async with get_pool().connection() as conn:
            return await inference_budget.over_budget(conn, user_id, deployment_budget)
    except Exception:
        logger.warning("Budget check failed (fail-open)", exc_info=True)
        return False


async def _record_usage(user_id: str, usage: Usage | None, messages: list[Message], output: str) -> Usage:
    """Record the step's usage now. A provider that sends no counts is charged
    an estimate (chars/4), so it can't be used for free (spec §5.6)."""
    if usage is None:
        prompt = math.ceil(sum(len(m.content) for m in messages) / 4)
        usage = Usage(prompt, math.ceil(len(output) / 4), estimated=True)
        logger.warning("Provider returned no usage; recorded an estimate (%d+%d tokens)",
                       usage.prompt_tokens, usage.completion_tokens)
    try:
        async with get_pool().connection() as conn:
            await inference_budget.record_usage(conn, user_id, usage.prompt_tokens, usage.completion_tokens)
    except Exception:
        logger.warning("Usage accounting failed (fail-open)", exc_info=True)
    return usage
