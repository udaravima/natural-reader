"""The phone service: the AudioSocket listener's life with the app, who is let through when a call connects, and the
real wiring of each call (spec §5.3 "Connect" and "Ends", §5.10, §5.11).

A call that connects is let through only if:
1. its line exists (an unknown line is hung up on at once, and nothing is recorded);
2. the line is on, has a project, and its service user is still a reader of that project (else `closed`): a
   maintainer revokes a line by removing that member, and a deleted project leaves the line on with no project;
3. the line's user has budget left today, by the check a chat turn makes before it starts (else `closed`, recorded
   `budget`);
4. the line's model may still answer, by the chat turn's own rules (`model_settings`, R10.10): the environment
   allows it and the console's chat list offers it, and its provider still has it (else `closed`). A model withdrawn
   after the line was switched on closes the line at its next call, as a removed membership does. The provider's
   list may need a request: it runs with no connection held, and the phone waits at most MODEL_CHECK_S for it, then
   lets the call through, as the router does for a provider it can't reach (R10.11);
5. the line and the whole service are under their call caps (else `busy`). The counters live in this process: with
   WORKERS > 1 each worker counts its own calls.
Then it is `open_call`, `CallSession.run()`, `close_call`. A refused caller hears its clip, then the hang-up, and the
refusal is recorded. If the line can't be checked at connect (the database can't be reached, or the model router
hasn't started), a caller hears the line's `busy` clip when this process holds it, else just the hang-up, and only
the log knows.

No pool connection is held across a call (R10.4): each step takes its own short connection, which commits as it
ends, so `begin_turn`, on a connection of its own, finds the chat `open_call` wrote. A connection is waited for at
most DB_WAIT_S, and the whole gate is bounded at GATE_S (a stalled query is the database-down path). Asterisk drops
a call that gets no audio from the app for 2 s, so silence goes down the line from connect (a keep-alive pacer with
nothing queued) until a clip or the call session's own pacer takes over; it stops before either starts, and before
any hang-up, so two pacers never run at once and nothing follows the 0x00.

A call is ended from outside only through its media's `hang_up()`, which leads to the session's own end (R10.7): its
`run()` task is never cancelled.

Logs carry ids, durations and end reasons, never what anyone said."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import secrets
import string
from collections import Counter
from dataclasses import dataclass, field

import psycopg

from .. import db
from ..auth.authz import project_role
from ..chat import store as chat_store
from ..chat.config import get_chat_config
from ..chat.orchestrator import run_turn
from ..llm.router import get_router
from ..services import chat_options, inference_budget, model_router, model_settings
from ..speech import service as speech_service, window
from . import audiosocket, store, vad, voice
from .call import CallDeps, CallOutcome, CallSession, Hearing
from .clips import ClipCache
from .config import PhoneConfig, load_phone_config, split_listen
from .media import CallMedia
from .pacer import Pacer
from .store import Line

logger = logging.getLogger(__name__)

LANGUAGE = "en"            # a line's language in phase 1 (phone_lines.language)
STALE_S = 3600 + 60        # an open call older than the longest call allowed, plus a minute, was left by a crash
DB_WAIT_S = 1.0            # longest wait for a pool connection at connect: Asterisk drops a silent line after 2 s
MODEL_CHECK_S = 0.5        # longest wait at connect for the provider's model list, from the same 2 s
GATE_S = 3.0               # longest the connect-time checks may take (a stalled query); then the database-down path
STOP_S = 5.0               # how long stop() waits for the calls it hung up to finish their run() and close_call
CLOSE_S = 1.0              # then for the server's last connections to close, before they are aborted
_ID_CHARS = string.digits + string.ascii_lowercase


class RouterNotStarted(RuntimeError):
    """The model router hasn't started (or has stopped): like the database being away, the line can't be checked."""


# What "the line can't be checked" may be without a bug: the database or its pool (psycopg's errors, PoolTimeout
# and PoolClosed among them, or no pool at all), the network (OSError, which covers the GATE_S timeout), or the
# router not started. These are logged by their type alone; anything else gets its traceback.
_OUTAGES = (psycopg.Error, db.DatabaseUnavailable, OSError, RouterNotStarted)


def new_session_id() -> str:
    """A chat id in the web client's own format (useChatEngine.js `newSessionId`: "s-<ms>-<6 base-36 characters>"),
    so a call's transcript is a chat like any other (R10.2). Every call gets a fresh one (R10.9)."""
    return f"s-{chat_store.now_ms()}-{''.join(secrets.choice(_ID_CHARS) for _ in range(6))}"


@dataclass(eq=False)
class _Live:
    """One connection being handled, from its first frame until handle() returns."""
    media: CallMedia
    done: asyncio.Event = field(default_factory=asyncio.Event)
    shutdown: bool = False     # stop() hung it up
    keep_alive: asyncio.Task | None = None   # silence while the call is checked and opened

    def start_keep_alive(self) -> None:
        """Send silence frames from connect, at the phone's pace (a pacer with nothing queued). Asterisk drops a call
        after 2 s without audio from the app, and the gate and `open_call` may wait on the database before any
        clip or session speaks."""
        loop = asyncio.get_running_loop()
        self.keep_alive = asyncio.ensure_future(Pacer(self.media.send, loop.time, asyncio.sleep).run())

    async def quiet(self) -> None:
        """Stop the keep-alive and wait until it has stopped: called before any other sender starts and before any
        hang-up begins, so two pacers never run at once and no frame follows the 0x00 (R9.6). A send that failed
        because the caller left is the expected end; anything else is logged."""
        task = self.keep_alive
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.keep_alive = None
        if not task.cancelled() and not isinstance(task.exception(), ConnectionError):
            logger.warning("phone line %s: the keep-alive stopped (%s)", self.media.line_id,
                           type(task.exception()).__name__)


@dataclass(frozen=True)
class _Gate:
    """What the connect-time reads found."""
    line: Line | None
    refusal: str | None = None         # the end reason, 'closed' or 'budget': the caller hears `closed`
    clips: dict | None = None
    options: chat_options.ChatOptions | None = None
    budget: int | None = None          # the deployment's daily token budget, as post_turn passes it


class PhoneService:
    def __init__(self, cfg: PhoneConfig | None = None) -> None:
        """`cfg` None: read PHONE_* from the environment at each start()."""
        self._given = cfg
        self._cfg = cfg
        self._server: asyncio.Server | None = None
        self._stopping = False
        self._live: set[_Live] = set()
        self._per_line: Counter[str] = Counter()
        self._active = 0
        self._clips = ClipCache()
        self._listing: dict[str, asyncio.Task] = {}   # canonical model id -> its provider-list request in flight
        self._starting: asyncio.Task | None = None     # a start() in progress
        self._warming: asyncio.Task | None = None      # the recognizer's warm-up, started with the listener

    # --- the listener ---

    async def start(self) -> None:
        """Close the calls a crash left open, then listen. A no-op when the listener is off. Never raises: the app
        comes up without phone lines rather than not at all.

        Idempotent: once listening it returns at once, and a start while another is still starting waits for that
        one instead of binding a second listener (the app may call it from the boot and from the late-database path
        together; with `reuse_port` a second bind would quietly succeed)."""
        if self._starting is None or self._starting.done():
            if self._server is not None:
                return
            self._starting = asyncio.ensure_future(self._start())
        await asyncio.shield(self._starting)

    async def _start(self) -> None:
        cfg = self._cfg = self._given or load_phone_config(os.environ)
        if cfg.listen is None:
            return
        try:
            # R10.8: load the VAD now, off the event loop, so the first call doesn't stall every pacer while it loads.
            await asyncio.to_thread(vad._session)
        except Exception:
            logger.exception("The phone listener is off: the voice activity model couldn't load")
            return
        try:
            async with db.get_pool().connection(timeout=DB_WAIT_S) as conn:
                await store.close_stale_calls(conn, STALE_S)
        except Exception as exc:
            logger.warning("Stale phone calls weren't closed (%s)", type(exc).__name__)
        self._stopping = False
        try:
            self._server = await audiosocket.serve(cfg, self.handle)
        except Exception as exc:
            logger.error("The phone listener is off: it can't listen on %s (%s: %s)", cfg.listen,
                         type(exc).__name__, exc)
            return
        logger.info("Phone listener on %s (pbx address %s)", self._bound() or cfg.listen, self._pbx_address())
        # Not awaited: a slow options read or engine set-up must not hold the app's start. stop() cancels it.
        self._warming = asyncio.ensure_future(self._warm_hearing())

    async def _warm_hearing(self) -> None:
        """Start loading the recognizer calls hear with (the deployment's engine for the line language, as `_deps`
        asks for it), so the first caller's first question doesn't wait for a model to load. `warm()` itself only
        starts a load in the background. A failure is logged, and the first call loads it instead."""
        try:
            async with db.get_pool().connection(timeout=DB_WAIT_S) as conn:
                options = await chat_options.load_chat_options(conn)
            speech_service.engine_for(options, LANGUAGE).warm()
        except Exception as exc:
            logger.warning("The phone lines' recognizer wasn't warmed at start (%s); the first call will load it",
                           type(exc).__name__, exc_info=None if isinstance(exc, _OUTAGES) else exc)

    async def stop(self) -> None:
        """R10.6: stop accepting, hang up every live call and wait for each to finish (they are recorded `shutdown`),
        then close the server, aborting whatever connection is left. Run before the pool closes."""
        server, self._server = self._server, None
        self._stopping = True
        if server is not None:
            server.close()
        live = list(self._live)
        for call in live:
            call.shutdown = True
        await asyncio.gather(*(self._hang_up(call) for call in live))
        if live:
            waits = [asyncio.ensure_future(call.done.wait()) for call in live]
            _, pending = await asyncio.wait(waits, timeout=STOP_S)
            for w in pending:
                w.cancel()
            if pending:
                logger.warning("%d phone call(s) still ending at shutdown", len(pending))
        # A provider-list request that outlived its calls' bound (R10.11), and a warm-up still reading the options:
        # nothing waits for them any more.
        warming, self._warming = self._warming, None
        leftovers = list(self._listing.values()) + ([warming] if warming is not None else [])
        for task in leftovers:
            task.cancel()
        await asyncio.gather(*leftovers, return_exceptions=True)
        if server is not None:
            server.close_clients()
            try:
                await asyncio.wait_for(server.wait_closed(), CLOSE_S)
            except asyncio.TimeoutError:
                server.abort_clients()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(server.wait_closed(), CLOSE_S)
            logger.info("Phone listener stopped (%d call(s) hung up)", len(live))

    def status(self) -> dict:
        """What the console shows: {listening, pbx_address, allow, active}. `active` counts this process's calls."""
        cfg = self._config()
        return {"listening": self._server is not None and self._server.is_serving(),
                "pbx_address": self._pbx_address(), "allow": list(cfg.allow), "active": self._active}

    def _config(self) -> PhoneConfig:
        if self._cfg is None:
            self._cfg = self._given or load_phone_config(os.environ)
        return self._cfg

    def _bound(self) -> str | None:
        """The address the server is bound to, as host:port."""
        if self._server is None or not self._server.sockets:
            return None
        host, port = self._server.sockets[0].getsockname()[:2]
        return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"

    def _pbx_address(self) -> str | None:
        """R10.3: the configured address, except that an address taken from a listen port of 0 (tests) can't be
        dialled, so the port the server really bound is reported."""
        cfg = self._config()
        parts = split_listen(cfg.listen) if cfg.listen else None
        if parts and parts[1] == 0 and cfg.pbx_address == cfg.listen:
            return self._bound() or cfg.pbx_address
        return cfg.pbx_address

    # --- a call ---

    async def handle(self, media: CallMedia) -> None:
        """One connection, from its first frame to its hang-up. Never raises. Silence goes down the line from the
        start (`_Live.start_keep_alive`), until a clip or the call session takes over, or the hang-up."""
        live = _Live(media)
        if self._stopping:
            await self._hang_up(live)
            return
        self._live.add(live)
        live.start_keep_alive()
        try:
            await self._handle(media, live)
        except Exception:
            logger.exception("phone line %s: the call failed", media.line_id)
            await self._hang_up(live)
        finally:
            if live.keep_alive is not None:
                live.keep_alive.cancel()         # handle() itself was cancelled: nothing may keep sending
            self._live.discard(live)
            live.done.set()

    async def _handle(self, media: CallMedia, live: _Live) -> None:
        line_id = media.line_id
        try:
            gate = await asyncio.wait_for(self._gate(line_id), GATE_S)
        except Exception as exc:
            # Spec 5.11: the pre-loaded `busy` if this process holds the line's clips, else an immediate hang-up.
            # A gate past GATE_S (TimeoutError) is the same case: the line can't be checked.
            logger.warning("phone line %s: the line can't be checked at connect: the database or the model router "
                           "is unavailable (%s)", line_id, type(exc).__name__,
                           exc_info=None if isinstance(exc, _OUTAGES) else exc)
            await self._say_and_hang_up(live, self._first(self._clips.peek(line_id), "busy"))
            return
        line = gate.line
        if line is None:
            logger.info("phone line %s: no such line; hung up", line_id)
            await self._hang_up(live)
            return
        if gate.refusal is not None:
            await self._refuse(live, line, gate.clips, "closed", gate.refusal)
            return
        if self._stopping:
            # stop() began while the gate ran: the app is closing, so this caller is hung up, not refused `busy` by
            # caps that the calls still ending fill, and nothing is recorded.
            await self._hang_up(live)
            return
        if not self._take_slot(line):
            await self._refuse(live, line, gate.clips, "busy", "busy")
            return
        try:
            await self._call(media, live, gate)
        finally:
            self._free_slot(line)

    async def _gate(self, line_id: str) -> _Gate:
        """Steps 1 to 4 above. The reads share one short connection; the model checks run after it is back in the
        pool, so no connection waits on a provider (R10.11)."""
        async with db.get_pool().connection(timeout=DB_WAIT_S) as conn:
            line = await store.get_line(conn, line_id)
            if line is None:
                return _Gate(None)
            clips = await self._clips.get(conn, line)
            # R10.5: a line whose project is gone is closed, whatever `enabled` says.
            if not line.enabled or line.project_id is None \
                    or await project_role(conn, line.service_user_id, line.project_id) is None:
                return _Gate(line, "closed", clips)
            budget = model_router.get_config().daily_token_budget
            if await inference_budget.over_budget(conn, line.service_user_id, budget):
                return _Gate(line, "budget", clips)
            models = await model_settings.settings_or_env(conn)
            options = await chat_options.load_chat_options(conn)
        # R10.10. A router that hasn't started raises RuntimeError here: like the database, the line can't be checked,
        # so that is the "unavailable at connect" path, not a refusal. Named, so the log can tell it from a bug's.
        try:
            router = get_router()
        except RuntimeError as exc:
            raise RouterNotStarted(str(exc)) from exc
        if not model_settings.model_offered(models, line.model_id, router) \
                or not await self._listed(line, router.canonical_id(line.model_id), router):
            logger.info("phone line %s: its model %s may no longer answer; closed", line.id, line.model_id)
            return _Gate(line, "closed", clips)
        return _Gate(line, None, clips, options, budget)

    async def _listed(self, line: Line, model: str, router) -> bool:
        """Whether the provider still has `model` (model_refusal's last rule), waiting at most MODEL_CHECK_S. Past
        that the call goes through, as the router lets through a provider it can't reach. The request isn't
        cancelled by the bound: one per model runs at a time in this process, every call waits on it under its own
        bound, and a slow provider's answer still lands in the router's cache for the next call."""
        task = self._listing.get(model)
        if task is None:
            task = asyncio.ensure_future(router.is_listed(model))
            self._listing[model] = task
            task.add_done_callback(lambda done: self._listed_done(model, done))
        try:
            return await asyncio.wait_for(asyncio.shield(task), MODEL_CHECK_S)
        except asyncio.TimeoutError:
            logger.info("phone line %s: the provider didn't confirm model %s within %.1f s; the call goes through",
                        line.id, model, MODEL_CHECK_S)
            return True
        except asyncio.CancelledError:
            if task.cancelled() and not asyncio.current_task().cancelling():
                return True              # stop() cancelled the shared request; this call is being hung up anyway
            raise
        except Exception as exc:
            logger.warning("phone line %s: checking model %s failed (%s); the call goes through", line.id, model,
                           type(exc).__name__)
            return True

    def _listed_done(self, model: str, task: asyncio.Task) -> None:
        if self._listing.get(model) is task:
            del self._listing[model]
        if not task.cancelled():
            task.exception()             # read, so a failure nobody awaited isn't reported as never retrieved

    def _take_slot(self, line: Line) -> bool:
        """Step 5: count the call against both caps, or refuse it. No await between the check and the count."""
        if self._active >= self._config().max_calls or self._per_line[line.id] >= line.max_calls:
            return False
        self._active += 1
        self._per_line[line.id] += 1
        return True

    def _free_slot(self, line: Line) -> None:
        self._active -= 1
        self._per_line[line.id] -= 1
        if self._per_line[line.id] <= 0:
            del self._per_line[line.id]

    async def _call(self, media: CallMedia, live: _Live, gate: _Gate) -> None:
        line = gate.line
        if self._stopping:
            await self._hang_up(live)
            return
        session_id = new_session_id()
        try:
            async with db.get_pool().connection(timeout=DB_WAIT_S) as conn:
                call_id = await store.open_call(conn, line, session_id=session_id, now_ms=chat_store.now_ms())
        except Exception as exc:
            logger.warning("phone line %s: the call couldn't be opened (%s)", line.id, type(exc).__name__)
            await self._say_and_hang_up(live, self._first(gate.clips, "busy"))
            return
        try:
            await live.quiet()                   # the session's own pacer takes over from the keep-alive
            outcome = await CallSession(media, line, gate.clips, session_id, self._deps(line, gate)).run()
        except Exception:
            logger.exception("phone call %s: the call session failed", call_id)
            outcome = CallOutcome("error", 0, [])
        reason = "shutdown" if live.shutdown else outcome.end_reason
        try:
            async with db.get_pool().connection(timeout=DB_WAIT_S) as conn:
                await store.close_call(conn, call_id, end_reason=reason, turns=outcome.turns, stats=outcome.stats)
        except Exception as exc:
            logger.warning("phone call %s: its end wasn't recorded (%s); it stays open until the listener next "
                           "starts", call_id, type(exc).__name__)

    def _deps(self, line: Line, gate: _Gate) -> CallDeps:
        """R10.9: the recognizer, the chat engine and the voice a call uses, as Talk and post_turn use them."""
        options, budget = gate.options, gate.budget

        async def transcribe(wav: bytes, key: str):
            # The engine the deployment chose for the line's language (as Talk's route picks it), and the per-call
            # key, so two calls on one line are heard at once.
            engine = speech_service.engine_for(options, LANGUAGE)
            return await speech_service.transcribe_for(key, wav, "audio/wav", LANGUAGE, engine=engine)

        def run(req, claim):
            return run_turn(req, claim, router=get_router(), cfg=get_chat_config(), deployment_budget=budget)

        async def render(text: str) -> bytes:
            return await voice.render(text, line.voice)

        loop = asyncio.get_running_loop()
        return CallDeps(transcribe=transcribe, begin=chat_store.begin_turn, run=run, render=render, clock=loop.time,
                        sleep=asyncio.sleep, settings=window.settings(), cfg=self._config(), hearing=Hearing)

    # --- refusals ---

    async def _refuse(self, live: _Live, line: Line, clips: dict | None, clip: str, reason: str) -> None:
        """R10.1: the clip, the hang-up, then the record."""
        await self._say_and_hang_up(live, self._first(clips, clip))
        try:
            async with db.get_pool().connection(timeout=DB_WAIT_S) as conn:
                await store.record_refused_call(conn, line.id, reason)
        except Exception as exc:
            logger.warning("phone line %s: a refused call (%s) wasn't recorded (%s)", line.id, reason,
                           type(exc).__name__)

    @staticmethod
    def _first(clips: dict | None, kind: str) -> bytes | None:
        pcms = (clips or {}).get(kind) or []
        return pcms[0] if pcms else None

    async def _say_and_hang_up(self, live: _Live, pcm: bytes | None) -> None:
        """Play one clip at the phone's pace, then hang up. The keep-alive has stopped before the clip's pacer
        starts, and the clip's pacer before the hang-up, so no audio follows the 0x00 (R9.6). A caller who hangs up
        first ends it sooner; the wait is bounded by the clip."""
        media = live.media
        await live.quiet()
        if pcm:
            loop = asyncio.get_running_loop()
            played = asyncio.Event()
            pacer = Pacer(media.send, loop.time, asyncio.sleep)
            pacer.on_drained(played.set)
            pacer.play(pcm)
            sending = asyncio.ensure_future(pacer.run())
            waiting = asyncio.ensure_future(played.wait())
            try:
                await asyncio.wait({sending, waiting}, timeout=len(pcm) / 16000 + 1.0,
                                   return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in (sending, waiting):
                    task.cancel()
                await asyncio.gather(sending, waiting, return_exceptions=True)
            if not sending.cancelled() and not isinstance(sending.exception(), (ConnectionError, type(None))):
                logger.warning("phone line %s: playing a clip failed (%s)", media.line_id,
                               type(sending.exception()).__name__)
        await self._hang_up(live)

    @staticmethod
    async def _hang_up(live: _Live) -> None:
        """The keep-alive stops first, so nothing follows the 0x00."""
        await live.quiet()
        try:
            await live.media.hang_up()
        except Exception as exc:
            logger.warning("phone line %s: hanging up failed (%s)", live.media.line_id, type(exc).__name__)


_service = PhoneService()


async def start() -> None:
    await _service.start()


async def stop() -> None:
    await _service.stop()


async def handle(media: CallMedia) -> None:
    await _service.handle(media)


def status() -> dict:
    return _service.status()
