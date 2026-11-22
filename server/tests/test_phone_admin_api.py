"""The admin API for phone lines (spec 5.7, 5.10; rulings R11.x): lines, their voices and clips, the listener's state,
and each line's calls with their transcripts.

Every route runs on the test's own rolled-back connection, handed out through the router's connection seam, which
counts what is checked out: a voice render and a provider request must find nothing held (R11.5, R11.7). No request
reaches Kokoro, Google or a model provider: the voice, the Kokoro voice list and the model router are fakes."""
import asyncio
import io
import logging
import secrets
import wave
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from server import db
from server.auth import deps
from server.auth.authz import project_role
from server.chat_voice import GoogleVoiceError
from server.phone import service, store, voice
from server.phone.clips import DEFAULT_CLIP_TEXTS
from server.phone.store import LineVoice
from server.routers import phone_admin
from server.services import console_settings, inference_budget, model_settings, profile_library
from server.tests import seed
from server.tests.chat_harness import FakeRouter
from server.tests.dbutil import ADMIN_URL, apply_migrations, url_for
from server.tests.test_project_members import _p, _user

pytestmark = pytest.mark.asyncio

GREETING = "Hello, you've reached Front desk. I'm an automated assistant. How can I help?"
KOKORO = {"engine": "kokoro", "voice": "af_heart", "accent": None}
CHIRP = {"engine": "chirp", "voice": "Aoede", "accent": "en-GB"}
OFF = {"listening": False, "pbx_address": None, "allow": [], "active": 0}
ON = {"listening": True, "pbx_address": "192.0.2.10:9092", "allow": ["192.0.2.1"], "active": 0}
CLIP_KINDS = 1 + sum(len(texts) for texts in DEFAULT_CLIP_TEXTS.values())   # the greeting and the defaults


class Conns:
    """The router's connection seam in tests: every short connection is the test's own, and `open` counts how many
    the route holds at this moment."""

    def __init__(self, conn):
        self.conn = conn
        self.open = 0

    @asynccontextmanager
    async def _one(self):
        self.open += 1
        try:
            yield self.conn
        finally:
            self.open -= 1

    def __call__(self):
        return self._one()


@pytest.fixture
def conns(db_conn):
    return Conns(db_conn)


@pytest.fixture
def router(monkeypatch, conns):
    """A model router that allows and lists everything, and fails a test if a provider request is made while a
    connection is held."""
    class _Router(FakeRouter):
        async def is_listed(self, model_id):
            assert conns.open == 0, "a connection was held across the provider's model list"
            return await super().is_listed(model_id)

    fake = _Router()
    monkeypatch.setattr(phone_admin, "get_router", lambda: fake)
    return fake


@pytest.fixture
def renders(monkeypatch, conns):
    """The voice: each sentence renders as 0.1 s of a constant tone; records what it spoke and checks that no
    connection is held meanwhile."""
    spoken = []

    async def render(text, v):
        assert conns.open == 0, "a connection was held across a voice render"
        spoken.append((text, v))
        return b"\x10\x00" * 800
    monkeypatch.setattr(voice, "render", render)
    return spoken


@pytest.fixture
def failing_render(monkeypatch):
    async def render(text, v):
        raise GoogleVoiceError(502, "Google's quota for voices is used up for now")
    monkeypatch.setattr(voice, "render", render)


@pytest.fixture(autouse=True)
def _environment(monkeypatch, router, renders):
    """Off by default: the listener, and both Google voices. Kokoro's voices are a fixed list: an American, a
    British and a Spanish one (Kokoro reads a voice's text in the language its id's first letter names)."""
    monkeypatch.setattr(service, "status", lambda: dict(OFF))
    monkeypatch.setattr(phone_admin, "_kokoro_voices", lambda: ["af_heart", "bf_emma", "ef_dora"])
    for var in ("GOOGLE_TTS_MODEL", "GOOGLE_APPLICATION_CREDENTIALS"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def listening(monkeypatch):
    monkeypatch.setattr(service, "status", lambda: dict(ON))


@pytest.fixture
async def world(db_conn):
    """An admin who owns a project, a second project they own, a project they're only a reader of, a project
    they're not in, and a plain member."""
    admin, other, member = await _user(db_conn, "pa-admin"), await _user(db_conn, "pa-other"), \
        await _user(db_conn, "pa-member")
    project = await seed.make_project(db_conn, admin, "Product docs")
    second = await seed.make_project(db_conn, admin, "Second")
    read_only = await seed.make_project(db_conn, other, "Read only", members=[(admin, "reader")])
    foreign = await seed.make_project(db_conn, other, "Foreign")
    return {"admin": _p(admin, admin=True), "member": _p(member), "other": _p(other, admin=True),
            "project": project, "second": second, "read_only": read_only, "foreign": foreign}


def _client(conns, principal):
    app = FastAPI()
    app.dependency_overrides[deps.get_current_user] = lambda: principal
    app.dependency_overrides[phone_admin.phone_conn] = lambda: conns
    app.include_router(phone_admin.router)
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.fixture
async def admin_client(conns, world):
    async with _client(conns, world["admin"]) as c:
        yield c


@pytest.fixture
async def member_client(conns, world):
    async with _client(conns, world["member"]) as c:
        yield c


def _body(world, **kw):
    return {"name": "Front desk", "projectId": world["project"], "model": "m1", "voice": KOKORO,
            "greeting": GREETING, **kw}


async def _count(db_conn, sql, *params):
    cur = await db_conn.execute(sql, params)
    return (await cur.fetchone())[0]


async def _store_line(db_conn, world, **kw):
    """A line made straight through the store: no clips."""
    args = dict(name="Front desk", profile_id=None, project_id=world["project"], model_id="ollama:m1",
                voice=LineVoice("kokoro", "af_heart", None), greeting=GREETING)
    return await store.create_line(db_conn, world["admin"], **{**args, **kw})


@pytest.fixture
async def line(admin_client, world):
    """A line made through the API (so with its clips), off."""
    r = await admin_client.post("/v1/admin/phone/lines", json=_body(world))
    assert r.status_code == 201, r.text
    return r.json()


def _refusal(r, status, reason):
    assert r.status_code == status, r.text
    assert r.json()["detail"]["reason"] == reason
    return r.json()["detail"]


# --- who may ---

async def test_members_get_403(member_client):
    lid, cid = "6f1c2a00-0000-4000-8000-000000000001", "6f1c2a00-0000-4000-8000-000000000002"
    for method, path in [("GET", "/status"), ("GET", "/voices"), ("GET", "/lines"), ("POST", "/lines"),
                         ("GET", f"/lines/{lid}"), ("PATCH", f"/lines/{lid}"), ("DELETE", f"/lines/{lid}"),
                         ("POST", f"/lines/{lid}/access"), ("GET", f"/lines/{lid}/clips/greeting"),
                         ("GET", f"/lines/{lid}/calls"), ("GET", f"/calls/{cid}"), ("DELETE", f"/calls/{cid}")]:
        r = await member_client.request(method, "/v1/admin/phone" + path, json={} if method != "GET" else None)
        assert r.status_code == 403, (method, path, r.status_code)


async def test_status_is_the_services(admin_client, listening):
    assert (await admin_client.get("/v1/admin/phone/status")).json() == ON


@pytest.mark.parametrize("address", ["0.0.0.0:9092", "[::]:9092", "192.0.2.10", "2001:db8::1:9092",
                                     "[2001:db8::1:9092"])
async def test_status_never_shows_an_undialable_address(admin_client, monkeypatch, address):
    # Final fix F7: the console's header shows status's pbx_address; an address nobody can dial reads as none, so the
    # header says to set PHONE_PBX_ADDRESS (R12.1) instead of naming a wildcard.
    monkeypatch.setattr(service, "status", lambda: {**ON, "pbx_address": address})
    assert (await admin_client.get("/v1/admin/phone/status")).json() == {**ON, "pbx_address": None}


# --- creating, and the snippet ---

async def test_create_line_renders_clips_and_shows_snippet(admin_client, db_conn, world, renders, listening):
    r = await admin_client.post("/v1/admin/phone/lines", json=_body(world, dailyTokens=5000))
    assert r.status_code == 201, r.text
    body = r.json()
    lid = body["id"]
    assert body == {
        "id": lid, "name": "Front desk", "enabled": False, "profile": None,
        "project": {"id": world["project"], "name": "Product docs"}, "model": "ollama:m1", "voice": KOKORO,
        "language": "en", "greeting": GREETING, "maxCallS": 600, "maxCalls": 2, "dailyTokens": 5000,
        "callsToday": 0, "lastCallAt": None, "transcripts": 0, "access": True, "closedReason": "off",
        "snippet": "exten => 810,1,Answer()\n"
                   f" same => n,AudioSocket({lid},192.0.2.10:9092)\n"
                   " same => n,Hangup()"}
    clips = await store.load_clips(db_conn, lid)
    assert len(clips) == CLIP_KINDS == len(renders)
    assert [c.text for c in clips if c.kind == "greeting"] == [GREETING]
    assert {c.voice_key for c in clips} == {"kokoro:af_heart:"}
    line = await store.get_line(db_conn, lid)
    assert await project_role(db_conn, line.service_user_id, world["project"]) == "reader"
    assert (await admin_client.get(f"/v1/admin/phone/lines/{lid}")).json() == body
    assert (await admin_client.get("/v1/admin/phone/lines")).json() == [body]


async def test_create_can_switch_the_line_on(admin_client, world):
    r = await admin_client.post("/v1/admin/phone/lines", json=_body(world, enabled=True))
    assert r.status_code == 201, r.text
    assert r.json()["enabled"] is True


async def test_create_without_a_project_makes_an_off_line(admin_client, world):
    r = await admin_client.post("/v1/admin/phone/lines", json=_body(world, projectId=None))
    assert r.status_code == 201, r.text
    assert (r.json()["project"], r.json()["access"]) == (None, None)
    _refusal(await admin_client.post("/v1/admin/phone/lines", json=_body(world, projectId=None, enabled=True)),
             422, "no_project")


async def test_snippet_is_null_when_not_listening(admin_client, line, monkeypatch):
    assert line["snippet"] is None
    # an address is configured, but the listener isn't up (it couldn't bind, or the VAD didn't load)
    monkeypatch.setattr(service, "status", lambda: {**ON, "listening": False})
    assert (await admin_client.get(f"/v1/admin/phone/lines/{line['id']}")).json()["snippet"] is None


@pytest.mark.parametrize("address", [None, "0.0.0.0:9092", "[::]:9092", "0:9092", "192.0.2.10", "192.0.2.10:0",
                                     "nonsense",
                                     # R11.13: an IPv6 host without brackets has no port that can be told apart
                                     "2001:db8::1", "2001:db8::1:9092",
                                     # final fix F15: a bracket that never closes, or anything else after it
                                     "[2001:db8::1:9092", "[2001:db8::1]x:9092", "[2001:db8::1]]:9092"])
async def test_snippet_is_null_without_a_dialable_address(admin_client, line, monkeypatch, address):
    # R11.1 / R11.1a: never a copyable snippet with a placeholder or a wildcard in it
    monkeypatch.setattr(service, "status", lambda: {**ON, "pbx_address": address})
    assert (await admin_client.get(f"/v1/admin/phone/lines/{line['id']}")).json()["snippet"] is None


@pytest.mark.parametrize("address", ["pbx-gw.example.com:9092", "[2001:db8::1]:9092"])
async def test_a_named_or_bracketed_pbx_address_is_dialable(admin_client, line, monkeypatch, address):
    monkeypatch.setattr(service, "status", lambda: {**ON, "pbx_address": address})
    snippet = (await admin_client.get(f"/v1/admin/phone/lines/{line['id']}")).json()["snippet"]
    assert f"AudioSocket({line['id']},{address})" in snippet


async def test_the_line_shows_its_profile_by_name(admin_client, db_conn, world):
    profile = await profile_library.create_profile(db_conn, name="Phone desk", description="", text="Be brief.",
                                                   updated_by=None)
    r = await admin_client.post("/v1/admin/phone/lines", json=_body(world, profileId=profile.id))
    assert r.json()["profile"] == {"id": profile.id, "name": "Phone desk", "enabled": True}
    # Final fix F8: a profile switched off in the library is still the line's, but a call answers as the default
    # profile (profile_text.resolve skips a disabled one). The JSON says so, for the console to say it.
    other = await profile_library.create_profile(db_conn, name="Day desk", description="", text="Be kind.",
                                                 updated_by=None)
    await profile_library.update_profile(db_conn, other.id, is_default=True, updated_by=None)   # the default stays on
    await profile_library.update_profile(db_conn, profile.id, enabled=False, updated_by=None)
    lid = r.json()["id"]
    assert (await admin_client.get(f"/v1/admin/phone/lines/{lid}")).json()["profile"] == {
        "id": profile.id, "name": "Phone desk", "enabled": False}
    assert [ln["profile"] for ln in (await admin_client.get("/v1/admin/phone/lines")).json()
            if ln["id"] == lid] == [{"id": profile.id, "name": "Phone desk", "enabled": False}]
    _refusal(await admin_client.post("/v1/admin/phone/lines",
                                     json=_body(world, profileId="6f1c2a00-0000-4000-8000-00000000000f")),
             422, "unknown_profile")


# --- why a line turns callers away (final fix F19) ---

async def test_closed_reason_is_the_gates_own_offline_answer(admin_client, db_conn, world, router):
    # The list says why a line turns its callers away, by the gate's checks in the gate's order (service.py steps
    # 2-4): its state, its project, its membership, its budget, the model's offline rules. Never the provider's list.
    async def never(model_id):
        raise AssertionError("the line JSON asked the provider")
    router.is_listed = never

    async def on(**kw):
        made = await _store_line(db_conn, world, **kw)
        return await store.update_line(db_conn, world["admin"], made.id, enabled=True)
    off = await _store_line(db_conn, world)
    answers = await on()
    no_project = await on(project_id=world["second"])
    await db_conn.execute("DELETE FROM projects WHERE id = %s", (world["second"],))
    no_access = await on()
    await db_conn.execute("DELETE FROM project_members WHERE user_id = %s", (no_access.service_user_id,))
    spent = await on(daily_tokens=10)
    await inference_budget.record_usage(db_conn, spent.service_user_id, 6, 4, model=spent.model_id, kind="chat",
                                        estimated=False)

    async def reasons():
        return {ln["id"]: ln["closedReason"] for ln in (await admin_client.get("/v1/admin/phone/lines")).json()}
    assert await reasons() == {off.id: "off", answers.id: None, no_project.id: "no_project",
                               no_access.id: "no_access", spent.id: "budget"}
    assert (await admin_client.get(f"/v1/admin/phone/lines/{spent.id}")).json()["closedReason"] == "budget"
    # The model withdrawn (the environment's list, here): a line that would answer now reads "model"; the earlier
    # checks still win for the others, as at the gate.
    router.allowed = False
    assert await reasons() == {off.id: "off", answers.id: "model", no_project.id: "no_project",
                               no_access.id: "no_access", spent.id: "budget"}
    router.allowed = True
    await console_settings.save(db_conn, model_settings.SECTION, {"chat_models": ["ollama:other"]},
                                updated_by=world["admin"].user_id)
    assert (await reasons())[answers.id] == "model"


# --- the model (R11.5, R11.6) ---

@pytest.mark.parametrize("allowed,listed,words", [
    (False, True, "That model isn't available on this server."),
    (True, False, "This model isn't available."),
])
async def test_unallowed_model_is_422(admin_client, db_conn, world, router, renders, allowed, listed, words):
    router.allowed, router.listed = allowed, listed
    detail = _refusal(await admin_client.post("/v1/admin/phone/lines", json=_body(world)), 422, "model_not_allowed")
    assert detail["message"] == words
    assert renders == [] and await store.list_lines(db_conn) == []


async def test_a_model_the_console_dropped_is_422(admin_client, db_conn, world):
    await console_settings.save(db_conn, model_settings.SECTION, {"chat_models": ["ollama:other"]},
                                updated_by=world["admin"].user_id)
    _refusal(await admin_client.post("/v1/admin/phone/lines", json=_body(world)), 422, "model_not_allowed")
    assert (await admin_client.post("/v1/admin/phone/lines", json=_body(world, model="other"))).status_code == 201


async def test_the_model_is_checked_when_the_line_is_or_turns_on(admin_client, db_conn, world, router, line):
    lid = line["id"]
    url = f"/v1/admin/phone/lines/{lid}"
    assert (await admin_client.patch(url, json={"enabled": True})).status_code == 200
    router.allowed = False                 # the admin withdrew the model after the line was switched on
    _refusal(await admin_client.patch(url, json={"name": "Night desk"}), 422, "model_not_allowed")
    _refusal(await admin_client.patch(url, json={"model": "m2", "enabled": False}), 422, "model_not_allowed")
    off = await admin_client.patch(url, json={"enabled": False})     # switching off is never blocked
    assert off.status_code == 200 and off.json()["enabled"] is False
    renamed = await admin_client.patch(url, json={"name": "Night desk"})   # nor is tidying an off line
    assert renamed.status_code == 200 and renamed.json()["name"] == "Night desk"
    _refusal(await admin_client.patch(url, json={"enabled": True}), 422, "model_not_allowed")
    assert (await store.get_line(db_conn, lid)).enabled is False
    router.allowed = True
    changed = await admin_client.patch(url, json={"model": "m2"})
    assert changed.json()["model"] == "ollama:m2"


# --- switching on (spec 5.7) ---

async def test_switch_on_needs_project_and_greeting(admin_client, db_conn, world):
    no_project = await _store_line(db_conn, world, project_id=None)
    _refusal(await admin_client.patch(f"/v1/admin/phone/lines/{no_project.id}", json={"enabled": True}),
             422, "no_project")
    no_clips = await _store_line(db_conn, world, name="No clips")
    _refusal(await admin_client.patch(f"/v1/admin/phone/lines/{no_clips.id}", json={"enabled": True}),
             422, "no_greeting_clip")
    assert (await store.get_line(db_conn, no_clips.id)).enabled is False
    # a save that renders the clips brings the greeting with it
    r = await admin_client.patch(f"/v1/admin/phone/lines/{no_clips.id}",
                                 json={"enabled": True, "greeting": "Hello there."})
    assert r.status_code == 200 and r.json()["enabled"] is True


async def test_clearing_the_project_switches_the_line_off(admin_client, line):
    url = f"/v1/admin/phone/lines/{line['id']}"
    await admin_client.patch(url, json={"enabled": True})
    r = await admin_client.patch(url, json={"projectId": None})
    assert (r.json()["enabled"], r.json()["project"], r.json()["access"]) == (False, None, None)


# --- the clips (R11.7) ---

async def test_render_failure_saves_nothing(admin_client, db_conn, world, line, monkeypatch, failing_render):
    detail = _refusal(await admin_client.post("/v1/admin/phone/lines", json=_body(world, name="Second desk")),
                      502, "voice_failed")
    assert detail["message"] == "Google's quota for voices is used up for now"
    assert [ln.name for ln in await store.list_lines(db_conn)] == ["Front desk"]
    assert await _count(db_conn, "SELECT count(*) FROM users WHERE kind = 'line'") == 1
    before = await store.get_line(db_conn, line["id"])
    stamp = await store.clips_stamp(db_conn, line["id"])
    _refusal(await admin_client.patch(f"/v1/admin/phone/lines/{line['id']}",
                                      json={"greeting": "A new welcome.", "name": "Renamed", "enabled": True}),
             502, "voice_failed")
    assert await store.get_line(db_conn, line["id"]) == before
    assert await store.clips_stamp(db_conn, line["id"]) == stamp


@pytest.mark.parametrize("exc,message,traceback", [
    # a voice's own failure: Google's (written for people) and Kokoro's (whose message may quote the text)
    (GoogleVoiceError(503, "Google's voice isn't working right now. Ask your admin."),
     "Google's voice isn't working right now. Ask your admin.", False),
    (ValueError(f"Nothing to synthesize, {GREETING!r} produced no phonemes"),
     f"Nothing to synthesize, {GREETING!r} produced no phonemes", False),
    (RuntimeError("espeak failed"), "espeak failed", False),
    # anything else is a bug inside the render: the same 502, and a traceback for whoever debugs it (R11.12)
    (TypeError("unsupported operand"), "unsupported operand", True),
    (KeyError("greeting"), "'greeting'", True),
    (ZeroDivisionError(), "The voice failed (ZeroDivisionError).", True),
])
async def test_any_render_failure_is_a_502_and_a_bug_is_logged_with_its_traceback(
        admin_client, db_conn, world, monkeypatch, caplog, exc, message, traceback):
    async def render(text, v):
        raise exc
    monkeypatch.setattr(voice, "render", render)
    caplog.set_level(logging.WARNING, logger="server.routers.phone_admin")
    detail = _refusal(await admin_client.post("/v1/admin/phone/lines", json=_body(world)), 502, "voice_failed")
    assert detail["message"] == message
    assert await store.list_lines(db_conn) == []
    [record] = [r for r in caplog.records if r.name == "server.routers.phone_admin"]
    assert bool(record.exc_info) is traceback
    assert type(exc).__name__ in record.getMessage()
    assert GREETING not in caplog.text


async def test_clips_re_render_only_when_the_text_or_voice_changes(admin_client, db_conn, line, renders,
                                                                    monkeypatch):
    lid, url = line["id"], f"/v1/admin/phone/lines/{line['id']}"
    stamp = await store.clips_stamp(db_conn, lid)
    renders.clear()
    for change in ({"name": "Night desk"}, {"maxCalls": 3, "maxCallS": 900, "dailyTokens": 100},
                   {"greeting": GREETING}, {"voice": KOKORO}, {"enabled": True}):
        assert (await admin_client.patch(url, json=change)).status_code == 200, change
    assert renders == [] and await store.clips_stamp(db_conn, lid) == stamp
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/run/secrets/google.json")
    r = await admin_client.patch(url, json={"voice": CHIRP})
    assert r.status_code == 200 and r.json()["voice"] == CHIRP
    assert len(renders) == CLIP_KINDS and {v for _, v in renders} == {LineVoice("chirp", "Aoede", "en-GB")}
    clips = await store.load_clips(db_conn, lid)
    assert {c.voice_key for c in clips} == {"chirp:Aoede:en-GB"}
    renders.clear()
    await admin_client.patch(url, json={"greeting": "Welcome to the night desk."})
    assert [t for t, _ in renders if t == "Welcome to the night desk."] == ["Welcome to the night desk."]
    assert [c.text for c in await store.load_clips(db_conn, lid) if c.kind == "greeting"] == [
        "Welcome to the night desk."]


async def test_a_line_changed_during_its_render_is_not_saved_with_stale_clips(admin_client, db_conn, line,
                                                                                monkeypatch):
    # another save changed the greeting while this one rendered the new voice with the old greeting
    async def render(text, v):
        await db_conn.execute("UPDATE phone_lines SET greeting = 'Changed elsewhere.' WHERE id = %s",
                              (line["id"],))
        return b"\x00\x00" * 80
    monkeypatch.setattr(voice, "render", render)
    stamp = await store.clips_stamp(db_conn, line["id"])
    _refusal(await admin_client.patch(f"/v1/admin/phone/lines/{line['id']}",
                                      json={"voice": {**KOKORO, "voice": "bf_emma"}}), 409, "line_changed")
    assert (await store.get_line(db_conn, line["id"])).voice == LineVoice("kokoro", "af_heart", None)
    assert await store.clips_stamp(db_conn, line["id"]) == stamp


async def test_greeting_clip_is_a_wav(admin_client, db_conn, line):
    r = await admin_client.get(f"/v1/admin/phone/lines/{line['id']}/clips/greeting")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    assert r.headers["cache-control"] == "no-store"
    [pcm] = [c.pcm for c in await store.load_clips(db_conn, line["id"]) if c.kind == "greeting"]
    with wave.open(io.BytesIO(r.content)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (8000, 1, 2)
        assert w.getnframes() == len(pcm) // 2
        assert w.readframes(w.getnframes()) == pcm


async def test_greeting_clip_404s_without_one(admin_client, db_conn, world):
    bare = await _store_line(db_conn, world)
    assert (await admin_client.get(f"/v1/admin/phone/lines/{bare.id}/clips/greeting")).status_code == 404
    unknown = "6f1c2a00-0000-4000-8000-000000000003"
    assert (await admin_client.get(f"/v1/admin/phone/lines/{unknown}/clips/greeting")).status_code == 404


# --- voices ---

async def test_voices_lists_only_the_offered_engines(admin_client, monkeypatch):
    engines = (await admin_client.get("/v1/admin/phone/voices")).json()["engines"]
    # R11.11: a phase-1 line speaks English, and Kokoro reads ef_dora's text with Spanish rules
    assert [(e["engine"], e["voices"]) for e in engines] == [("kokoro", ["af_heart", "bf_emma"])]
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/run/secrets/google.json")
    engines = (await admin_client.get("/v1/admin/phone/voices")).json()["engines"]
    assert [e["engine"] for e in engines] == ["kokoro", "chirp"]
    monkeypatch.setenv("GOOGLE_TTS_MODEL", "gemini-tts")
    engines = {e["engine"]: e for e in (await admin_client.get("/v1/admin/phone/voices")).json()["engines"]}
    assert list(engines) == ["kokoro", "google", "chirp"]
    assert "Aoede" in engines["google"]["voices"] and "Kore" in engines["chirp"]["voices"]
    assert {"code": "en-GB", "label": "British English"} in engines["chirp"]["accents"]
    assert "accents" not in engines["kokoro"]


@pytest.mark.parametrize("v,reason", [
    (CHIRP, "engine_not_configured"),                              # no Google set up
    ({**KOKORO, "voice": "zz_nobody"}, "unknown_voice"),
    ({**KOKORO, "voice": "ef_dora"}, "unknown_voice"),             # R11.11: Kokoro's, but not English
])
async def test_the_voice_must_be_offered(admin_client, db_conn, world, renders, v, reason):
    _refusal(await admin_client.post("/v1/admin/phone/lines", json=_body(world, voice=v)), 422, reason)
    assert renders == [] and await store.list_lines(db_conn) == []


async def test_google_voices_and_accents_are_checked(admin_client, world, monkeypatch):
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/run/secrets/google.json")
    _refusal(await admin_client.post("/v1/admin/phone/lines", json=_body(world, voice={**CHIRP, "voice": "Nobody"})),
             422, "unknown_voice")
    _refusal(await admin_client.post("/v1/admin/phone/lines", json=_body(world, voice={**CHIRP, "accent": "fr-FR"})),
             422, "unknown_accent")


async def test_kokoro_takes_no_accent(admin_client, world):
    r = await admin_client.post("/v1/admin/phone/lines", json=_body(world, voice={**KOKORO, "accent": "en-GB"}))
    assert r.json()["voice"] == KOKORO


# --- validation mirrors migration 033 (R11.8) ---

@pytest.mark.parametrize("change", [
    {"name": ""}, {"name": "   "}, {"name": "x" * 61}, {"greeting": ""}, {"greeting": "x" * 401},
    {"maxCallS": 59}, {"maxCallS": 3601}, {"maxCalls": 0}, {"maxCalls": 21}, {"language": "si"},
    {"dailyTokens": -1}, {"model": ""}, {"model": "has space"}, {"projectId": "not-a-uuid"},
    {"voice": {"engine": "espeak", "voice": "x"}}, {"enabled": "yes"}, {"unknown": 1},
])
async def test_bad_bodies_are_422_before_the_database(admin_client, db_conn, world, renders, change):
    assert (await admin_client.post("/v1/admin/phone/lines", json=_body(world, **change))).status_code == 422
    assert renders == [] and await store.list_lines(db_conn) == []


async def test_the_limits_themselves_are_accepted(admin_client, world):
    r = await admin_client.post("/v1/admin/phone/lines", json=_body(
        world, name="x" * 60, greeting="y" * 400, maxCallS=3600, maxCalls=20, dailyTokens=0, language="en"))
    assert r.status_code == 201, r.text
    assert (r.json()["maxCallS"], r.json()["maxCalls"], r.json()["dailyTokens"]) == (3600, 20, 0)


@pytest.mark.parametrize("field", ["name", "enabled", "model", "voice", "greeting", "maxCallS", "maxCalls"])
async def test_patch_refuses_null_for_a_required_field(admin_client, line, field):
    assert (await admin_client.patch(f"/v1/admin/phone/lines/{line['id']}", json={field: None})).status_code == 422


async def test_patch_passes_only_the_fields_it_holds(admin_client, db_conn, line):
    url = f"/v1/admin/phone/lines/{line['id']}"
    await admin_client.patch(url, json={"dailyTokens": 900, "maxCalls": 4})
    assert (await admin_client.patch(url, json={})).json() == {**line, "dailyTokens": 900, "maxCalls": 4}
    cleared = (await admin_client.patch(url, json={"dailyTokens": None})).json()
    assert (cleared["dailyTokens"], cleared["maxCalls"]) == (None, 4)
    assert (await admin_client.patch("/v1/admin/phone/lines/6f1c2a00-0000-4000-8000-000000000004",
                                     json={"name": "x"})).status_code == 404
    assert (await admin_client.patch("/v1/admin/phone/lines/not-a-uuid", json={})).status_code == 422


# --- attaching a project (R2.6, R11.4, R11.10) ---

@pytest.mark.parametrize("project,status", [("foreign", 404), ("read_only", 403)])
async def test_only_a_maintainer_attaches_a_project(admin_client, db_conn, world, renders, line, project, status):
    renders.clear()
    r = await admin_client.post("/v1/admin/phone/lines", json=_body(world, projectId=world[project], name="Other"))
    assert r.status_code == status
    assert renders == [], "a refused attach must not wait on a render first"
    assert [ln.name for ln in await store.list_lines(db_conn)] == ["Front desk"]
    r = await admin_client.patch(f"/v1/admin/phone/lines/{line['id']}", json={"projectId": world[project]})
    assert r.status_code == status
    assert (await store.get_line(db_conn, line["id"])).project_id == world["project"]


async def test_any_admin_may_save_a_line_whose_project_stays(conns, db_conn, world, line):
    # the console sends the whole form: a non-member admin's rename carries the current project, which is no change
    async with _client(conns, world["other"]) as c:
        r = await c.patch(f"/v1/admin/phone/lines/{line['id']}", json={"name": "Renamed", "projectId": world["project"]})
    assert r.status_code == 200, r.text
    assert (r.json()["name"], r.json()["project"]["id"]) == ("Renamed", world["project"])


async def test_moving_a_line_moves_its_membership(admin_client, db_conn, world, line):
    r = await admin_client.patch(f"/v1/admin/phone/lines/{line['id']}", json={"projectId": world["second"]})
    assert r.json()["project"] == {"id": world["second"], "name": "Second"} and r.json()["access"] is True
    user = (await store.get_line(db_conn, line["id"])).service_user_id
    assert await project_role(db_conn, user, world["project"]) is None


async def test_a_non_member_admin_reads_lines_and_transcripts(conns, db_conn, world, line):
    # R11.10: reading needs no project role; a project on a line is already public to anyone who dials
    async with _client(conns, world["other"]) as c:
        got = (await c.get(f"/v1/admin/phone/lines/{line['id']}")).json()
        assert got["project"] == {"id": world["project"], "name": "Product docs"}
        assert (await c.get(f"/v1/admin/phone/lines/{line['id']}/calls")).status_code == 200


async def _revoke(db_conn, lid):
    """What a maintainer does from the project's member list."""
    user = (await store.get_line(db_conn, lid)).service_user_id
    await db_conn.execute("DELETE FROM project_members WHERE user_id = %s", (user,))
    return user


async def test_restore_access_re_adds_the_membership(admin_client, conns, db_conn, world, line):
    lid = line["id"]
    user = await _revoke(db_conn, lid)
    assert (await admin_client.get(f"/v1/admin/phone/lines/{lid}")).json()["access"] is False
    # a save of the same project is not a change, and never re-adds it (R2.7)
    r = await admin_client.patch(f"/v1/admin/phone/lines/{lid}", json={"projectId": world["project"],
                                                                       "enabled": True})
    assert r.status_code == 200 and r.json()["access"] is False
    # someone who isn't a maintainer of the project can't give it back
    async with _client(conns, world["other"]) as c:
        assert (await c.post(f"/v1/admin/phone/lines/{lid}/access")).status_code == 404
    assert await project_role(db_conn, user, world["project"]) is None
    r = await admin_client.post(f"/v1/admin/phone/lines/{lid}/access")
    assert r.status_code == 200 and r.json()["access"] is True
    assert await project_role(db_conn, user, world["project"]) == "reader"
    events = "SELECT count(*) FROM project_events WHERE subject_user_id = %s AND kind = 'member.added'"
    added = await _count(db_conn, events, user)
    again = await admin_client.post(f"/v1/admin/phone/lines/{lid}/access")
    assert again.status_code == 200 and again.json()["access"] is True
    assert await _count(db_conn, events, user) == added


async def test_restore_access_needs_a_project_and_a_line(admin_client, db_conn, world):
    bare = await _store_line(db_conn, world, project_id=None)
    _refusal(await admin_client.post(f"/v1/admin/phone/lines/{bare.id}/access"), 422, "no_project")
    unknown = "6f1c2a00-0000-4000-8000-000000000005"
    assert (await admin_client.post(f"/v1/admin/phone/lines/{unknown}/access")).status_code == 404


# --- calls and transcripts ---

T0 = 1_763_700_000_000      # a call's greeting, in ms


async def _call(db_conn, lid, session_id, *, seconds_ago=2, length_s=151, stats=None, reason="caller", turns=1):
    """A finished call with a transcript. Times are relative to the test's transaction (`now()`), which the routes
    read too, and to which a refused call's row is stamped."""
    line = await store.get_line(db_conn, lid)
    call_id = await store.open_call(db_conn, line, session_id=session_id, now_ms=T0)
    # the caller's question and the reply share a millisecond: the question must still come first (R11.9)
    await db_conn.execute(
        "INSERT INTO chat_messages (id, session_id, role, content, timestamp, status) VALUES "
        "(%s, %s, 'assistant', 'We open at nine.', %s, 'complete'), (%s, %s, 'user', 'When do you open?', %s, "
        "'complete')", (f"a-{session_id}", session_id, T0 + 5000, f"u-{session_id}", session_id, T0 + 5000))
    await store.close_call(db_conn, call_id, end_reason=reason, turns=turns,
                           stats=stats if stats is not None else [])
    await db_conn.execute(
        "UPDATE phone_calls SET started_at = now() - make_interval(secs => %s), "
        "ended_at = now() - make_interval(secs => %s) + make_interval(secs => %s) WHERE id = %s",
        (seconds_ago, seconds_ago, length_s, call_id))
    return call_id


@pytest.fixture
async def finished_call(db_conn, line):
    stats = [{"stopped_at": 4.0, "heard_at": 4.6, "first_text_at": 5.1, "first_sentence_at": 5.5,
              "first_audio_at": 5.9, "error": None},
             {"stopped_at": 20.0, "heard_at": 20.5, "first_text_at": 21.4, "first_sentence_at": 22.0,
              "first_audio_at": 22.9, "error": None},
             {"stopped_at": 40.0, "heard_at": None, "first_text_at": None, "first_sentence_at": None,
              "first_audio_at": None, "error": "transcription"}]
    return await _call(db_conn, line["id"], "s-call-1", stats=stats, turns=2)


async def test_calls_list_and_transcript(admin_client, db_conn, line, finished_call):
    lid = line["id"]
    older = await _call(db_conn, lid, "s-call-0", seconds_ago=3 * 86400)
    refused = await store.record_refused_call(db_conn, lid, "busy")
    calls = (await admin_client.get(f"/v1/admin/phone/lines/{lid}/calls")).json()
    assert [c["id"] for c in calls] == [refused, finished_call, older]
    one = calls[1]
    assert (one["endReason"], one["turns"], one["lengthS"], one["slowestFirstSoundS"], one["transcript"]) == (
        "caller", 2, 151.0, 2.9, True)
    assert one["startedAt"] and one["endedAt"]
    assert (calls[0]["endReason"], calls[0]["transcript"], calls[0]["slowestFirstSoundS"], calls[0]["lengthS"]) == (
        "busy", False, None, 0.0)
    assert len((await admin_client.get(f"/v1/admin/phone/lines/{lid}/calls", params={"limit": 1})).json()) == 1
    got = (await admin_client.get(f"/v1/admin/phone/calls/{finished_call}")).json()
    assert got["lineId"] == lid and got["stats"][1]["first_audio_at"] == 22.9
    assert [(m["role"], m["content"]) for m in got["messages"]] == [
        ("assistant", GREETING), ("user", "When do you open?"), ("assistant", "We open at nine.")]
    assert set(got["messages"][0]) == {"id", "role", "content", "timestamp", "status", "finishReason"}
    line_now = (await admin_client.get(f"/v1/admin/phone/lines/{lid}")).json()
    assert (line_now["callsToday"], line_now["transcripts"]) == (2, 2)
    assert line_now["lastCallAt"] is not None
    unknown = "6f1c2a00-0000-4000-8000-000000000006"
    assert (await admin_client.get(f"/v1/admin/phone/calls/{unknown}")).status_code == 404
    assert (await admin_client.get(f"/v1/admin/phone/lines/{unknown}/calls")).status_code == 404


@pytest.fixture
async def line_with_two_calls(db_conn, line):
    for i in range(2):
        await _call(db_conn, line["id"], f"s-two-{i}")
    await store.record_refused_call(db_conn, line["id"], "closed")
    return line


async def test_delete_reports_transcripts(admin_client, db_conn, line_with_two_calls):
    lid = line_with_two_calls["id"]
    assert (await admin_client.get(f"/v1/admin/phone/lines/{lid}")).json()["transcripts"] == 2
    r = await admin_client.delete(f"/v1/admin/phone/lines/{lid}")
    assert r.status_code == 200 and r.json() == {"transcripts": 2}
    assert await store.get_line(db_conn, lid) is None
    assert await _count(db_conn, "SELECT count(*) FROM chat_sessions WHERE id IN ('s-two-0', 's-two-1')") == 0
    assert (await admin_client.delete(f"/v1/admin/phone/lines/{lid}")).status_code == 404


async def test_delete_one_call_removes_its_transcript(admin_client, db_conn, line, finished_call):
    r = await admin_client.delete(f"/v1/admin/phone/calls/{finished_call}")
    assert r.status_code == 200 and r.json() == {"transcripts": 1}
    assert await store.get_call(db_conn, finished_call) is None
    assert await _count(db_conn, "SELECT count(*) FROM chat_sessions WHERE id = 's-call-1'") == 0
    assert (await admin_client.get(f"/v1/admin/phone/lines/{line['id']}")).json()["transcripts"] == 0
    assert (await admin_client.delete(f"/v1/admin/phone/calls/{finished_call}")).status_code == 404


async def test_a_call_in_progress_blocks_deleting_it_and_its_line(admin_client, db_conn, line):
    lid = line["id"]
    live = await store.open_call(db_conn, await store.get_line(db_conn, lid), session_id="s-live", now_ms=T0)
    _refusal(await admin_client.delete(f"/v1/admin/phone/lines/{lid}"), 409, "call_in_progress")
    _refusal(await admin_client.delete(f"/v1/admin/phone/calls/{live}"), 409, "call_in_progress")
    assert await store.get_line(db_conn, lid) is not None
    # an open row older than the longest call plus a minute was left by a crash, and doesn't block
    await db_conn.execute("UPDATE phone_calls SET started_at = now() - interval '661 seconds' WHERE id = %s",
                          (live,))
    assert (await admin_client.delete(f"/v1/admin/phone/calls/{live}")).json() == {"transcripts": 1}
    await store.open_call(db_conn, await store.get_line(db_conn, lid), session_id="s-stale", now_ms=T0)
    await db_conn.execute("UPDATE phone_calls SET started_at = now() - interval '661 seconds'")
    assert (await admin_client.delete(f"/v1/admin/phone/lines/{lid}")).json() == {"transcripts": 1}


async def test_logs_hold_ids_not_the_greeting(admin_client, world, caplog):
    caplog.set_level(logging.DEBUG)
    r = await admin_client.post("/v1/admin/phone/lines", json=_body(world))
    assert r.status_code == 201
    assert GREETING not in caplog.text


# --- on the app's own pooled connections (the router's real seam) ---

@pytest.fixture(scope="module")
def scratch_url():
    """A database of this module's own: these saves commit for real."""
    name = f"nr_phone_admin_{secrets.token_hex(4)}"

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
async def pool_of_one(scratch_url, monkeypatch):
    """The app's pool settings with a single connection, published as the app's pool. A route that held it while
    the voice or the provider worked would starve them: they each take it for a moment."""
    pool = AsyncConnectionPool(scratch_url, min_size=1, max_size=1, open=False, configure=db._configure,
                               kwargs=db.POOL_CONN_KWARGS)
    await pool.open(wait=True, timeout=10)
    monkeypatch.setattr(db, "_pool", pool)
    try:
        yield pool
    finally:
        await pool.close()


async def test_on_the_real_pool_saves_commit_and_hold_no_connection(pool_of_one, monkeypatch):
    async def take_the_connection():
        async with pool_of_one.connection(timeout=1) as conn:   # PoolTimeout if a save still holds it
            await conn.execute("SELECT 1")

    class _Router(FakeRouter):
        async def is_listed(self, model_id):
            await take_the_connection()
            return True

    async def render(text, v):
        await take_the_connection()
        return b"\x01\x00" * 80
    monkeypatch.setattr(phone_admin, "get_router", lambda: _Router())
    monkeypatch.setattr(voice, "render", render)
    async with pool_of_one.connection() as conn:
        admin = await _user(conn, f"pa-real-{secrets.token_hex(4)}")
        project = await seed.make_project(conn, admin, "Real")
    app = FastAPI()
    app.dependency_overrides[deps.get_current_user] = lambda: _p(admin, admin=True)
    app.include_router(phone_admin.router)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/v1/admin/phone/lines", json={"name": "Real desk", "projectId": project, "model": "m1",
                                                         "voice": KOKORO, "greeting": GREETING, "enabled": True})
        assert r.status_code == 201, r.text
        lid = r.json()["id"]
        r = await c.patch(f"/v1/admin/phone/lines/{lid}", json={"greeting": "A new welcome."})
        assert r.status_code == 200, r.text
        async with pool_of_one.connection() as conn:        # committed: another connection sees it all
            line = await store.get_line(conn, lid)
            assert (line.enabled, line.greeting) == (True, "A new welcome.")
            assert len(await store.load_clips(conn, lid)) == CLIP_KINDS
            assert await project_role(conn, line.service_user_id, project) == "reader"

        async def fails(text, v):
            raise GoogleVoiceError(502, "Google's voice didn't answer")
        monkeypatch.setattr(voice, "render", fails)
        assert (await c.patch(f"/v1/admin/phone/lines/{lid}", json={"greeting": "Lost."})).status_code == 502
        assert (await c.post("/v1/admin/phone/lines", json={"name": "Lost desk", "model": "m1", "voice": KOKORO,
                                                             "greeting": GREETING})).status_code == 502
        async with pool_of_one.connection() as conn:
            assert (await store.get_line(conn, lid)).greeting == "A new welcome."
            assert [ln.name for ln in await store.list_lines(conn)] == ["Real desk"]
        assert (await c.delete(f"/v1/admin/phone/lines/{lid}")).json() == {"transcripts": 0}
        assert (await c.get(f"/v1/admin/phone/lines/{lid}")).status_code == 404
