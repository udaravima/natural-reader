"""Which profile a turn uses, and what it sends (profile library spec, "What a turn sends")."""

import pytest

from server.chat import context as ctx_mod
from server.chat import orchestrator, store
from server.chat.config import ChatConfig
from server.chat.orchestrator import TurnRequest, run_turn
from server.chat.tools import search_documents as sd_tool
from server.llm.types import CallSettings
from server.services import profile_library as lib
from server.tests.chat_harness import FakeRouter, member, reply, shim_pool


@pytest.fixture
def conn(db_conn, monkeypatch):
    shim_pool(monkeypatch, db_conn, store, orchestrator, ctx_mod, sd_tool)
    monkeypatch.delenv("CHAT_ASSISTANT_PROFILE_FILE", raising=False)
    return db_conn


async def _turn(conn, user, *, asked=None, sid="s-1"):
    claim = await store.begin_turn(session_id=sid, user_id=user.user_id, model_id="ollama:m", text="Hi",
                                   attachments=[], new_session_pins=None)
    req = TurnRequest(user_id=user.user_id, session_id=sid, model_id="ollama:m", text="Hi", attachments=(),
                      settings=CallSettings(), doc_id=None, timezone="UTC", profile_id=asked)
    router = FakeRouter(steps=[reply("Hello!")])
    events = [e async for e in run_turn(req, claim, router=router, cfg=ChatConfig(), deployment_budget=None)]
    start = next(e for e in events if e["type"] == "start")
    return start, router.calls[0]["messages"][0].content


async def _stamp(conn, sid="s-1"):
    cur = await conn.execute("SELECT profile_id::text, profile_name FROM chat_sessions WHERE id = %s", (sid,))
    return await cur.fetchone()


async def test_no_library_sends_what_it_always_did(conn):
    alice = await member(conn, "alice")
    start, system = await _turn(conn, alice)
    assert (start["profileId"], start["profileName"]) == (None, "")
    assert system.startswith("You are the assistant in Natural Reader, an app for reading and listening to documents. "
                             "Today is ")
    assert await _stamp(conn) == (None, None)


async def test_a_chat_keeps_its_profile_when_the_default_moves(conn):
    alice = await member(conn, "alice")
    plain = await lib.create_profile(conn, name="Plain", description="", text="You are plain.", updated_by=None)
    tutor = await lib.create_profile(conn, name="Tutor", description="", text="You are a tutor.", updated_by=None)
    await lib.save_house_rules(conn, "Be kind.", updated_by=None)
    start, system = await _turn(conn, alice)
    assert (start["profileId"], start["profileName"]) == (plain.id, "Plain")
    assert system.startswith("Be kind.\n\nYou are plain.\n\nYou are working in Natural Reader")
    assert await _stamp(conn) == (plain.id, "Plain")
    await lib.update_profile(conn, tutor.id, is_default=True, updated_by=None)
    start, system = await _turn(conn, alice)
    assert start["profileId"] == plain.id and "You are plain." in system      # the chat's own, not the new default
    start, system = await _turn(conn, alice, sid="s-2")
    assert start["profileId"] == tutor.id                                     # a new chat gets the new default


async def test_a_picked_profile_applies_and_sticks(conn):
    alice = await member(conn, "alice")
    await lib.create_profile(conn, name="Plain", description="", text="You are plain.", updated_by=None)
    tutor = await lib.create_profile(conn, name="Tutor", description="",
                                     text="You are {{profile}} in {{app}}. It is {{date}}.", updated_by=None)
    start, system = await _turn(conn, alice, asked=tutor.id)
    assert start["profileId"] == tutor.id and await _stamp(conn) == (tutor.id, "Tutor")
    assert system.startswith("You are Tutor in Natural Reader. It is ")
    assert "Today is" not in system and "You are working in" not in system      # placed by the profile: said once
    start, _ = await _turn(conn, alice)                                         # no pick sent: the chat's own
    assert start["profileId"] == tutor.id
    start, _ = await _turn(conn, alice, asked="00000000-0000-0000-0000-000000000000")   # gone: the chat's own
    assert start["profileId"] == tutor.id


async def test_a_deleted_profile_falls_back_to_the_default_and_the_chat_says_so_until_the_next_turn(conn):
    from server.routers.chat_sessions import _profile
    alice = await member(conn, "alice")
    plain = await lib.create_profile(conn, name="Plain", description="", text="You are plain.", updated_by=None)
    tutor = await lib.create_profile(conn, name="Tutor", description="", text="You are a tutor.", updated_by=None)
    await _turn(conn, alice, asked=tutor.id)
    await lib.delete_profile(conn, tutor.id)
    assert _profile(*await _stamp(conn)) == {"profileId": None, "profileName": "Tutor", "profileRemoved": True}
    start, system = await _turn(conn, alice)
    assert start["profileId"] == plain.id and "You are plain." in system
    assert _profile(*await _stamp(conn)) == {"profileId": plain.id, "profileName": "Plain", "profileRemoved": False}


async def test_a_profile_read_that_fails_ends_the_turn_as_an_error(conn, monkeypatch):
    """The profile is read before `start`: a failure there still ends the turn, saved and released, and never runs
    the model without the house rules."""
    from server.chat import profile_text

    async def broken(*a, **k):
        raise RuntimeError("database gone")
    monkeypatch.setattr(profile_text, "resolve", broken)
    alice = await member(conn, "alice")
    claim = await store.begin_turn(session_id="s-1", user_id=alice.user_id, model_id="ollama:m", text="Hi",
                                   attachments=[], new_session_pins=None)
    req = TurnRequest(user_id=alice.user_id, session_id="s-1", model_id="ollama:m", text="Hi", attachments=(),
                      settings=CallSettings(), doc_id=None, timezone="UTC")
    router = FakeRouter(steps=[reply("Hello!")])
    events = [e async for e in run_turn(req, claim, router=router, cfg=ChatConfig(), deployment_budget=None)]
    assert [e["type"] for e in events] == ["error"] and events[0]["code"] == "internal_error"
    assert router.calls == []                                                    # the model never ran
    cur = await conn.execute("SELECT status FROM chat_messages WHERE id = %s", (claim.assistant_message_id,))
    assert (await cur.fetchone())[0] == "error"
