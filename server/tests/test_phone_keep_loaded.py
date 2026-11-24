"""Keeping a phone line's model loaded (R17.1 to R17.8, server/phone/service.py): the refresher that loads the open
lines' models when the listener starts and every REFRESH_S after, without fighting anyone for memory; the read of the
line's prompt each accepted call starts, which loads the model too (R19.2); and how both stay out of a call's way.

The refresher's sleep is a fake clock (`Ticks`): a tick happens only when the test says so. Most tests put a real
Router in front of a fake local Ollama (`FakeOllama`, its HTTP only), beside two hosted APIs that must never be asked
anything: Gemini, and Ollama's own cloud at ollama.com. Everything runs on a scratch database of this module's own,
emptied of lines before each test, so "the lines that are on" are exactly the test's own. Every service a test makes
is stopped when the test ends, whatever happened (`phone`)."""
import asyncio
import json
import logging
import re
import secrets
from dataclasses import replace

import httpx
import psycopg
import pytest

from server.auth.users import SEED_ADMIN_ID
from server.chat import context as chat_context
from server.llm import router as llm_router
from server.phone import service, store, vad
from server.phone.config import PhoneConfig
from server.services import console_settings, model_settings
from server.tests import seed
from server.tests.llm_fakes import ndjson
from server.tests.test_phone_service import (  # noqa: F401 (the fixtures are used by name)
    CFG, END, GREETED, GREETING, LOOK_IT_UP, QUESTION, START, VOICE, FakeMedia, axis, calls_of, clip_rows, ears,
    make_line_with_hours, messages_of, pool, real_call, router, scratch_url, sessions, until)
from server.tests.test_project_members import _p, _user

LISTENING = PhoneConfig(listen="127.0.0.1:0", allow=(), pbx_address="127.0.0.1:0")
OURS = 16384            # the context size a phone turn sends for a self-hosted model (model_settings' default)


@pytest.fixture(autouse=True)
async def only_this_tests_lines(pool, monkeypatch):
    """No lines but the test's own; the deployment's default limits; a VAD that loads nothing."""
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM phone_lines")
    for name in ("INFERENCE_KEEP_ALIVE_MAX", "INFERENCE_NUM_CTX_MAX", "INFERENCE_LOCAL_NUM_CTX"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(vad, "_session", lambda: None)


class Ticks:
    """The refresher's clock. Each sleep waits until the test calls `tick()`; `slept` holds what each one asked for."""

    def __init__(self) -> None:
        self.slept: list[float] = []
        self._asleep = asyncio.Event()
        self._wake = asyncio.Event()

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self._wake.clear()
        self._asleep.set()
        await self._wake.wait()

    async def first(self) -> None:
        """Wait until the tick at start is done and the refresher sleeps."""
        await asyncio.wait_for(self._asleep.wait(), 5)

    async def tick(self) -> None:
        """End the refresher's sleep, and wait until the tick after it is done."""
        await self.first()
        self._asleep.clear()
        self._wake.set()
        await asyncio.wait_for(self._asleep.wait(), 5)


@pytest.fixture
async def phone(pool):
    """Makes the services a test uses, and stops each one when the test ends, whatever happened."""
    made: list[service.PhoneService] = []

    def make(cfg: PhoneConfig = LISTENING, ticks: Ticks | None = None) -> service.PhoneService:
        svc = service.PhoneService(cfg, refresh_sleep=ticks.sleep) if ticks else service.PhoneService(cfg)
        made.append(svc)
        return svc
    yield make
    for svc in made:
        await asyncio.wait_for(svc.stop(), 5)


class FakeOllama:
    """A local Ollama's HTTP, as a real Router reaches it.
    - A load request (a chat with no messages) loads its model at the request's context size (the server's default
      when it names none: 128000 here, as on a server whose OLLAMA_CONTEXT_LENGTH is 128000); past `room` models the
      one loaded longest ago is pushed out, as Ollama does when memory runs short. A model in `missing` is listed
      but refuses to load (a 404).
    - A pre-read (a chat with messages that waits for the whole answer: stream false, R19.2) loads its model the same
      way, and answers with one token.
    - A chat with messages that streams (a turn) is answered with one streamed sentence.
    - /api/ps lists what is loaded, with each model's context size, times `parallel` (an Ollama may report the size
      for all its parallel slots); /api/tags what is installed; /api/show a model's capabilities.
    - `down` makes every request fail to connect, `ps_down` only /api/ps, `failing_loads` only the loads and
      pre-reads, and `held` (an Event) holds each load and pre-read until it is set; `cancelled` counts the held ones
      whose request was given up, and `slow_cancel` (seconds) makes such a request take that long to go. Requests to
      any other host are noted too, so a test can see nothing reached one."""
    TAGS = ("gemma4:e4b", "qwen3:8b", "llama3.2:3b", "test-model", "gpt-oss:120b-cloud")
    SHOW = {"capabilities": ["completion", "tools", "thinking"], "model_info": {"gemma3.context_length": 131072}}
    DEFAULT_CTX = 128000

    def __init__(self) -> None:
        self.room = 4
        self.parallel = 1
        self.loaded: dict[str, int] = {}       # name -> the context size /api/ps reports, oldest first
        self.missing: set[str] = set()
        self.requests: list[httpx.Request] = []
        self.down = False
        self.ps_down = False
        self.failing_loads = False
        self.held: asyncio.Event | None = None
        self.cancelled = 0
        self.slow_cancel = 0.0
        self.router: llm_router.Router | None = None

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if self.down or (self.failing_loads and path == "/api/chat") or (self.ps_down and path == "/api/ps"):
            raise httpx.ConnectError("Ollama is down")
        if path == "/api/ps":
            return httpx.Response(200, json={"models": [{"name": n, "model": n, "context_length": size}
                                                        for n, size in self.loaded.items()]})
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": n} for n in self.TAGS]})
        if path == "/api/show":
            return httpx.Response(200, json=self.SHOW)
        body = json.loads(request.content) if path == "/api/chat" else {}
        if path == "/api/chat" and body["messages"] and body.get("stream", True):
            return ndjson({"message": {"role": "assistant", "content": "We open at nine."}},
                          {"done": True, "done_reason": "stop", "prompt_eval_count": 10, "eval_count": 5})
        if path == "/api/chat":
            model = body["model"]
            if self.held is not None:
                try:
                    await self.held.wait()
                except asyncio.CancelledError:
                    self.cancelled += 1
                    if self.slow_cancel:
                        await asyncio.sleep(self.slow_cancel)
                    raise
            if model in self.missing:
                return httpx.Response(404, json={"error": f"model '{model}' not found"})
            self.loaded.pop(model, None)
            self.loaded[model] = (body.get("options") or {}).get("num_ctx", self.DEFAULT_CTX) * self.parallel
            while len(self.loaded) > self.room:
                del self.loaded[next(iter(self.loaded))]
            if body["messages"]:
                return httpx.Response(200, json={"model": model, "message": {"role": "assistant", "content": "We"},
                                                 "done": True, "done_reason": "length", "prompt_eval_count": 1000,
                                                 "eval_count": 1})
            return httpx.Response(200, json={"model": model, "done": True, "done_reason": "load"})
        return httpx.Response(404, json={"error": f"no route {path}"})

    def chats(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests if r.url.path == "/api/chat"]

    def loads(self) -> list[dict]:
        """The body of each request that loads a model without answering a caller, in the order they arrived: the
        refresher's loads, and each call's pre-read (R19.2), which loads the model as it reads the line's prompt."""
        return [body for body in self.chats() if not body["messages"] or body.get("stream") is False]

    def prereads(self) -> list[dict]:
        """The body of each call's pre-read of its line's prompt."""
        return [body for body in self.loads() if body["messages"]]

    def models(self) -> list[str]:
        return [body["model"] for body in self.loads()]

    def turns(self) -> list[dict]:
        """The body of each turn's request that carries the caller's question."""
        return [body for body in self.chats() if QUESTION in json.dumps(body["messages"])]


@pytest.fixture
async def ollama(monkeypatch):
    """The phone's router: a real one, with the fake local Ollama, a hosted Gemini and Ollama's cloud (ollama.com),
    the last two never to be reached."""
    fake = FakeOllama()
    fake.router = llm_router.Router(llm_router.load_provider_configs({
        "INFERENCE_PROVIDERS": "ollama,gemini,cloud",
        "INFERENCE_OLLAMA_KIND": "ollama", "INFERENCE_OLLAMA_URL": "http://ollama.test",
        "INFERENCE_GEMINI_KIND": "gemini", "INFERENCE_GEMINI_URL": "https://gemini.test",
        "INFERENCE_GEMINI_API_KEY": "test-key",
        "INFERENCE_CLOUD_KIND": "ollama", "INFERENCE_CLOUD_URL": "https://ollama.com",
        "INFERENCE_CLOUD_API_KEY": "test-key"}), httpx.AsyncClient(transport=httpx.MockTransport(fake.handle)))
    monkeypatch.setattr(service, "get_router", lambda: fake.router)
    yield fake
    await fake.router.aclose()


async def line(pool, model: str, *, on: bool = True):
    """A line answering with `model` on a project of its own, switched on unless `on` is False, with its clips."""
    async with pool.connection() as conn:
        owner = await _user(conn, f"owner-{secrets.token_hex(4)}")
        project = await seed.make_project(conn, owner)
        made = await store.create_line(conn, _p(owner), name="Front desk", profile_id=None, project_id=project,
                                       model_id=model, voice=VOICE, greeting=GREETING)
        if on:
            made = await store.update_line(conn, _p(owner), made.id, enabled=True)
        await store.save_clips(conn, made.id, clip_rows())
    return made


def phone_tasks() -> list[asyncio.Task]:
    """The service's background work still pending: the refresher, and loads."""
    names = ("PhoneService._keep_models", "PhoneService._load_and_note")
    return [t for t in asyncio.all_tasks() if not t.done() and getattr(t.get_coro(), "__qualname__", "") in names]


async def answered(svc, media, sessions, count: int) -> None:
    """One call that goes through (a fake session), from connect to the caller's hang-up."""
    running = asyncio.create_task(svc.handle(media))
    await until(lambda: len(sessions) == count)
    media.caller_hangs_up()
    await asyncio.wait_for(running, 5)


def warnings_naming(caplog, text: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno == logging.WARNING and text in r.getMessage()]


# --- the refresher (R17.1) ---

async def test_start_loads_each_model_the_open_lines_answer_with_once(pool, ollama, phone):
    # Brief test 1. Two lines share a model (one names it without its provider), a third has another. A line that is
    # off gets nothing, and neither do hosted models (R17.8 I2): Gemini, an Ollama cloud model (`-cloud`) on the local
    # server, and ollama.com. A load is not a turn: it records no usage and no chat (M2: this test's lines only).
    made = [await line(pool, "ollama:gemma4:e4b"), await line(pool, "gemma4:e4b"), await line(pool, "ollama:qwen3:8b"),
            await line(pool, "ollama:llama3.2:3b", on=False), await line(pool, "gemini:gemini-x-flash"),
            await line(pool, "ollama:gpt-oss:120b-cloud"), await line(pool, "cloud:gpt-oss:120b")]
    ticks = Ticks()
    svc = phone(ticks=ticks)
    await svc.start()
    await ticks.first()
    assert sorted(ollama.models()) == ["gemma4:e4b", "qwen3:8b"]
    assert all(body["keep_alive"] == 900 and body["messages"] == [] for body in ollama.loads())
    assert {r.url.host for r in ollama.requests} == {"ollama.test"}       # Gemini and ollama.com were never asked
    assert ticks.slept == [service.REFRESH_S] and service.REFRESH_S == 120
    users = [m.service_user_id for m in made]
    async with pool.connection() as conn:
        for table in ("inference_usage", "chat_sessions"):
            cur = await conn.execute(f"SELECT count(*) FROM {table} WHERE user_id = ANY(%s::uuid[])", (users,))
            assert (await cur.fetchone())[0] == 0, table


async def test_the_refresher_loads_again_each_period_and_follows_the_lines(pool, ollama, phone):
    # Brief test 2, and R17.8 (c). Every REFRESH_S it loads a model held at the phone's size again (so a chat's 5 min
    # default never lets it go), takes up a line switched on since, and drops a model once no open line uses it.
    first = await line(pool, "ollama:gemma4:e4b")
    ticks = Ticks()
    svc = phone(ticks=ticks)
    await svc.start()
    await ticks.first()
    assert ollama.models() == ["gemma4:e4b"] and ollama.loaded == {"gemma4:e4b": OURS}
    await line(pool, "ollama:qwen3:8b")
    await ticks.tick()
    assert sorted(ollama.models()[1:]) == ["gemma4:e4b", "qwen3:8b"]
    async with pool.connection() as conn:
        await conn.execute("UPDATE phone_lines SET enabled = false WHERE id = %s", (first.id,))
    await ticks.tick()
    assert ollama.models()[3:] == ["qwen3:8b"]
    assert ticks.slept == [service.REFRESH_S] * 3


async def test_keep_alive_0_loads_nothing_and_runs_no_refresher(pool, ollama, phone, sessions):
    # Brief test 5. PHONE_MODEL_KEEP_ALIVE=0 leaves the model to Ollama, as any chat's is: no load at start, no
    # refresher, no read of the line's prompt when a call connects (R19.2), and its turns send no keep_alive
    # (test_phone_call).
    made = await line(pool, "ollama:gemma4:e4b")
    ticks = Ticks()
    svc = phone(replace(LISTENING, model_keep_alive_s=0), ticks)
    await svc.start()
    await answered(svc, FakeMedia(made.id), sessions, 1)
    await asyncio.sleep(0.1)
    assert ollama.chats() == [] and ticks.slept == [] and phone_tasks() == []


async def test_a_keep_alive_cap_of_0_keeps_nothing_and_never_sends_keep_alive_0(pool, ollama, phone, sessions,
                                                                               monkeypatch, caplog):
    # R17.8 I1. INFERENCE_KEEP_ALIVE_MAX=0 caps every keep-alive at 0, which Ollama reads as "unload after every
    # answer": sent with each load and turn, it would unload the model after every reply. So a cap of 0 means "not
    # kept": no refresher (one warning naming the cap), no load when a call connects, and no keep_alive on a turn
    # (test_phone_call).
    monkeypatch.setenv("INFERENCE_KEEP_ALIVE_MAX", "0")
    made = await line(pool, "ollama:gemma4:e4b")
    ticks = Ticks()
    svc = phone(ticks=ticks)
    await svc.start()
    await answered(svc, FakeMedia(made.id), sessions, 1)
    await asyncio.sleep(0.1)
    assert ollama.chats() == [] and ticks.slept == [] and phone_tasks() == []
    [warning] = warnings_naming(caplog, "INFERENCE_KEEP_ALIVE_MAX is 0")
    assert "aren't kept loaded" in warning.getMessage()


async def test_a_keep_alive_too_short_to_bridge_two_ticks_turns_the_refresher_off(pool, ollama, phone, sessions,
                                                                                 monkeypatch, caplog):
    # A model refreshed every REFRESH_S must still be loaded at the next tick with time to spare, or every tick would
    # reload it. INFERENCE_KEEP_ALIVE_MAX of 3 min caps the keep-alive at 180 s: the refresher stays off and says why,
    # once. A call still loads its model (its read of the line's prompt, R19.2), under the capped keep-alive.
    monkeypatch.setenv("INFERENCE_KEEP_ALIVE_MAX", "3m")
    made = await line(pool, "ollama:gemma4:e4b")
    ticks = Ticks()
    svc = phone(ticks=ticks)
    await svc.start()
    await asyncio.sleep(0.1)
    assert ticks.slept == [] and ollama.loads() == []
    [warning] = warnings_naming(caplog, "PHONE_MODEL_KEEP_ALIVE")
    assert "180" in warning.getMessage() and "240" in warning.getMessage()
    await answered(svc, FakeMedia(made.id), sessions, 1)
    await until(lambda: len(ollama.loads()) == 1)
    assert ollama.loads()[0]["keep_alive"] == 180


# --- R19.2 (and R17.2): a call reads its line's prompt as its first turn will send it ---

async def test_a_calls_pre_read_is_its_first_turns_prefix_with_the_same_options(pool, real_call, ollama, phone,
                                                                                 monkeypatch):
    # R19.2. When a call is let in, the line's prompt is read ahead of the caller's first question, so Ollama has it
    # cached when the question comes: read cold, the line's prompt took about 22 s on a CPU-only machine. Ollama
    # caches by prefix, so the read must send what the first turn will, up to the caller's words. Both requests are
    # built by the real code (the gate and the read it starts; the CallSession, run_turn, the Router and the Ollama
    # adapter); the fakes are Ollama's HTTP and the question's embedding. They agree on the system message, the tools
    # (the phone's two, R19.1), the thinking switch, the keep-alive and every option, the context size included (R17.2:
    # a changed num_ctx would reload the model). The read waits for its answer and asks for one token. A context size
    # the console sets for the model moves both.
    async def embed(text: str) -> list[float]:
        return axis(0 if "Saturday" in text else 1)        # the question finds the line's passage
    monkeypatch.setattr(chat_context, "embed_query", embed)
    monkeypatch.setattr(service, "get_router", lambda: ollama.router)
    made = await make_line_with_hours(pool)
    svc = phone(CFG)

    async def one_call() -> tuple[dict, dict]:
        before = (len(ollama.prereads()), len(ollama.turns()))
        media = FakeMedia(made.id, say=[(0.3, START), (0.4, END)])
        running = asyncio.create_task(svc.handle(media))
        await until(lambda: len(ollama.turns()) > before[1] and len(media.heard()) >= 2)   # the greeting, the answer
        media.caller_hangs_up()
        await asyncio.wait_for(running, 5)
        [read] = ollama.prereads()[before[0]:]
        [turn] = ollama.turns()[before[1]:]
        return read, turn

    for size in (OURS, 8192):
        if size != OURS:
            async with pool.connection() as conn:
                await console_settings.save(conn, model_settings.SECTION,
                                            {"context_sizes": {"ollama:test-model": size}}, updated_by=SEED_ADMIN_ID)
        try:
            read, turn = await one_call()
        finally:
            async with pool.connection() as conn:
                await conn.execute("DELETE FROM app_settings WHERE key = %s", (model_settings.SECTION.key,))
        assert read["messages"][0] == turn["messages"][0] and read["tools"] == turn["tools"]
        assert [t["function"]["name"] for t in read["tools"]] == ["search_documents", "current_time"]
        assert read["messages"][1:] == [{"role": "user", "content": f"({LOOK_IT_UP})"}]   # no caller's words yet
        same = ("model", "think", "keep_alive")
        assert {k: read.get(k) for k in same} == {k: turn.get(k) for k in same} == {
            "model": "test-model", "think": False, "keep_alive": 900}
        assert turn["options"] == {"num_ctx": size} and read["options"] == {"num_ctx": size, "num_predict": 1}
        assert read["stream"] is False and turn["stream"] is True


async def test_a_pre_read_stores_nothing(pool, ollama, phone, sessions, caplog):
    # R19.2, as R17.6 for a load: reading the line's prompt is no turn, whatever it generates is thrown away. A call
    # whose prompt was read and that hangs up after the welcome leaves only what opening a call writes, its chat with
    # the greeting: no message, log line or usage for the read, and the chat's profile still unset (a turn sets it).
    # The backend's log says how long the read took, in the line the docs quote (R19.6, M9).
    caplog.set_level(logging.INFO, logger="server.phone.service")
    made = await make_line_with_hours(pool)
    svc = phone(CFG)
    media = FakeMedia(made.id)
    running = asyncio.create_task(svc.handle(media))
    await until(lambda: len(sessions) == 1 and len(ollama.prereads()) == 1 and phone_tasks() == [])
    media.caller_hangs_up()
    await asyncio.wait_for(running, 5)
    [call] = await calls_of(pool, made.id)
    assert await messages_of(pool, call["session_id"]) == [("assistant", GREETING, "complete")]
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT count(*) FROM chat_events WHERE session_id = %s", (call["session_id"],))
        events = (await cur.fetchone())[0]
        cur = await conn.execute("SELECT count(*) FROM inference_usage WHERE user_id = %s", (made.service_user_id,))
        usage = (await cur.fetchone())[0]
        cur = await conn.execute("SELECT profile_id FROM chat_sessions WHERE id = %s", (call["session_id"],))
        profile = (await cur.fetchone())[0]
    assert (events, usage, profile) == (0, 0, None)
    [said] = [r for r in caplog.records if "prompt was read ahead" in r.getMessage()]
    assert said.levelno == logging.INFO
    assert re.fullmatch(rf"phone call {call['session_id']}: its line's prompt was read ahead in \d+\.\d s",
                        said.getMessage())


# --- R19.2 (as R17.3): the read never holds up a call, and ends with it ---

async def test_a_stalled_pre_read_never_delays_the_welcome_and_ends_with_the_call(pool, real_call, ollama, phone,
                                                                                  monkeypatch):
    # Brief test 4. Ollama never finishes reading the line's prompt (a reload after an eviction, a stuck server): the
    # greeting starts at once all the same (under 1 s of the loop's clock after connect, with the read held for good),
    # and the call goes on. The caller's hang-up gives the read up, so nothing of it outlives the call.
    monkeypatch.setattr(service, "get_router", lambda: ollama.router)
    ollama.held = asyncio.Event()
    made = await line(pool, "ollama:gemma4:e4b")
    svc = phone(CFG)
    media = FakeMedia(made.id)
    loop = asyncio.get_running_loop()
    connected = loop.time()
    running = asyncio.create_task(svc.handle(media))
    await until(lambda: GREETED in media.heard() and len(ollama.prereads()) == 1)
    assert media.first_sound_at - connected < 1.0
    await asyncio.sleep(0.2)
    assert not running.done() and media.hang_ups == 0 and len(phone_tasks()) == 1     # the call carries on
    media.caller_hangs_up()
    await asyncio.wait_for(running, 5)
    assert phone_tasks() == [] and ollama.cancelled == 1
    assert [c["end_reason"] for c in await calls_of(pool, made.id)] == ["caller"]


async def test_stop_gives_up_a_pre_read_still_running(pool, real_call, ollama, phone, monkeypatch):
    # Brief test 4: the app stopping hangs the call up, and the read it started is given up with it.
    monkeypatch.setattr(service, "get_router", lambda: ollama.router)
    ollama.held = asyncio.Event()
    made = await line(pool, "ollama:gemma4:e4b")
    svc = phone(CFG)
    media = FakeMedia(made.id)
    running = asyncio.create_task(svc.handle(media))
    await until(lambda: GREETED in media.heard() and len(ollama.prereads()) == 1)
    await asyncio.wait_for(svc.stop(), 5)
    await asyncio.wait_for(running, 5)
    assert phone_tasks() == [] and ollama.cancelled == 1
    assert [c["end_reason"] for c in await calls_of(pool, made.id)] == ["shutdown"]


async def test_a_call_interrupted_while_it_gives_up_its_read_still_frees_its_place(pool, real_call, ollama, phone,
                                                                                  monkeypatch):
    # R19.6 (M2). A call's end gives its read up and waits for it to go. If the call's own task is cancelled during
    # that wait, its place must still be freed: otherwise the line would answer "busy" for good once its cap was
    # reached, and the refresher would count the call as live (R17.9) for good.
    monkeypatch.setattr(service, "get_router", lambda: ollama.router)
    ollama.held = asyncio.Event()
    ollama.slow_cancel = 1.0                           # the given-up read takes a second to go
    made = await line(pool, "ollama:gemma4:e4b")
    svc = phone(CFG)
    media = FakeMedia(made.id)
    running = asyncio.create_task(svc.handle(media))
    await until(lambda: GREETED in media.heard() and len(ollama.prereads()) == 1)
    assert svc.status()["active"] == 1
    media.caller_hangs_up()
    await until(lambda: ollama.cancelled == 1)        # the call is over, and waits for its read to go
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert svc.status()["active"] == 0 and not svc._call_models


async def test_a_read_whose_build_fails_warns_once_per_outage_and_sends_nothing(pool, ollama, phone, sessions,
                                                                                monkeypatch, caplog):
    # R19.5.4, R19.6 (M9). The read's own build can fail too: the database away while `first_request` reads the
    # line's settings. Every call still goes through, one warning covers the outage (ids and the error's type, no
    # traceback), and nothing is sent to Ollama instead (no bare load).
    built = []

    async def database_away(req, *, router, cfg):
        built.append(req.session_id)
        raise psycopg.OperationalError("the database is away")
    monkeypatch.setattr(service, "first_request", database_away)
    made = await line(pool, "ollama:gemma4:e4b")
    svc = phone(CFG)
    for count in (1, 2, 3):
        await answered(svc, FakeMedia(made.id), sessions, count)
        await until(lambda: phone_tasks() == [])
    assert len(built) == 3 and len(sessions) == 3                      # every call went through, and tried a read
    assert ollama.chats() == []
    [warning] = warnings_naming(caplog, "didn't read the line's prompt")
    assert warning.exc_info is None and "OperationalError" in warning.getMessage()


async def test_a_calls_read_teaches_the_refresher_the_phones_own_size(pool, ollama, phone, sessions, caplog):
    # R17.9's guard, reached through a call's read (R19.6, M9). An Ollama may report another context size than the
    # num_ctx asked (16384 x 2 here). The size /api/ps shows right after the call's read is the phone's from then on,
    # so the next tick refreshes that copy (c), and never leaves it alone as someone else's (b).
    caplog.set_level(logging.INFO, logger="server.phone.service")
    ollama.parallel = 2
    ticks = Ticks()
    svc = phone(ticks=ticks)
    await svc.start()
    await ticks.first()                                # no line yet: the first tick loads nothing
    made = await line(pool, "ollama:gemma4:e4b")
    media = FakeMedia(made.id)
    running = asyncio.create_task(svc.handle(media))
    await until(lambda: len(sessions) == 1 and len(ollama.prereads()) == 1 and len(phone_tasks()) == 1)
    media.caller_hangs_up()                            # the read is done (only the refresher is left)
    await asyncio.wait_for(running, 5)
    assert ollama.loaded == {"gemma4:e4b": 2 * OURS} and ollama.loads() == ollama.prereads()
    await ticks.tick()
    assert [r for r in caplog.records if "left alone" in r.getMessage()] == []
    assert [body["model"] for body in ollama.loads() if not body["messages"]] == ["gemma4:e4b"]   # refreshed: (c)


async def test_each_accepted_call_reads_its_lines_prompt_once_and_a_refused_call_nothing(pool, ollama, phone,
                                                                                        sessions):
    made = await line(pool, "ollama:gemma4:e4b")
    off = await line(pool, "ollama:qwen3:8b", on=False)
    svc = phone(CFG)
    for count in (1, 2):
        await answered(svc, FakeMedia(made.id), sessions, count)
        await until(lambda: phone_tasks() == [])
    await asyncio.wait_for(svc.handle(FakeMedia(off.id)), 5)          # `closed`: the line is off
    await asyncio.sleep(0.05)
    assert [body["model"] for body in ollama.prereads()] == ["gemma4:e4b", "gemma4:e4b"]
    assert ollama.loads() == ollama.prereads()                         # no bare load besides: the read loads it


async def test_a_hosted_model_gets_no_pre_read(pool, ollama, phone, sessions):
    # Brief test 5 (R19.2, as R17.8 I2): only a server the deployer hosts is asked to read a prompt. An Ollama cloud
    # model, even on the local server, caches nothing here, and a request there may cost money. The call goes on.
    made = await line(pool, "ollama:gpt-oss:120b-cloud")
    svc = phone(CFG)
    await answered(svc, FakeMedia(made.id), sessions, 1)
    await asyncio.sleep(0.05)
    assert len(sessions) == 1 and ollama.chats() == [] and phone_tasks() == []


async def test_a_pre_read_that_fails_never_ends_the_call(pool, real_call, ollama, phone, monkeypatch, caplog):
    # Brief test 4: Ollama refuses the read. One warning (ids and the error's type only); the call goes on.
    monkeypatch.setattr(service, "get_router", lambda: ollama.router)
    ollama.failing_loads = True
    made = await line(pool, "ollama:gemma4:e4b")
    svc = phone(CFG)
    media = FakeMedia(made.id)
    running = asyncio.create_task(svc.handle(media))
    await until(lambda: GREETED in media.heard() and warnings_naming(caplog, "gemma4:e4b"))
    await asyncio.sleep(0.2)
    assert not running.done() and media.hang_ups == 0                 # the call carries on
    media.caller_hangs_up()
    await asyncio.wait_for(running, 5)
    assert [c["end_reason"] for c in await calls_of(pool, made.id)] == ["caller"]
    [warning] = warnings_naming(caplog, "gemma4:e4b")
    assert "ProviderUnavailable" in warning.getMessage() and warning.exc_info is None


# --- R17.8: don't fight ---

async def test_a_model_gone_from_memory_is_loaded_again_whatever_else_is_there(pool, ollama, phone, caplog):
    # R17.8 (a). The line's model leaves memory (Ollama restarted, `ollama stop`, a crash, the server's own short
    # keep-alive), with nothing else loaded, then with an embedding model loaded meanwhile for a document search.
    # Each time the next tick loads it again: being gone is what refreshing is for, never a reason to give up.
    await line(pool, "ollama:gemma4:e4b")
    ticks = Ticks()
    svc = phone(ticks=ticks)
    await svc.start()
    await ticks.first()
    for meanwhile in ({}, {"embeddinggemma:latest": 2048}):
        ollama.loaded = dict(meanwhile)
        await ticks.tick()
        assert "gemma4:e4b" in ollama.loaded
    await ticks.tick()
    assert ollama.models() == ["gemma4:e4b"] * 4 and warnings_naming(caplog, "gemma4:e4b") == []


async def test_a_model_loaded_at_another_context_size_is_left_alone(pool, ollama, phone, caplog):
    # R17.8 (b). A chat has the model loaded at its own size (here the server's default, 128000). Loading it at the
    # phone's would reload it under that chat, so the tick leaves it; a call on the line pays the reload instead,
    # overlapped by the welcome. Once the chat's copy is gone, (a) loads it at the phone's size. R17.9: said at INFO
    # once per model per listener start, however many ticks leave it alone.
    caplog.set_level(logging.INFO, logger="server.phone.service")
    await line(pool, "ollama:gemma4:e4b")
    ollama.loaded = {"gemma4:e4b": FakeOllama.DEFAULT_CTX}
    ticks = Ticks()
    svc = phone(ticks=ticks)
    await svc.start()
    await ticks.first()
    await ticks.tick()
    await ticks.tick()
    assert ollama.loads() == []
    [said] = [r for r in caplog.records if "left alone" in r.getMessage()]
    assert said.levelno == logging.INFO and "ollama:gemma4:e4b" in said.getMessage() and "128000" in said.getMessage()
    del ollama.loaded["gemma4:e4b"]                    # the chat's keep-alive ran out
    await ticks.tick()
    assert ollama.models() == ["gemma4:e4b"] and ollama.loaded == {"gemma4:e4b": OURS}


async def test_the_size_ollama_reports_for_the_phones_own_load_counts_as_the_phones(pool, ollama, phone, caplog):
    # R17.9, rule (b)'s guard. An Ollama may report another context size than the num_ctx asked (with
    # OLLAMA_NUM_PARALLEL above 1 it may count every slot: 16384 x 2 here). The size /api/ps shows right after the
    # phone's own load is the phone's from then on, so the next ticks refresh its copy (c), and never take it for
    # someone else's (b), which would let it expire.
    caplog.set_level(logging.INFO, logger="server.phone.service")
    ollama.parallel = 2
    await line(pool, "ollama:gemma4:e4b")
    ticks = Ticks()
    svc = phone(ticks=ticks)
    await svc.start()
    await ticks.first()
    assert ollama.loaded == {"gemma4:e4b": 2 * OURS}
    for _ in range(3):
        await ticks.tick()
    assert ollama.models() == ["gemma4:e4b"] * 4
    assert [r for r in caplog.records if "left alone" in r.getMessage()] == []


async def test_a_live_call_only_stops_other_models_being_loaded_into_memory(pool, ollama, phone, sessions):
    # R17.9, R17.8's live-call rule narrowed to its purpose. Loading a model that isn't in memory during a call could
    # push the live call's model out, so while a call lasts rule (a) loads only the live calls' models. Refreshing a
    # model already loaded at the phone's size (c) only restarts its timer and takes no memory, so it runs for every
    # line: a busy line must never let another line's model expire. The review's case: a call live on gemma's line
    # through 7 ticks in a row. qwen3, loaded, is refreshed at each one. Then qwen3 leaves memory, and while the
    # call lasts it isn't loaded, nor when Ollama can't say what it holds; after the call, it is.
    gemma = await line(pool, "ollama:gemma4:e4b")
    await line(pool, "ollama:qwen3:8b")
    ticks = Ticks()
    svc = phone(ticks=ticks)
    await svc.start()
    await ticks.first()
    assert sorted(ollama.models()) == ["gemma4:e4b", "qwen3:8b"]
    media = FakeMedia(gemma.id)
    running = asyncio.create_task(svc.handle(media))
    await until(lambda: len(sessions) == 1 and len(ollama.loads()) == 3)      # the call's read of its line's prompt
    for _ in range(7):
        await ticks.tick()
    during = ollama.models()[3:]
    assert (during.count("gemma4:e4b"), during.count("qwen3:8b")) == (7, 7)   # (c) for both, at every tick
    before = len(ollama.loads())
    del ollama.loaded["qwen3:8b"]                      # qwen3 leaves memory while the call goes on
    await ticks.tick()
    ollama.ps_down = True                              # and then Ollama can't say what it holds
    await ticks.tick()
    ollama.ps_down = False
    assert ollama.models()[before:] == ["gemma4:e4b", "gemma4:e4b"]           # only the live call's model
    media.caller_hangs_up()
    await asyncio.wait_for(running, 5)
    await ticks.tick()
    assert sorted(ollama.models()[before + 2:]) == ["gemma4:e4b", "qwen3:8b"]


async def test_a_model_pushed_out_by_a_sibling_in_one_refresh_stops_being_refreshed(pool, ollama, phone, sessions,
                                                                                    caplog):
    # Brief test 7, as R17.8 replaces R17.5. The two lines' models don't fit in memory together: a refresh that loads
    # both ends with one of them already gone. That one isn't refreshed again (one warning, model ids only), so the
    # two never take turns reloading; the other is. A call on the dropped model's line still loads it as the call
    # connects, which pushes the other out; while that call lasts nothing else is loaded, and after it the other is
    # loaded again. A new listener retries both.
    ollama.room = 1
    lines = {"gemma4:e4b": await line(pool, "ollama:gemma4:e4b"), "qwen3:8b": await line(pool, "ollama:qwen3:8b")}
    ticks = Ticks()
    svc = phone(ticks=ticks)
    await svc.start()
    await ticks.first()
    assert sorted(ollama.models()) == sorted(lines)
    [kept] = ollama.loaded
    [pushed] = set(lines) - {kept}
    [warning] = warnings_naming(caplog, "was pushed out")
    assert warning.getMessage().startswith(f"Phone lines: model ollama:{pushed} was pushed out of memory by "
                                           f"ollama:{kept}")
    await ticks.tick()
    assert ollama.models()[2:] == [kept]
    media = FakeMedia(lines[pushed].id)
    running = asyncio.create_task(svc.handle(media))
    await until(lambda: len(sessions) == 1 and len(ollama.loads()) == 4)
    assert ollama.models()[3] == pushed and list(ollama.loaded) == [pushed]
    await ticks.tick()
    assert len(ollama.loads()) == 4                    # the live call's model stays; the kept one waits
    media.caller_hangs_up()
    await asyncio.wait_for(running, 5)
    await ticks.tick()
    assert ollama.models()[4:] == [kept] and len(warnings_naming(caplog, "was pushed out")) == 1
    await svc.stop()
    await svc.start()                                  # a new listener starts afresh
    await until(lambda: len(warnings_naming(caplog, "was pushed out")) == 2)
    assert sorted(ollama.models()[5:7]) == sorted(lines)


# --- R17.6, R17.8 M1: quiet failures ---

async def test_an_ollama_outage_is_one_warning_and_the_refresher_carries_on(pool, ollama, phone, caplog):
    # Brief test 8. Ollama refusing every tick is one warning, not one a tick; it recovers without a word, and a
    # second outage is a second warning. stop() leaves nothing running.
    await line(pool, "ollama:gemma4:e4b")
    ollama.down = True
    ticks = Ticks()
    svc = phone(ticks=ticks)
    await svc.start()
    await ticks.first()
    await ticks.tick()
    await ticks.tick()
    assert len(warnings_naming(caplog, "gemma4:e4b")) == 1
    ollama.down = False
    await ticks.tick()
    assert ollama.loaded == {"gemma4:e4b": OURS} and len(warnings_naming(caplog, "gemma4:e4b")) == 1
    ollama.down = True
    await ticks.tick()
    assert len(warnings_naming(caplog, "gemma4:e4b")) == 2
    await asyncio.wait_for(svc.stop(), 5)
    assert phone_tasks() == []


async def test_a_model_the_server_refuses_is_one_warning_while_another_loads_fine(pool, ollama, phone, caplog):
    # R17.8 M1. A 404 is that model's own trouble: the other line's model loading fine on the same server, at every
    # tick, must not make it warn again each time (as one warning per provider would).
    ollama.missing = {"qwen3:8b"}                      # still listed, but it can't be loaded
    await line(pool, "ollama:gemma4:e4b")
    await line(pool, "ollama:qwen3:8b")
    ticks = Ticks()
    svc = phone(ticks=ticks)
    await svc.start()
    await ticks.first()
    for _ in range(3):
        await ticks.tick()
    [warning] = warnings_naming(caplog, "qwen3:8b")
    assert "ProviderError" in warning.getMessage() and warnings_naming(caplog, "gemma4:e4b") == []
    assert ollama.models().count("gemma4:e4b") == 4


async def test_a_model_its_provider_no_longer_lists_is_not_loaded(pool, ollama, phone, caplog):
    # R17.8 M1: the gate would close this line (its model is gone from the provider's list), so nothing loads it.
    await line(pool, "ollama:deleted-model:1b")
    ticks = Ticks()
    svc = phone(ticks=ticks)
    await svc.start()
    await ticks.first()
    await ticks.tick()
    assert ollama.loads() == [] and warnings_naming(caplog, "deleted-model") == []


@pytest.mark.parametrize("trouble", ["down", "slow"])
async def test_lines_that_cant_be_read_are_one_warning_and_the_refresher_recovers(pool, ollama, phone, monkeypatch,
                                                                                  caplog, trouble):
    # R17.6: the database refusing, or too slow to answer within GATE_S, at every tick: one warning; then the lines
    # are read again and their model loaded, without a word.
    await line(pool, "ollama:gemma4:e4b")
    monkeypatch.setattr(service, "GATE_S", 0.2)
    real = store.open_line_models
    broken = [True]

    async def reading(conn):
        if broken[0]:
            if trouble == "down":
                raise psycopg.OperationalError("the database went away")
            await asyncio.sleep(30)
        return await real(conn)
    monkeypatch.setattr(store, "open_line_models", reading)
    ticks = Ticks()
    svc = phone(ticks=ticks)
    await svc.start()
    await ticks.first()
    await ticks.tick()
    assert ollama.loads() == []
    [warning] = warnings_naming(caplog, "refreshed")
    assert ("OperationalError" if trouble == "down" else "TimeoutError") in warning.getMessage()
    assert warning.exc_info is None                                   # an outage, not a bug: no traceback
    broken[0] = False
    await ticks.tick()
    assert ollama.models() == ["gemma4:e4b"] and len(warnings_naming(caplog, "refreshed")) == 1
    await asyncio.wait_for(svc.stop(), 5)
    assert phone_tasks() == []
