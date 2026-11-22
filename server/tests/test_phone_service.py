"""The phone service (server/phone/service.py): who is let through when a call connects, the call caps, and the
listener's start and stop with the app.

Most tests hand `handle()` a fake media and a fake call session. The ones marked "real" run the real CallSession, the
real chat store and the real chat engine (`run_turn`), with a fake model, voice and recognizer. Every test runs on a
real connection pool over a scratch database of this module's own, so the store's writers commit for real, and the
chat `open_call` writes on one connection is claimed by `begin_turn` on another (R10.4)."""
import ast
import asyncio
import inspect
import logging
import re
import secrets
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np
import pytest
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool, PoolTimeout

from server import db
from server.auth.users import SEED_ADMIN_ID
from server.chat import orchestrator
from server.llm import router as llm_router
from server.llm.types import Finish, TextDelta, Usage
from server.phone import audiosocket, service, store, vad, voice
from server.phone.call import CallDeps, CallOutcome
from server.phone.config import PhoneConfig
from server.phone.media import Audio, HungUp
from server.phone.segmenter import SpeechEnded, SpeechStarted
from server.phone.store import Clip, LineVoice
from server.services import chat_options, console_settings, inference_budget, model_settings
from server.speech import service as speech_service
from server.speech.base import EngineStatus, Transcript
from server.tests import seed
from server.tests.chat_harness import FakeRouter, reply
from server.tests.dbutil import ADMIN_URL, apply_migrations, url_for
from server.tests.test_project_members import _p, _user

VOICE = LineVoice("kokoro", "af_heart", None)
GREETING = "Hello, you have reached the library. How can I help?"
QUESTION = "What time do you open on Saturday?"
TONES = {"greeting": 1, "busy": 2, "closed": 3, "goodbye": 4, "still": 5, "sorry": 6, "checking": 9}
BUSY, CLOSED, GREETED = TONES["busy"], TONES["closed"], TONES["greeting"]
CFG = PhoneConfig(listen=None, allow=(), pbx_address=None, max_calls=5)
START, END = Audio(b"<start>"), Audio(b"<end>")
SESSION_ID = re.compile(r"^s-\d{13}-[0-9a-z]{6}$")    # the web client's newSessionId (useChatEngine.js)


def tone(value: int, seconds: float = 0.1) -> bytes:
    """`seconds` of 8 kHz s16le whose every sample is `value`, so the frames say what played."""
    return np.full(int(round(seconds * 8000)), value, dtype="<i2").tobytes()


def clip_rows() -> list[Clip]:
    at = datetime(2026, 11, 21, 9, 0, tzinfo=timezone.utc)
    rows = [Clip(kind, 0, kind, voice.voice_key(VOICE), tone(value), at) for kind, value in TONES.items()]
    return rows + [Clip("filler", n, "filler", voice.voice_key(VOICE), tone(7 + n), at) for n in (0, 1)]


# --- the database: a scratch one for this module, reached through a real pool ---

@pytest.fixture(scope="module")
def scratch_url():
    name = f"nr_phone_svc_{secrets.token_hex(4)}"

    async def admin(sql: str) -> None:
        conn = await AsyncConnection.connect(ADMIN_URL, autocommit=True)
        try:
            await conn.execute(sql)
        finally:
            await conn.close()

    asyncio.run(admin(f'CREATE DATABASE "{name}"'))
    try:
        asyncio.run(apply_migrations(url_for(name)))
        yield url_for(name)
    finally:
        asyncio.run(admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture
async def pool(scratch_url, monkeypatch):
    """The app's own pool settings, published as the app's pool: the store, the chat store and the chat engine all
    take their connections from it."""
    pool = AsyncConnectionPool(scratch_url, min_size=1, max_size=8, open=False, configure=db._configure,
                               kwargs=db.POOL_CONN_KWARGS)
    await pool.open(wait=True, timeout=10)
    monkeypatch.setattr(db, "_pool", pool)
    try:
        yield pool
    finally:
        await orchestrator.drain_background(timeout=5)
        speech_service.reset_for_tests()
        await pool.close()


async def make_line(pool, *, enabled=True, max_calls=2, daily_tokens=None, clips=True):
    """A line on a project of its own, owned by a person of its own (the module shares one database)."""
    async with pool.connection() as conn:
        owner = await _user(conn, f"owner-{secrets.token_hex(4)}")
        project = await seed.make_project(conn, owner)
        line = await store.create_line(conn, _p(owner), name="Front desk", profile_id=None, project_id=project,
                                       model_id="ollama:test-model", voice=VOICE, greeting=GREETING,
                                       max_calls=max_calls, daily_tokens=daily_tokens)
        if enabled:
            line = await store.update_line(conn, _p(owner), line.id, enabled=True)
        if clips:
            await store.save_clips(conn, line.id, clip_rows())
    return line


async def calls_of(pool, line_id: str) -> list[dict]:
    async with pool.connection() as conn:
        return await store.list_calls(conn, line_id)


async def messages_of(pool, session_id: str) -> list[tuple]:
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT role, content, status FROM chat_messages WHERE session_id = %s "
                                 "ORDER BY timestamp, id", (session_id,))
        return await cur.fetchall()


async def refused(svc, media) -> None:
    """A call that must be refused: handle() returns once its clip has played. Bounded, because a call let through
    by mistake would wait for a hang-up nobody sends, and a regression should fail, not hang the suite. Every
    refusal is also checked for audio after the hang-up (R9.6): a sender left running would try its next frame
    within 20 ms."""
    await asyncio.wait_for(svc.handle(media), 5)
    await no_audio_after_the_hang_up(media)


async def no_audio_after_the_hang_up(media) -> None:
    await asyncio.sleep(0.1)
    assert media.hang_ups >= 1 and media.after_hang_up == 0, "audio went down the line after the 0x00 (R9.6)"


async def until(check, timeout: float = 10.0) -> None:
    """Wait for `check()` (plain or async) to be true."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not (await done if inspect.isawaitable(done := check()) else done):
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.05)


# --- the caller, and the fakes ---

class FakeMedia:
    """The PBX's end of one call. `say` is (seconds after the call's events start, Audio) pairs. The caller hangs up
    when the test says (`caller_hangs_up`); we hang up with `hang_up()`. Like the AudioSocket adapter, a send after
    the end raises ConnectionError, and hanging up twice is harmless. Any send tried once our hang-up has begun is
    counted in `after_hang_up`: on the wire it would follow the 0x00."""
    sample_rate = 8000

    def __init__(self, line_id: str, say=()) -> None:
        self.line_id = line_id
        self.say = list(say)
        self.frames: list[bytes] = []
        self.sent_at: list[float] = []
        self.hang_ups = 0
        self.after_hang_up = 0
        self.first_frame_at: float | None = None
        self.first_sound_at: float | None = None
        self.gone = asyncio.Event()
        self.gone_at: float | None = None

    async def events(self):
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        for at, event in self.say:
            await asyncio.sleep(max(0.0, t0 + at - loop.time()))
            if self.gone.is_set():
                break
            yield event
        await self.gone.wait()
        yield HungUp("caller")

    async def send(self, frame: bytes) -> None:
        if self.hang_ups:
            self.after_hang_up += 1
        if self.gone.is_set():
            raise ConnectionError("the call has ended")
        assert len(frame) == 320
        now = asyncio.get_running_loop().time()
        if self.first_frame_at is None:
            self.first_frame_at = now
        if self.first_sound_at is None and frame.strip(b"\0"):
            self.first_sound_at = now
        self.frames.append(frame)
        self.sent_at.append(now)

    async def hang_up(self) -> None:
        self.hang_ups += 1
        await asyncio.sleep(0)          # the 0x00 drains, as the adapter's does: a frame sent now would follow it
        self._end()

    def caller_hangs_up(self) -> None:
        self._end()

    def _end(self) -> None:
        if not self.gone.is_set():
            self.gone_at = asyncio.get_running_loop().time()
            self.gone.set()

    def heard(self) -> set[int]:
        """The sounds that went down the line (each clip is one constant value; silence is 0)."""
        return set(np.unique(np.frombuffer(b"".join(self.frames), dtype="<i2")).tolist()) - {0}

    def frames_of(self, value: int) -> int:
        return sum(value in np.frombuffer(f, dtype="<i2") for f in self.frames)


@pytest.fixture(autouse=True)
def router(monkeypatch):
    """The model router every call is checked against (R10.10) and answered by: a fake that allows and lists
    every model, unless a test says otherwise."""
    fake = FakeRouter()
    monkeypatch.setattr(service, "get_router", lambda: fake)
    return fake


class QuietEars:
    """What start() warms when a test doesn't say (F13): a recognizer that notes the warm-up and loads nothing, so no
    test starts loading a real Whisper model in a thread."""

    def __init__(self) -> None:
        self.asked: list[tuple] = []
        self.warmed = 0

    def engine_for(self, options, language=None):
        self.asked.append((options, language))
        return self

    def warm(self) -> None:
        self.warmed += 1


@pytest.fixture(autouse=True)
def ears(monkeypatch):
    fake = QuietEars()
    monkeypatch.setattr(speech_service, "engine_for", fake.engine_for)
    return fake


@pytest.fixture
def sessions(monkeypatch):
    """A fake CallSession that lasts until either side hangs up, and the list of those made."""
    made: list = []

    class FakeSession:
        def __init__(self, media, line, clips, session_id, deps) -> None:
            self.media, self.line, self.clips, self.session_id, self.deps = media, line, clips, session_id, deps
            made.append(self)

        async def run(self) -> CallOutcome:
            async for event in self.media.events():
                if isinstance(event, HungUp):
                    break
            await self.media.hang_up()
            return CallOutcome("caller", 0, [])

    monkeypatch.setattr(service, "CallSession", FakeSession)
    return made


class ScriptedHearing:
    """Stands in for the VAD: marker frames become its events. A clip is 1 s of 16 kHz silence."""

    def __init__(self, settings: dict, max_run_s: float | None = None) -> None:
        pass

    def push(self, pcm: bytes) -> list:
        return {b"<start>": [SpeechStarted()], b"<end>": [SpeechEnded(np.zeros(16000, dtype=np.float32))]}.get(pcm, [])


class FakeEars:
    """A remote recognizer (no one-at-a-time slot, so two calls overlap) that takes `seconds` per clip. It notes
    which keys `transcribe_for` was holding while it worked."""
    name, model, languages, where = "openai", "fake-whisper", (), "test"

    def __init__(self, seconds: float = 0.05) -> None:
        self.seconds = seconds
        self.calls: list[dict] = []

    async def transcribe(self, audio: bytes, mime: str, language: str | None) -> Transcript:
        loop = asyncio.get_running_loop()
        started = loop.time()
        await asyncio.sleep(self.seconds)
        self.calls.append({"start": started, "end": loop.time(), "mime": mime, "language": language,
                           "keys": set(speech_service._running)})
        return Transcript(QUESTION, "en", 1.0, no_speech=0.02, avg_logprob=-0.2)

    async def status(self) -> EngineStatus:
        return EngineStatus(True, "ready", "")

    def warm(self) -> None:
        pass


class FakeVoice:
    """Each sentence is 0.2 s of a tone of its own."""

    def __init__(self) -> None:
        self.calls: list[tuple[float, str, LineVoice]] = []

    async def __call__(self, text: str, line_voice: LineVoice) -> bytes:
        self.calls.append((asyncio.get_running_loop().time(), text, line_voice))
        await asyncio.sleep(0.02)
        return tone(100 + len(self.calls) % 100, 0.2)


class SlowRouter(FakeRouter):
    """A model that writes one sentence every 0.1 s for 4 s: still writing when the caller hangs up. It notes when
    each sentence was taken and when its stream was closed."""

    def __init__(self) -> None:
        super().__init__()
        self.pulled: list[float] = []
        self.closed_at: list[float] = []

    def stream_chat(self, model_id, messages, tools, settings):
        self.calls.append({"model": model_id, "at": asyncio.get_running_loop().time()})
        return self._write()

    async def _write(self):
        loop = asyncio.get_running_loop()
        try:
            for i in range(40):
                await asyncio.sleep(0.1)
                self.pulled.append(loop.time())
                yield TextDelta(f"This is sentence number {i} of a long answer. ")
            yield Usage(10, 400)
            yield Finish("stop")
        finally:
            self.closed_at.append(loop.time())


class Fakes:
    def __init__(self) -> None:
        self.router = FakeRouter(steps=[reply("We open at nine."), reply("We open at nine.")])
        self.voice = FakeVoice()
        self.ears = FakeEars()


@pytest.fixture
def real_call(monkeypatch):
    """The real CallSession with scripted hearing, and a fake model, voice and recognizer behind the real
    `run_turn` and `transcribe_for`."""
    fakes = Fakes()
    monkeypatch.setattr(service, "Hearing", ScriptedHearing)
    monkeypatch.setattr(service, "get_router", lambda: fakes.router)
    monkeypatch.setattr(voice, "render", fakes.voice)
    monkeypatch.setattr(speech_service, "engine_for", lambda options, language=None: fakes.ears)
    return fakes


# --- who gets through ---

async def test_unknown_line_hangs_up_silently(pool, sessions):
    svc = service.PhoneService(CFG)
    async with pool.connection() as conn:
        before = (await (await conn.execute("SELECT count(*) FROM phone_calls")).fetchone())[0]
    for line_id in (str(uuid.uuid4()), "not-a-line"):
        media = FakeMedia(line_id)
        await refused(svc, media)
        assert media.hang_ups == 1 and media.heard() == set()     # silence from connect, no clip, then the hang-up
    async with pool.connection() as conn:
        assert (await (await conn.execute("SELECT count(*) FROM phone_calls")).fetchone())[0] == before
    assert sessions == []


async def test_line_off_plays_closed(pool, sessions):
    line = await make_line(pool, enabled=False)
    media = FakeMedia(line.id)
    await refused(service.PhoneService(CFG), media)
    assert media.heard() == {CLOSED} and media.frames_of(CLOSED) == 5      # all 0.1 s of it, then the hang-up
    assert media.hang_ups == 1 and sessions == []
    [call] = await calls_of(pool, line.id)
    assert (call["end_reason"], call["session_id"], call["turns"]) == ("closed", None, 0)
    assert call["ended_at"] == call["started_at"]


async def test_removed_membership_plays_closed(pool, sessions):
    # Review Focus 4: a maintainer took the line off the project. The line is still switched on.
    line = await make_line(pool)
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM project_members WHERE user_id = %s", (line.service_user_id,))
    media = FakeMedia(line.id)
    await refused(service.PhoneService(CFG), media)
    assert media.heard() == {CLOSED} and media.hang_ups == 1 and sessions == []
    assert [c["end_reason"] for c in await calls_of(pool, line.id)] == ["closed"]


async def test_deleted_project_plays_closed(pool, sessions):
    # Review Focus 4, R10.5: deleting the project nulls the line's project and leaves it switched on.
    line = await make_line(pool)
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM projects WHERE id = %s", (line.project_id,))
        after = await store.get_line(conn, line.id)
    assert after.enabled is True and after.project_id is None
    media = FakeMedia(line.id)
    await refused(service.PhoneService(CFG), media)
    assert media.heard() == {CLOSED} and media.hang_ups == 1 and sessions == []
    assert [c["end_reason"] for c in await calls_of(pool, line.id)] == ["closed"]


async def test_spent_budget_plays_closed(pool, sessions):
    line = await make_line(pool, daily_tokens=10)
    async with pool.connection() as conn:
        await inference_budget.record_usage(conn, line.service_user_id, 6, 4, model=line.model_id, kind="chat",
                                            estimated=False)
    media = FakeMedia(line.id)
    await refused(service.PhoneService(CFG), media)
    assert media.heard() == {CLOSED} and media.hang_ups == 1 and sessions == []
    # The caller hears `closed`; the row says why (spec 5.11: "Daily budget gone ... recorded as budget").
    assert [c["end_reason"] for c in await calls_of(pool, line.id)] == ["budget"]


async def test_a_model_the_console_withdrew_plays_closed(pool, sessions):
    # R10.10: the console's chat list no longer offers the line's model. A line is checked at every call, as a chat
    # turn is, so it stops answering instead of serving a model the admin took away.
    line = await make_line(pool)
    svc = service.PhoneService(CFG)

    async def offer(models):
        async with pool.connection() as conn:
            await console_settings.save(conn, model_settings.SECTION, {"chat_models": models},
                                        updated_by=SEED_ADMIN_ID)
    try:
        await offer(["ollama:other-model"])
        media = FakeMedia(line.id)
        await refused(svc, media)
        assert media.heard() == {CLOSED} and media.hang_ups == 1 and sessions == []
        # Offered again (a ':latest' spelling counts as the same model), it answers.
        await offer(["ollama:test-model:latest"])
        welcome = FakeMedia(line.id)
        running = asyncio.create_task(svc.handle(welcome))
        await until(lambda: len(sessions) == 1)
        welcome.caller_hangs_up()
        await asyncio.wait_for(running, 5)
    finally:
        async with pool.connection() as conn:
            await conn.execute("DELETE FROM app_settings WHERE key = %s", (model_settings.SECTION.key,))
    assert sorted(c["end_reason"] for c in await calls_of(pool, line.id)) == ["caller", "closed"]


async def test_a_model_the_environment_dropped_plays_closed(pool, sessions, monkeypatch):
    # R10.10: the real router, with INFERENCE_MODELS no longer naming the line's model.
    real = llm_router.Router(llm_router.load_provider_configs(
        {"OLLAMA_URL": "http://127.0.0.1:9", "INFERENCE_MODELS": "other-model"}), httpx.AsyncClient())
    monkeypatch.setattr(service, "get_router", lambda: real)
    line = await make_line(pool)
    media = FakeMedia(line.id)
    try:
        await refused(service.PhoneService(CFG), media)
    finally:
        await real.aclose()
    assert media.heard() == {CLOSED} and media.hang_ups == 1 and sessions == []
    assert [c["end_reason"] for c in await calls_of(pool, line.id)] == ["closed"]


async def test_a_model_its_provider_no_longer_has_plays_closed(pool, sessions, router):
    router.listed = False
    line = await make_line(pool)
    media = FakeMedia(line.id)
    await refused(service.PhoneService(CFG), media)
    assert media.heard() == {CLOSED} and media.hang_ups == 1 and sessions == []
    assert [c["end_reason"] for c in await calls_of(pool, line.id)] == ["closed"]


class SlowListRouter(FakeRouter):
    """A provider whose model list takes `delay` seconds to come back (a provider incident). It notes each request,
    the task it ran in, and how many of the pool's connections were checked out when it was asked."""

    def __init__(self, pool, delay: float = 5.0) -> None:
        super().__init__()
        self.pool, self.delay = pool, delay
        self.asked: list[dict] = []

    async def is_listed(self, model_id: str) -> bool:
        stats = self.pool.get_stats()
        self.asked.append({"model": model_id, "task": asyncio.current_task(),
                           "in_use": stats.get("pool_size", 0) - stats.get("pool_available", 0)})
        await asyncio.sleep(self.delay)
        return True


async def test_a_slow_model_list_doesnt_hold_the_greeting(pool, real_call, caplog):
    # R10.11 test 1. Real CallSession: the provider takes 5 s to list its models. The phone waits MODEL_CHECK_S for
    # it, then lets the call through (the router's own fail-open), so the greeting starts well inside Asterisk's 2 s.
    caplog.set_level(logging.INFO)
    router = real_call.router = SlowListRouter(pool)
    line = await make_line(pool)
    svc = service.PhoneService(CFG)
    media = FakeMedia(line.id)
    loop = asyncio.get_running_loop()
    connected = loop.time()
    running = asyncio.create_task(svc.handle(media))
    await until(lambda: media.first_sound_at is not None, timeout=5)
    assert media.first_sound_at - connected < 2.0
    await until(lambda: GREETED in media.heard())
    assert len(router.asked) == 1 and router.asked[0]["model"] == line.model_id
    assert any(line.id in r.getMessage() and line.model_id in r.getMessage() and r.levelno == logging.INFO
               for r in caplog.records)
    media.caller_hangs_up()
    await asyncio.wait_for(running, 5)
    await svc.stop()


async def test_the_model_list_is_asked_with_no_connection_held(pool, sessions, monkeypatch):
    # R10.11 test 2: while the provider is being asked, the gate's connection is back in the pool.
    router = SlowListRouter(pool, delay=0.2)
    monkeypatch.setattr(service, "get_router", lambda: router)
    line = await make_line(pool)
    svc = service.PhoneService(CFG)
    media = FakeMedia(line.id)
    running = asyncio.create_task(svc.handle(media))
    await until(lambda: len(sessions) == 1)
    assert [a["in_use"] for a in router.asked] == [0]
    media.caller_hangs_up()
    await asyncio.wait_for(running, 5)
    await svc.stop()


async def test_calls_during_one_slow_model_list_share_its_request(pool, sessions, monkeypatch):
    # R10.11 test 3: one request per model in flight; a second call waits on the same one, under its own bound.
    router = SlowListRouter(pool)
    monkeypatch.setattr(service, "get_router", lambda: router)
    line = await make_line(pool, max_calls=2)
    svc = service.PhoneService(CFG)
    medias = [FakeMedia(line.id), FakeMedia(line.id)]
    running = [asyncio.create_task(svc.handle(m)) for m in medias]
    await until(lambda: len(sessions) == 2)
    assert len(router.asked) == 1
    for media in medias:
        media.caller_hangs_up()
    await asyncio.wait_for(asyncio.gather(*running), 5)
    await svc.stop()


async def test_a_dropped_model_is_closed_without_waiting_for_the_provider(pool, sessions, monkeypatch):
    # R10.11 test 4: the console no longer offers the model. The offline check decides; the hanging provider is never
    # asked, and the caller hears `closed` at once.
    router = SlowListRouter(pool, delay=30)
    monkeypatch.setattr(service, "get_router", lambda: router)
    line = await make_line(pool)
    async with pool.connection() as conn:
        await console_settings.save(conn, model_settings.SECTION, {"chat_models": ["ollama:other-model"]},
                                    updated_by=SEED_ADMIN_ID)
    try:
        media = FakeMedia(line.id)
        loop = asyncio.get_running_loop()
        began = loop.time()
        await refused(service.PhoneService(CFG), media)
        assert loop.time() - began < 1.0 and router.asked == []
    finally:
        async with pool.connection() as conn:
            await conn.execute("DELETE FROM app_settings WHERE key = %s", (model_settings.SECTION.key,))
    assert media.heard() == {CLOSED} and sessions == []
    assert [c["end_reason"] for c in await calls_of(pool, line.id)] == ["closed"]


async def test_stop_cancels_a_model_list_still_in_flight(pool, sessions, monkeypatch):
    # R10.11 test 5: the call went through on the bound; the request it started is still running when the app stops.
    router = SlowListRouter(pool)
    monkeypatch.setattr(service, "get_router", lambda: router)
    line = await make_line(pool)
    svc = service.PhoneService(CFG)
    media = FakeMedia(line.id)
    running = asyncio.create_task(svc.handle(media))
    await until(lambda: len(sessions) == 1)
    [asked] = router.asked
    assert not asked["task"].done()
    loop = asyncio.get_running_loop()
    began = loop.time()
    await asyncio.wait_for(svc.stop(), 5)
    assert loop.time() - began < 2.0
    assert asked["task"].cancelled()
    await asyncio.wait_for(running, 5)
    assert [c["end_reason"] for c in await calls_of(pool, line.id)] == ["shutdown"]


async def test_a_router_not_started_takes_the_db_down_path(pool, sessions, monkeypatch, caplog):
    # R10.10: get_router() raising is the "can't check the line" path of R10.1. The gate read the line's clips just
    # before, so this process holds them: `busy`, the hang-up, and nothing recorded.
    monkeypatch.setattr(service, "get_router", llm_router.get_router)
    monkeypatch.setattr(llm_router, "_router", None)
    line = await make_line(pool)
    media = FakeMedia(line.id)
    await refused(service.PhoneService(CFG), media)
    assert media.heard() == {BUSY} and media.hang_ups == 1 and sessions == []
    assert await calls_of(pool, line.id) == []
    [logged] = [r for r in caplog.records if line.id in r.getMessage() and "can't be checked" in r.getMessage()]
    assert "RouterNotStarted" in logged.getMessage() and logged.exc_info is None    # an outage, not a bug (F12)


async def test_third_call_hears_busy(pool, sessions):
    # Review Focus 3: the line takes two calls at once; the third hears `busy`, and a slot frees when a call ends.
    line = await make_line(pool, max_calls=2)
    svc = service.PhoneService(CFG)
    first, second = FakeMedia(line.id), FakeMedia(line.id)
    running = [asyncio.create_task(svc.handle(m)) for m in (first, second)]

    await until(lambda: len(sessions) == 2)
    assert svc.status()["active"] == 2
    third = FakeMedia(line.id)
    await refused(svc, third)
    assert third.heard() == {BUSY} and third.frames_of(BUSY) == 5 and third.hang_ups == 1
    assert len(sessions) == 2

    first.caller_hangs_up()
    await asyncio.wait_for(running[0], 5)
    assert svc.status()["active"] == 1
    fourth = FakeMedia(line.id)
    running.append(asyncio.create_task(svc.handle(fourth)))
    await until(lambda: len(sessions) == 3)
    assert fourth.heard() == set()                 # it got in: the session, not a clip, speaks to it
    for media in (second, fourth):
        media.caller_hangs_up()
    await asyncio.wait_for(asyncio.gather(*running), 5)
    assert svc.status()["active"] == 0

    calls = await calls_of(pool, line.id)
    assert sorted(c["end_reason"] for c in calls) == ["busy", "caller", "caller", "caller"]
    made = {s.session_id for s in sessions}
    assert {c["session_id"] for c in calls if c["end_reason"] == "caller"} == made
    assert all(SESSION_ID.match(s) for s in made) and len(made) == 3        # a fresh chat per call (R10.9)
    assert all(s.line == line and s.clips["greeting"] == [tone(GREETED)] for s in sessions)
    assert all(isinstance(s.deps, CallDeps) for s in sessions)


async def test_the_global_cap_counts_every_line(pool, sessions):
    one, two = await make_line(pool), await make_line(pool)
    svc = service.PhoneService(PhoneConfig(listen=None, allow=(), pbx_address=None, max_calls=1))
    first = FakeMedia(one.id)
    running = asyncio.create_task(svc.handle(first))
    await until(lambda: len(sessions) == 1)
    other = FakeMedia(two.id)
    await refused(svc, other)
    assert other.heard() == {BUSY} and other.hang_ups == 1
    first.caller_hangs_up()
    await asyncio.wait_for(running, 5)
    assert [c["end_reason"] for c in await calls_of(pool, two.id)] == ["busy"]
    assert [c["end_reason"] for c in await calls_of(pool, one.id)] == ["caller"]


async def test_db_down_at_connect(pool, sessions, monkeypatch, caplog):
    line = await make_line(pool, enabled=False)
    warm = service.PhoneService(CFG)
    await refused(warm, FakeMedia(line.id))        # refused `closed`: the line's clips are now held in memory

    class DownPool:
        """A database that is gone: a connection waits out its timeout, then fails, as psycopg_pool's does."""
        def __init__(self) -> None:
            self.timeouts: list = []

        @asynccontextmanager
        async def connection(self, timeout=None):
            self.timeouts.append(timeout)
            await asyncio.sleep(30 if timeout is None else timeout)
            raise PoolTimeout("couldn't get a connection")
            yield                                   # pragma: no cover

    down = DownPool()
    loop = asyncio.get_running_loop()
    for broken in (down, None):                     # None: the app's pool was never published (get_pool raises)
        monkeypatch.setattr(db, "_pool", broken)
        media = FakeMedia(line.id)
        connected = loop.time()
        await asyncio.wait_for(warm.handle(media), 5)     # no exception escapes
        assert media.heard() == {BUSY} and media.hang_ups == 1
        assert media.first_sound_at - connected < 2.0     # the clip itself, not only the keep-alive's silence
        await no_audio_after_the_hang_up(media)
        cold = FakeMedia(line.id)
        await asyncio.wait_for(service.PhoneService(CFG).handle(cold), 5)
        assert cold.heard() == set() and cold.hang_ups == 1
        await no_audio_after_the_hang_up(cold)
    assert down.timeouts and all(t is not None and t <= 1.0 for t in down.timeouts)
    monkeypatch.setattr(db, "_pool", pool)
    assert [c["end_reason"] for c in await calls_of(pool, line.id)] == ["closed"]    # nothing recorded while down
    assert sessions == []
    assert any(line.id in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)
    # F12: a database that is away is an outage, named by type alone, with no traceback
    checked = [r for r in caplog.records if "can't be checked" in r.getMessage()]
    assert checked and all(r.exc_info is None for r in checked)
    assert {m for r in checked for m in ("PoolTimeout", "DatabaseUnavailable") if m in r.getMessage()} == {
        "PoolTimeout", "DatabaseUnavailable"}


async def test_a_bug_in_the_gate_is_logged_with_its_traceback(pool, sessions, monkeypatch, caplog):
    # Final fix F12: only an outage (the database, the pool, the network, the router not started) is logged by its
    # type alone. Anything else at connect is a bug, and its traceback is what makes it fixable.
    line = await make_line(pool)

    async def broken(conn, line_id):
        raise KeyError("a bug")
    monkeypatch.setattr(store, "get_line", broken)
    media = FakeMedia(line.id)
    await refused(service.PhoneService(CFG), media)
    assert media.heard() == set() and sessions == []
    [logged] = [r for r in caplog.records if line.id in r.getMessage() and "can't be checked" in r.getMessage()]
    assert logged.exc_info is not None and logged.exc_info[0] is KeyError


class SlowPool:
    """The app's pool, but its first `slow` connection waits take `wait` seconds each (a busy database at connect).
    Everything else is the real pool's."""

    def __init__(self, real, slow: int = 2, wait: float = 0.9) -> None:
        self.real, self.slow, self.wait = real, slow, wait

    @asynccontextmanager
    async def connection(self, timeout=None):
        if self.slow > 0:
            self.slow -= 1
            await asyncio.sleep(self.wait)
        async with self.real.connection(timeout=timeout) as conn:
            yield conn

    def __getattr__(self, name):
        return getattr(self.real, name)


async def test_silence_goes_down_the_line_from_connect_until_the_greeting(pool, real_call, monkeypatch):
    # Final fix F4. Asterisk drops a call after 2 s without audio from the app. Two slow connection waits (the gate's,
    # then open_call's) take 1.8 s before the session's pacer starts; the keep-alive's silence fills them.
    line = await make_line(pool)
    monkeypatch.setattr(db, "_pool", SlowPool(pool))
    svc = service.PhoneService(CFG)
    media = FakeMedia(line.id)
    loop = asyncio.get_running_loop()
    connected = loop.time()
    running = asyncio.create_task(svc.handle(media))
    await until(lambda: GREETED in media.heard(), timeout=5)
    assert media.first_frame_at - connected < 0.1
    assert media.first_sound_at - connected > 1.7                  # the waits really happened before the greeting
    greeting = next(i for i, f in enumerate(media.frames) if f.strip(b"\0"))
    times = [connected, *media.sent_at[:greeting + 1]]
    assert max(b - a for a, b in zip(times, times[1:])) < 0.1
    media.caller_hangs_up()
    await asyncio.wait_for(running, 5)
    await svc.stop()


async def test_a_stalled_gate_takes_the_database_down_path_within_its_bound(pool, sessions, monkeypatch):
    # Final fix F4. A gate query that never returns (a lock wait, a stuck server) is bounded at GATE_S: past it the
    # line can't be checked, so this process's `busy` clip plays (the gate read the clips before it stalled) and
    # nothing is recorded.
    line = await make_line(pool)

    async def stuck(conn, user_id, project_id):
        await asyncio.sleep(30)
    monkeypatch.setattr(service, "project_role", stuck)
    media = FakeMedia(line.id)
    loop = asyncio.get_running_loop()
    began = loop.time()
    await asyncio.wait_for(service.PhoneService(CFG).handle(media), 3.0 + 1.5)
    assert 2.9 < loop.time() - began < 3.0 + 1.0
    assert media.heard() == {BUSY} and media.hang_ups == 1 and sessions == []
    await no_audio_after_the_hang_up(media)
    assert await calls_of(pool, line.id) == []
    assert service.GATE_S == 3.0


async def test_a_call_that_cannot_be_opened_hears_busy_and_nothing_after_the_hang_up(pool, sessions, monkeypatch):
    line = await make_line(pool)

    async def broken(*args, **kwargs):
        raise OSError("the database went away")
    monkeypatch.setattr(store, "open_call", broken)
    media = FakeMedia(line.id)
    await refused(service.PhoneService(CFG), media)
    assert media.heard() == {BUSY} and media.hang_ups == 1 and sessions == []


async def test_a_stalled_open_call_takes_the_database_down_path_within_its_bound(pool, sessions, monkeypatch):
    # Task 15, E1. The keep-alive keeps a caller on the line, so an INSERT that never returns (a lock wait, a stuck
    # server) would leave them hearing silence until they hang up. `open_call` is bounded at GATE_S: past it the caller
    # hears this process's `busy` (the gate read the line's clips), the hang-up follows with nothing after it, and the
    # line's one slot is free for the next call.
    line = await make_line(pool, max_calls=1)
    opened = store.open_call

    async def stuck(*args, **kwargs):
        await asyncio.sleep(30)
    monkeypatch.setattr(store, "open_call", stuck)
    svc = service.PhoneService(CFG)
    media = FakeMedia(line.id)
    loop = asyncio.get_running_loop()
    began = loop.time()
    await asyncio.wait_for(svc.handle(media), service.GATE_S + 1.5)
    assert service.GATE_S < loop.time() - began < service.GATE_S + 1.0
    assert media.heard() == {BUSY} and media.hang_ups == 1 and sessions == []
    await no_audio_after_the_hang_up(media)
    assert svc.status()["active"] == 0
    monkeypatch.setattr(store, "open_call", opened)
    again = FakeMedia(line.id)
    running = asyncio.create_task(svc.handle(again))
    await until(lambda: len(sessions) == 1)                         # let through, not `busy`: the slot was freed
    again.caller_hangs_up()
    await asyncio.wait_for(running, 5)


# --- real calls ---

async def test_two_calls_transcribe_concurrently(pool, real_call, caplog):
    # Review Focus 3. Real CallSession: the per-call key `phone:<session id>` lets two calls on one line be heard at
    # once (the line's user as the key would answer the second AlreadyRunning).
    caplog.set_level(logging.INFO)
    real_call.ears.seconds = 0.5
    line = await make_line(pool, max_calls=2)
    svc = service.PhoneService(CFG)
    medias = [FakeMedia(line.id, say=[(0.3, START), (0.4, END)]) for _ in range(2)]
    running = [asyncio.create_task(svc.handle(m)) for m in medias]

    async def both_answered():
        calls = await calls_of(pool, line.id)
        if len(calls) < 2:
            return False
        replies = [m for c in calls for m in await messages_of(pool, c["session_id"])
                   if m[0] == "assistant" and m[1] != GREETING]
        played = all(len(m.heard()) == 2 for m in medias)                    # the greeting, then the answer
        return len(replies) == 2 and all(m[2] == "complete" for m in replies) and played
    await until(both_answered)
    for media in medias:
        media.caller_hangs_up()
    await asyncio.wait_for(asyncio.gather(*running), 5)

    calls = await calls_of(pool, line.id)
    keys = {f"phone:{c['session_id']}" for c in calls}
    heard = real_call.ears.calls
    assert len(heard) == 2 and len(keys) == 2
    assert max(h["start"] for h in heard) < min(h["end"] for h in heard)      # the two overlapped
    assert any(h["keys"] >= keys for h in heard)                              # both keys held at once
    assert all((h["mime"], h["language"]) == ("audio/wav", "en") for h in heard)
    assert len(real_call.router.calls) == 2
    for call in calls:
        assert (call["end_reason"], call["turns"], len(call["stats"])) == ("caller", 1, 1)
        assert await messages_of(pool, call["session_id"]) == [
            ("assistant", GREETING, "complete"), ("user", QUESTION, "complete"),
            ("assistant", "We open at nine.", "complete")]
    assert {v for _, _, v in real_call.voice.calls} == {VOICE}
    assert all(GREETED in media.heard() for media in medias)
    # Logs carry ids and reasons only.
    assert not any(QUESTION in r.getMessage() or "nine" in r.getMessage() for r in caplog.records)


async def test_hangup_mid_turn_spends_nothing_more(pool, real_call):
    # Review Focus 2. Real CallSession: the caller hangs up while the model is still writing. Its stream is closed
    # (run_turn's _finalize saves the reply and gives the claim back), and nothing more is asked of the model or the
    # voice. Ruling C1 is about a hang-up during begin(), which this isn't.
    router = real_call.router = SlowRouter()
    line = await make_line(pool)
    media = FakeMedia(line.id, say=[(0.2, START), (0.3, END)])
    running = asyncio.create_task(service.PhoneService(CFG).handle(media))

    async def voicing():
        return len(real_call.voice.calls) >= 2
    await until(voicing)
    media.caller_hangs_up()
    hung_up = media.gone_at
    await asyncio.wait_for(running, 5)
    returned = asyncio.get_running_loop().time()

    late = 0.05     # the session learns of the hang-up on its receiver's next turn of the loop
    assert len(router.calls) == 1
    assert router.pulled and max(router.pulled) <= hung_up + late and len(router.pulled) < 40
    assert len(router.closed_at) == 1 and router.closed_at[0] <= returned
    assert max(at for at, _, _ in real_call.voice.calls) <= hung_up + late
    [call] = await calls_of(pool, line.id)
    assert (call["end_reason"], call["turns"]) == ("caller", 1)
    greeting, asked, cut = await messages_of(pool, call["session_id"])
    assert asked[:2] == ("user", QUESTION) and cut[0] == "assistant" and cut[2] == "aborted"
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT active_turn_id FROM chat_sessions WHERE id = %s", (call["session_id"],))
        assert (await cur.fetchone())[0] is None          # the claim was given back before handle() returned


# --- start and stop ---

async def read_kinds(reader) -> list[int]:
    kinds = []
    try:
        while True:
            kind, _ = await audiosocket.read_frame(reader)
            kinds.append(kind)
    except (asyncio.IncompleteReadError, ConnectionError):
        return kinds


async def test_stop_ends_live_calls_as_shutdown(pool, real_call, monkeypatch):
    # R10.6, R10.7: stop() hangs each live call up itself and waits for its run(), which records `shutdown`; then
    # the server closes. Real listener, real adapter, real CallSession.
    warmed: list[str] = []
    monkeypatch.setattr(vad, "_session", lambda: warmed.append(threading.current_thread().name))
    line = await make_line(pool)
    svc = service.PhoneService(PhoneConfig(listen="127.0.0.1:0", allow=(), pbx_address="127.0.0.1:0"))
    await svc.start()
    status = svc.status()
    host, _, port = status["pbx_address"].rpartition(":")
    assert status["listening"] is True and host == "127.0.0.1" and port != "0"       # R10.3: the bound port
    assert (status["allow"], status["active"]) == ([], 0)
    assert len(warmed) == 1 and warmed[0] != threading.current_thread().name         # R10.8, off the event loop

    reader, writer = await asyncio.open_connection(host, int(port))
    writer.write(audiosocket.encode(audiosocket.KIND_UUID, uuid.UUID(line.id).bytes))
    kinds = asyncio.create_task(read_kinds(reader))

    async def greeted():
        return svc.status()["active"] == 1 and bool(await calls_of(pool, line.id))
    await until(greeted)
    await asyncio.sleep(0.2)                       # the greeting is going out
    loop = asyncio.get_running_loop()
    began = loop.time()
    await asyncio.wait_for(svc.stop(), 5)
    assert loop.time() - began < 3
    got = await asyncio.wait_for(kinds, 3)          # the line closed
    assert audiosocket.KIND_AUDIO in got and audiosocket.KIND_HANGUP in got
    [call] = await calls_of(pool, line.id)
    assert call["end_reason"] == "shutdown" and call["ended_at"] is not None and call["session_id"]
    assert (svc.status()["listening"], svc.status()["active"]) == (False, 0)
    with pytest.raises(OSError):
        await asyncio.open_connection(host, int(port))
    writer.close()


async def test_start_closes_stale_calls(pool, monkeypatch):
    monkeypatch.setattr(vad, "_session", lambda: None)
    line = await make_line(pool)
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO phone_calls (line_id, started_at) VALUES (%s, now() - interval '3700 seconds'), "
            "(%s, now() - interval '3500 seconds') RETURNING id", (line.id, line.id))
        stale, recent = [str(r[0]) for r in await cur.fetchall()]
    svc = service.PhoneService(PhoneConfig(listen="127.0.0.1:0", allow=(), pbx_address="127.0.0.1:0"))
    await svc.start()
    try:
        async with pool.connection() as conn:
            assert (await store.get_call(conn, stale))["end_reason"] == "shutdown"
            assert (await store.get_call(conn, recent))["ended_at"] is None     # it may still be a live call
    finally:
        await svc.stop()


async def test_start_is_a_no_op_when_the_listener_is_off(monkeypatch):
    def no_warm():
        raise AssertionError("the VAD is not loaded for a listener that is off")
    monkeypatch.setattr(vad, "_session", no_warm)
    monkeypatch.setattr(db, "_pool", None)          # and nothing asks the database
    svc = service.PhoneService(PhoneConfig(listen=None, allow=("192.0.2.10",), pbx_address=None))
    await svc.start()
    assert svc.status() == {"listening": False, "pbx_address": None, "allow": ["192.0.2.10"], "active": 0}
    await svc.stop()


def test_the_app_starts_the_listener_after_the_database_and_the_router_and_stops_it_first():
    """Read, not imported (importing server.app loads Kokoro). Every call needs the database, and its gate asks the
    model router at connect (R10.10), so the listener starts only once both are up (final fix F6):
    - on a normal boot, in `_startup` itself, after `start_llm_router()`, when the database is ready;
    - on the late-database path, in `_db_ready`, only when the router is already up.
    `stop()` runs first at shutdown, while the model router, the voices and the database are still there for the
    calls' last turns."""
    tree = ast.parse((Path(__file__).parents[1] / "app.py").read_text())
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)}

    def own(node):
        """The nodes of a function's own body, not of the functions defined inside it."""
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield child
                yield from own(child)

    def awaited(node) -> list[str]:
        """What the function itself awaits, in source order."""
        found = [(n.lineno, ast.unparse(n.value.func)) for n in own(node)
                 if isinstance(n, ast.Await) and isinstance(n.value, ast.Call)]
        return [name for _, name in sorted(found)]

    def guard_of(node, call: str) -> str:
        """The test of the `if` the awaited `call` sits in."""
        [cond] = [ast.unparse(n.test) for n in own(node) if isinstance(n, ast.If)
                  and any(isinstance(a, ast.Await) and ast.unparse(a.value.func) == call for a in ast.walk(n))]
        return cond

    startup = awaited(funcs["_startup"])
    assert "phone_service.start" in startup, "the normal boot starts the listener in _startup"
    assert startup.index("start_llm_router") < startup.index("phone_service.start")
    assert guard_of(funcs["_startup"], "phone_service.start") == "db_is_ready()"
    assert guard_of(funcs["_db_ready"], "phone_service.start") == "router_up"
    shutdown = awaited(funcs["_shutdown"])
    assert shutdown[0] == "phone_service.stop"
    assert shutdown.index("phone_service.stop") < shutdown.index("chat_orchestrator.drain_background")


LISTENING = PhoneConfig(listen="127.0.0.1:0", allow=(), pbx_address="127.0.0.1:0")


async def test_a_call_still_at_the_gate_when_the_app_stops_is_hung_up_not_refused(pool, monkeypatch):
    # Final fix F14. The line takes one call; the first is still ending (its last turn closing) when the second
    # call's gate returns, after stop() began. That call is hung up and recorded as nothing, not refused `busy`.
    line = await make_line(pool, max_calls=1)
    ended = asyncio.Event()

    class SlowToEnd:
        def __init__(self, media, *args) -> None:
            self.media = media

        async def run(self) -> CallOutcome:
            async for event in self.media.events():
                if isinstance(event, HungUp):
                    break
            await asyncio.sleep(0.5)                # the last turn's save
            ended.set()
            return CallOutcome("caller", 0, [])
    monkeypatch.setattr(service, "CallSession", SlowToEnd)
    real_get_line, at_gate, release = store.get_line, asyncio.Event(), asyncio.Event()
    reads = 0

    async def held(conn, line_id):
        nonlocal reads
        reads += 1
        if reads == 2:                              # the second call: wait at its gate
            at_gate.set()
            await release.wait()
        return await real_get_line(conn, line_id)
    monkeypatch.setattr(store, "get_line", held)
    svc = service.PhoneService(CFG)
    first, second = FakeMedia(line.id), FakeMedia(line.id)
    running = [asyncio.create_task(svc.handle(first))]
    await until(lambda: svc.status()["active"] == 1)
    running.append(asyncio.create_task(svc.handle(second)))
    await asyncio.wait_for(at_gate.wait(), 5)
    stopping = asyncio.create_task(svc.stop())
    await until(lambda: second.hang_ups == 1)
    release.set()
    await asyncio.wait_for(asyncio.gather(stopping, *running), 10)
    assert ended.is_set()                            # the first call was still ending when the second's gate returned
    assert second.heard() == set()                   # no `busy` clip to a line that is closing
    assert [c["end_reason"] for c in await calls_of(pool, line.id)] == ["shutdown"]


async def test_start_warms_the_recognizer_calls_hear_with(pool, monkeypatch, ears):
    # Final fix F13: the first caller's first question shouldn't wait for the speech model to load. The engine is the
    # one a call asks for: the deployment's, for the line language.
    monkeypatch.setattr(vad, "_session", lambda: None)
    svc = service.PhoneService(LISTENING)
    await svc.start()
    try:
        await until(lambda: ears.warmed == 1, timeout=5)
        [(options, language)] = ears.asked
        assert language == "en" and isinstance(options, chat_options.ChatOptions)
    finally:
        await svc.stop()


async def test_a_slow_warm_never_holds_the_start_and_stop_cancels_it(pool, monkeypatch, ears):
    monkeypatch.setattr(vad, "_session", lambda: None)
    reading, cancelled = asyncio.Event(), asyncio.Event()

    async def slow_options(conn):
        reading.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise
    monkeypatch.setattr(chat_options, "load_chat_options", slow_options)
    svc = service.PhoneService(LISTENING)
    loop = asyncio.get_running_loop()
    began = loop.time()
    await asyncio.wait_for(svc.start(), 5)
    assert loop.time() - began < 1.0 and svc.status()["listening"] is True
    await asyncio.wait_for(reading.wait(), 5)
    assert ears.warmed == 0
    began = loop.time()
    await asyncio.wait_for(svc.stop(), 5)
    assert loop.time() - began < 2.0
    assert cancelled.is_set() and ears.warmed == 0      # stop() cancelled the warm-up's read; nothing was warmed


async def test_a_failed_warm_is_logged_and_the_listener_stays_on(pool, monkeypatch, caplog):
    monkeypatch.setattr(vad, "_session", lambda: None)

    def no_engine(options, language=None):
        raise speech_service.EngineUnavailable("no speech model here")
    monkeypatch.setattr(speech_service, "engine_for", no_engine)
    svc = service.PhoneService(LISTENING)
    await svc.start()
    try:
        await until(lambda: any("warm" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records),
                    timeout=5)
        assert svc.status()["listening"] is True
    finally:
        await svc.stop()


async def test_start_is_idempotent_even_when_two_starts_overlap(pool, monkeypatch):
    # Final fix F6: on the late-database path `_db_ready` and the boot can both call start(). One listener, one
    # stale-call sweep; a start while listening returns at once.
    monkeypatch.setattr(vad, "_session", lambda: None)
    served = []
    real_serve = audiosocket.serve

    async def counting(cfg, handler):
        served.append(cfg.listen)
        await asyncio.sleep(0.05)                   # the bind takes a moment: the second start arrives meanwhile
        return await real_serve(cfg, handler)
    monkeypatch.setattr(audiosocket, "serve", counting)
    svc = service.PhoneService(PhoneConfig(listen="127.0.0.1:0", allow=(), pbx_address="127.0.0.1:0"))
    try:
        await asyncio.gather(svc.start(), svc.start())
        await svc.start()
        assert served == ["127.0.0.1:0"] and svc.status()["listening"] is True
    finally:
        await svc.stop()
