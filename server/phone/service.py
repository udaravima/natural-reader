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
most DB_WAIT_S, and the whole gate, then `open_call`, are each bounded at GATE_S (a stalled query is the database-down
path). Asterisk drops a call that gets no audio from the app for 2 s, so silence goes down the line from connect (a
keep-alive pacer with nothing queued) until a clip or the call session's own pacer takes over; it stops before either
starts, and before any hang-up, so two pacers never run at once and nothing follows the 0x00.

A call is ended from outside only through its media's `hang_up()`, which leads to the session's own end (R10.7): its
`run()` task is never cancelled.

While the listener is on, the open lines' models stay loaded (R17.1 to R17.6), so a first answer never waits for a
model to load: a refresher loads each one when the listener starts and every REFRESH_S after. Each call let in starts,
without waiting for it, a read of its line's prompt (R19.2): the system message and the tools of the first turn,
built by the chat engine's own code, which the server reads into its cache (loading the model if it must) while the
welcome plays, so the first answer reads only the caller's words and what follows. The call's end gives the read up.
Loads and reads ask for what the call's turns will (the keep-alive PHONE_MODEL_KEEP_ALIVE within
INFERENCE_KEEP_ALIVE_MAX, and the turn's own context size), so they never make Ollama reload the model for a turn.
Only a self-hosted model is loaded or read: a hosted one (Gemini, an OpenAI-compatible API, an Ollama cloud model,
ollama.com) gets no request. The refresher doesn't fight (R17.8, R17.9, `_refresh`): a model
loaded at someone else's context size is left alone; while a call is live, a model that isn't in memory is loaded
only if a live call uses it (a model already loaded at the phone's size is still refreshed); and a model pushed out
by a sibling in the same refresh (the lines' models don't fit together) isn't refreshed again until the listener
restarts, while calls still load it. The router has no per-model limit on requests in flight (and its HTTP
client allows 100 connections), so a load never takes a slot a call's turn needs here.

Logs carry ids, durations and end reasons, never what anyone said."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import secrets
import string
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import psycopg

from .. import db
from ..auth.authz import project_role
from ..chat import store as chat_store
from ..chat.config import get_chat_config
from ..chat.orchestrator import first_request, run_turn
from ..llm.router import get_router
from ..llm.types import CallSettings, ProviderError, ProviderTimeout, ProviderUnavailable
from ..services import chat_options, inference_budget, inference_limits, model_router, model_settings
from ..speech import service as speech_service, window
from . import audiosocket, store, vad, voice
from .call import CallDeps, CallOutcome, CallSession, Hearing, model_keep_alive, phone_settings, turn_request
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
GATE_S = 3.0               # longest the connect-time checks, and then open_call, may each take; then the db-down path
STOP_S = 5.0               # how long stop() waits for the calls it hung up to finish their run() and close_call
CLOSE_S = 1.0              # then for the server's last connections to close, before they are aborted
# Seconds between the refresher's ticks (R17.1). A chat request that sends no keep_alive resets a shared model's
# timer to Ollama's 5 min default, so loading every 2 min keeps a line's model loaded whatever the chat does.
REFRESH_S = 120.0
LOAD_WAIT_S = 60.0         # longest a tick waits for its loads; one still running is joined by the next tick
PS_WAIT_S = 5.0            # longest a tick waits to learn what the server holds in memory (R17.5)
# The shortest keep-alive the refresher can work with: a model loaded at one tick must still be loaded at the next,
# which comes REFRESH_S after the tick ends, and a tick may itself take LOAD_WAIT_S and more. Shorter, every tick
# would reload the model, so calls alone load it.
MIN_KEEP_S = int(2 * REFRESH_S)
_ID_CHARS = string.digits + string.ascii_lowercase


class RouterNotStarted(RuntimeError):
    """The model router hasn't started (or has stopped): like the database being away, the line can't be checked."""


# What "the line can't be checked" may be without a bug: the database or its pool (psycopg's errors, PoolTimeout
# and PoolClosed among them, or no pool at all), the network (OSError, which covers the GATE_S timeout), or the
# router not started. These are logged by their type alone; anything else gets its traceback.
_OUTAGES = (psycopg.Error, db.DatabaseUnavailable, OSError, RouterNotStarted)
# What a load that fails may be without a bug: the server can't be reached, refused, or went silent. The first, the
# third and OSError are the server being away (an outage of its provider); a refusal is the model's own (a 404).
_LOAD_OUTAGES = (ProviderUnavailable, ProviderError, ProviderTimeout, OSError)
_UNREACHABLE = (ProviderUnavailable, ProviderTimeout, OSError)


def _provider(model: str) -> str:
    """The provider of a canonical model id (`provider:name`)."""
    return model.partition(":")[0]


def _in_memory(held: dict[str, int | None] | None, model: str) -> bool | None:
    """Whether `model` is in one server's /api/ps map; None when the server couldn't say."""
    if held is None:
        return None
    return any(model_settings.same_model(model, other) for other in held)


def _size_in_memory(held: dict[str, int | None] | None, model: str) -> int | None:
    """The context size `model` is loaded with, by one server's /api/ps map; None when it isn't loaded, the server
    couldn't say, or Ollama reported no size."""
    for other, size in (held or {}).items():
        if model_settings.same_model(model, other):
            return size
    return None


def _router():
    """The model router. One that hasn't started raises RouterNotStarted: like the database being away, nothing can be
    checked or loaded, and the log can tell that from a bug."""
    try:
        return get_router()
    except RuntimeError as exc:
        raise RouterNotStarted(str(exc)) from exc


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
    def __init__(self, cfg: PhoneConfig | None = None, *, refresh_sleep=asyncio.sleep) -> None:
        """`cfg` None: read PHONE_* from the environment at each start(). `refresh_sleep` is the refresher's wait
        between ticks (tests pass a fake clock)."""
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
        # Keeping the lines' models loaded (R17.1 to R17.8)
        self._refresh_sleep = refresh_sleep
        self._refreshing: asyncio.Task | None = None   # the refresher, started with the listener
        self._loading: dict[str, asyncio.Task] = {}   # canonical model id -> its load request in flight
        self._reading: set[asyncio.Task] = set()       # the calls' reads of their line's prompt still running (R19.2)
        self._call_models: Counter[str] = Counter()    # the model ids of the calls let in, while they last
        self._unkept: set[str] = set()                 # pushed out by a sibling: not refreshed until restart
        self._ours: dict[str, int] = {}                # model -> the size /api/ps reported after the phone's load
        self._left_alone: set[str] = set()             # models rule (b) has left alone (logged once each)
        self._failing: set[str] = set()                # outages warned about: a provider's, or a model's (R17.8)
        self._refresh_failed = False                   # the last tick couldn't read the lines (one warning per outage)

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
        self._start_refresher(cfg)

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

    # --- keeping the lines' models loaded (R17.1 to R17.8) ---

    def _start_refresher(self, cfg: PhoneConfig) -> None:
        """Start the refresher with the listener, afresh: a model it stopped refreshing is refreshed again. There is
        none when the keep-alive is 0 (PHONE_MODEL_KEEP_ALIVE=0, or a cap of 0), or too short to last from one tick
        to the next (MIN_KEEP_S): every tick would then reload the model, so only calls load it, as they connect."""
        self._unkept, self._failing, self._refresh_failed = set(), set(), False
        self._ours, self._left_alone = {}, set()
        keep = model_keep_alive(cfg)
        if keep is None:
            if cfg.model_keep_alive_s:          # not switched off by its own setting: the cap is 0 (R17.8)
                logger.warning("The phone lines' models aren't kept loaded: INFERENCE_KEEP_ALIVE_MAX is 0, which "
                               "unloads a model after each use, so PHONE_MODEL_KEEP_ALIVE has no effect and the phone "
                               "sends no keep-alive and no load request.")
            return
        if 0 < keep < MIN_KEEP_S:
            logger.warning("The phone lines' models aren't kept loaded between calls: their keep-alive is %d s "
                           "(PHONE_MODEL_KEEP_ALIVE, within INFERENCE_KEEP_ALIVE_MAX), under the %d s a model must "
                           "last from one refresh to the next. Calls still load their model as they connect.",
                           keep, MIN_KEEP_S)
            return
        self._refreshing = asyncio.ensure_future(self._keep_models())

    async def _keep_models(self) -> None:
        """The refresher: a tick now and every REFRESH_S after, until stop() cancels it. A tick that can't read the
        lines (the database, the router) is one warning per outage, and the next tick tries again. It never raises
        into the service (R17.6)."""
        while True:
            try:
                await self._refresh()
            except Exception as exc:
                if not self._refresh_failed:
                    logger.warning("The phone lines' models weren't refreshed (%s); the refresher tries again every "
                                   "%d s", type(exc).__name__, REFRESH_S,
                                   exc_info=None if isinstance(exc, _OUTAGES) else exc)
                self._refresh_failed = True
            else:
                self._refresh_failed = False
            await self._refresh_sleep(REFRESH_S)

    async def _refresh(self) -> None:
        """One tick (R17.8, R17.9: "don't fight"). It reads the models of the lines a call could reach (one query,
        bounded by GATE_S), and keeps those the gate would offer (`model_offered`, and the provider still lists them)
        that run on a self-hosted server (`Router.holds_models`).

        Then, per model, by what its server holds now (/api/ps):
        (a) not loaded, or the server can't say: load it. A restart, `ollama stop`, a crash or a short server
            keep-alive is what refreshing is for, never a reason to give up. But while any call is live, only a model
            a live call uses: loading another could push the live call's model out of memory;
        (b) loaded at another context size than the phone's: someone is using it at theirs, so it is left alone this
            tick (a call on the line reloads it at the phone's size, overlapped by the welcome). Logged at INFO once
            per model per listener start. The phone's size is the `num_ctx` it asks for, or the size /api/ps
            reported after its own load (`_load_and_note`), whichever the server shows;
        (c) loaded at the phone's size: load it again, which restarts its keep-alive. That allocates nothing, so it
            runs for every line, live call or not.
        Loads ask for what the calls' turns will (R17.2). The tick waits at most LOAD_WAIT_S for them; one still
        running is joined by the next tick, never sent twice.

        Last, the sibling check: a model this tick loaded that is already gone, while another model this tick loaded
        on the same server is there, was pushed out by it, so the lines' models don't fit together. It isn't
        refreshed again until the listener restarts. Nothing else stops the refresh."""
        model_ids, models = await asyncio.wait_for(self._open_models(), GATE_S)
        router = _router()
        settings, limits = phone_settings(self._config()), inference_limits.get_limits()
        in_calls = self._call_model_ids(router) if self._active else None
        wanted: dict[str, CallSettings] = {}
        for model_id in model_ids:
            if not model_settings.model_offered(models, model_id, router) or not router.holds_models(model_id):
                continue
            model = router.canonical_id(model_id)
            if model in wanted or model in self._unkept:
                continue
            if await self._still_listed(router, model):
                wanted[model] = model_settings.sized_settings(models, router, model_id, settings, limits)
        held = await self._held(router, wanted)
        loads: dict[str, asyncio.Task] = {}
        for model, asked in wanted.items():
            now = held.get(_provider(model))
            size = _size_in_memory(now, model)
            if size is not None and asked.num_ctx is not None and size not in (asked.num_ctx, self._ours.get(model)):
                if model not in self._left_alone:                                  # (b)
                    self._left_alone.add(model)
                    logger.info("Phone lines: model %s is loaded at another context size (%d; the phone's is %d), "
                                "so someone else is using it: it is left alone, and a call on its line reloads it "
                                "at the phone's size", model, size, asked.num_ctx)
                continue
            if not _in_memory(now, model) and in_calls is not None and model not in in_calls:
                continue                 # (a) while a call is live: only the live calls' models (R17.9)
            loads[model] = self._load(router, model, asked)                       # (a), or (c)
        if not loads:
            return
        await asyncio.wait(loads.values(), timeout=LOAD_WAIT_S)
        loaded = sorted(model for model, task in loads.items()
                        if task.done() and not task.cancelled() and task.exception() is None and task.result())
        if len(loaded) < 2:
            return                       # a model can't be pushed out by a sibling it doesn't have
        after = await self._held(router, loaded)
        for model in loaded:
            now = after.get(_provider(model))
            if _in_memory(now, model) is not False:
                continue                 # still loaded, or the server can't say
            beside = [other for other in loaded if other != model and _provider(other) == _provider(model)
                      and _in_memory(now, other)]
            if beside:
                self._unkept.add(model)
                logger.warning("Phone lines: model %s was pushed out of memory by %s, loaded in the same refresh: "
                               "the lines' models don't fit in memory together. It isn't refreshed again until the "
                               "listener restarts; a call on its line still loads it as the call connects.",
                               model, ", ".join(beside))

    @staticmethod
    async def _open_models() -> tuple[list[str], model_settings.ModelsSettings]:
        async with db.get_pool().connection(timeout=DB_WAIT_S) as conn:
            return await store.open_line_models(conn), await model_settings.settings_or_env(conn)

    @staticmethod
    async def _still_listed(router, model: str) -> bool:
        """Whether the provider still lists `model` (the gate's last model check), within PS_WAIT_S. A provider that
        can't say counts as listing it, as the gate lets a call through then: the load fails instead, and that is
        warned about once per outage (`_loaded`), not on every tick."""
        try:
            return await asyncio.wait_for(router.has_model(model), PS_WAIT_S)
        except Exception as exc:  # noqa: BLE001 — not knowing only means trying the load
            logger.debug("phone lines: whether the provider still lists %s is unknown (%s)", model,
                         type(exc).__name__)
            return True

    @staticmethod
    async def _held(router, models) -> dict[str, dict[str, int | None] | None]:
        """What each server holds in memory now, by provider name: {canonical id: context size}, or None when it
        can't say. One /api/ps per server, each bounded by PS_WAIT_S."""
        held: dict[str, dict[str, int | None] | None] = {}
        for model in models:
            provider = _provider(model)
            if provider in held:
                continue
            try:
                held[provider] = await asyncio.wait_for(router.in_memory(model), PS_WAIT_S)
            except Exception as exc:  # noqa: BLE001 — not knowing only means loading as usual
                logger.debug("phone lines: what %s holds in memory is unknown (%s)", provider, type(exc).__name__)
                held[provider] = None
        return held

    def _call_model_ids(self, router) -> set[str]:
        """The canonical ids of the models live calls answer with."""
        out = set()
        for model_id in self._call_models:
            with contextlib.suppress(Exception):      # a provider removed since the call began
                out.add(router.canonical_id(model_id))
        return out

    def _load(self, router, model: str, settings: CallSettings) -> asyncio.Task:
        """Load `model` (a canonical id), or join the load already in flight for it: one per model in this process.
        Its outcome is noted by `_loaded`; stop() cancels one still running."""
        task = self._loading.get(model)
        if task is None:
            task = asyncio.ensure_future(self._load_and_note(router, model, lambda: router.load(model, settings)))
            self._loading[model] = task
            task.add_done_callback(lambda done: self._loaded(model, done))
        return task

    async def _load_and_note(self, router, model: str, load: Callable[[], Awaitable[bool]]) -> bool:
        """`load()`, then the context size /api/ps reports for the model right after it. From then on that size is
        the phone's own for rule (b) (R17.9): Ollama may report another size than the `num_ctx` asked (with
        OLLAMA_NUM_PARALLEL above 1, for example), and the phone must never take its own copy for someone else's.
        Every phone load comes here: the refresher's, and a call's read of its line's prompt, which loads the model
        too. Returns the load's own answer."""
        loaded = await load()
        if loaded:
            size = _size_in_memory((await self._held(router, [model])).get(_provider(model)), model)
            if size is not None:
                self._ours[model] = size
        return loaded

    def _first_failure(self, model: str, exc: BaseException | None) -> bool:
        """R17.6, R17.8: one warning per outage and a silent recovery, for the refresher's loads and the calls' reads
        alike. A server that can't be reached is an outage of its provider (its name is the id's prefix), whatever
        model hit it first; anything else (a model the server refuses, a 404, say; the database during a read) is that
        model's alone, so another model loading fine doesn't make it warn again. Returns whether `exc` begins an outage
        (the caller warns); a success (None) ends any outage of the model and its provider."""
        provider = _provider(model)
        if exc is None:
            self._failing -= {provider, model}
            return False
        key = provider if isinstance(exc, _UNREACHABLE) else model
        if key in self._failing:
            return False
        self._failing.add(key)
        return True

    def _loaded(self, model: str, task: asyncio.Task) -> None:
        """A load's end. Reading the exception here also keeps one nobody awaited from being reported as never
        retrieved."""
        if self._loading.get(model) is task:
            del self._loading[model]
        if task.cancelled():
            return
        exc = task.exception()
        if self._first_failure(model, exc):
            logger.warning("Phone lines: model %s couldn't be loaded (%s); the refresher keeps trying, and a call "
                           "loads it as the call connects", model, type(exc).__name__,
                           exc_info=None if isinstance(exc, _LOAD_OUTAGES) else exc)

    def _read_ahead(self, gate: _Gate, session_id: str) -> asyncio.Task | None:
        """R19.2: as a call is let in, have the model's server read the line's prompt (the system message and the
        tools, exactly as the call's first turn will send them) while the welcome plays and the caller asks. Read
        cold, a line's prompt (about 1,300 tokens before the caller's words) took about 15 s on a CPU-only machine;
        the first turn then reads only what follows it.
        The read loads the model too (R17.3), with the turn's own options, and asks for one token, thrown away.

        Same gates as a load: nothing when the keep-alive is off (PHONE_MODEL_KEEP_ALIVE=0, or a cap of 0), and
        nothing for a model that isn't self-hosted. It is never awaited here and never delays, fails or ends the call;
        the call's end gives it up (`_give_up`), and so does stop(). Each call reads its own: two lines on one model
        have different prompts. Returns the read's task, or None."""
        cfg, line = self._config(), gate.line
        if model_keep_alive(cfg) is None:
            return None
        try:
            router = _router()
            if not router.holds_models(line.model_id):
                return None
            model = router.canonical_id(line.model_id)
        except Exception as exc:  # noqa: BLE001 — the call goes on without it
            logger.warning("phone line %s: its prompt wasn't read ahead of the first question (%s)", line.id,
                           type(exc).__name__, exc_info=None if isinstance(exc, _OUTAGES) else exc)
            return None
        req = turn_request(line, session_id, cfg, "")
        task = asyncio.ensure_future(self._load_and_note(router, model, lambda: self._read_prompt(router, req)))
        self._reading.add(task)
        task.add_done_callback(lambda done: self._read(model, line.id, done))
        return task

    @staticmethod
    async def _read_prompt(router, req) -> bool:
        """The read itself: the first turn's request built by the chat engine's own code, with no words yet
        (`first_request`), sent through the LLM layer, which generates one token and throws it away."""
        loop = asyncio.get_running_loop()
        started = loop.time()
        first = await first_request(req, router=router, cfg=get_chat_config())
        read = await router.read_prompt(req.model_id, first.messages, first.tools, first.settings)
        if read:
            logger.info("phone call %s: its line's prompt was read ahead in %.1f s", req.session_id,
                        loop.time() - started)
        return read

    def _read(self, model: str, line_id: str, task: asyncio.Task) -> None:
        """A read's end: a given-up one is the call ending; a failure is one warning per outage (`_first_failure`)."""
        self._reading.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if self._first_failure(model, exc):
            logger.warning("phone line %s: model %s didn't read the line's prompt ahead of the first question (%s); "
                           "the first answer reads it instead", line_id, model, type(exc).__name__,
                           exc_info=None if isinstance(exc, (*_LOAD_OUTAGES, *_OUTAGES)) else exc)

    @staticmethod
    async def _give_up(task: asyncio.Task | None) -> None:
        """The call is over: a read of its line's prompt still running helps no one, and costs the server its CPU."""
        if task is None or task.done():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

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
        # A provider-list request that outlived its calls' bound (R10.11), a warm-up still reading the options, the
        # refresher, any load still running (R17.1), and a read of a line's prompt whose call didn't end in time
        # (R19.2): nothing waits for them any more.
        warming, self._warming = self._warming, None
        refreshing, self._refreshing = self._refreshing, None
        leftovers = [*self._listing.values(), *self._loading.values(), *self._reading,
                     *(task for task in (warming, refreshing) if task is not None)]
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
        session_id = new_session_id()
        reading = self._read_ahead(gate, session_id)
        try:
            await self._call(media, live, gate, session_id)
        finally:
            try:
                await self._give_up(reading)
            finally:
                self._free_slot(line)        # even if this task is cancelled while the read goes

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
        # R10.10. A router that hasn't started raises RouterNotStarted here: like the database, the line can't be
        # checked, so that is the "unavailable at connect" path, not a refusal.
        router = _router()
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
        self._call_models[line.model_id] += 1      # the refresher won't push a live call's model out (R17.8)
        return True

    def _free_slot(self, line: Line) -> None:
        self._active -= 1
        self._per_line[line.id] -= 1
        if self._per_line[line.id] <= 0:
            del self._per_line[line.id]
        self._call_models[line.model_id] -= 1
        if self._call_models[line.model_id] <= 0:
            del self._call_models[line.model_id]

    async def _call(self, media: CallMedia, live: _Live, gate: _Gate, session_id: str) -> None:
        line = gate.line
        if self._stopping:
            await self._hang_up(live)
            return
        try:
            # Bounded like the gate: the keep-alive holds the caller on the line, so a stalled INSERT (a lock wait, a
            # stuck server) would otherwise be silence until they hang up. Past GATE_S (TimeoutError) it is the
            # database-down path.
            call_id = await asyncio.wait_for(self._open_call(line, session_id), GATE_S)
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

    @staticmethod
    async def _open_call(line: Line, session_id: str) -> str:
        """The call's row and its chat, on a short connection of their own (R10.4)."""
        async with db.get_pool().connection(timeout=DB_WAIT_S) as conn:
            return await store.open_call(conn, line, session_id=session_id, now_ms=chat_store.now_ms())

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
