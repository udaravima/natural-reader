"""A profile's backstory (character profiles spec, "Backstory and rules"): stored beside the rules, sent between the
house rules and the rules under its label, never shown to people."""
import pytest

from server.chat import profile_text
from server.chat.profile_text import BACKSTORY_LABEL
from server.routers import chat_profiles
from server.services import profile_library as lib
from server.tests.chat_harness import member
from server.tests.test_assistant_profile import _app, _client, _principal
from server.tests.test_turn_profile import _turn, conn  # noqa: F401  (conn: the turn harness fixture)


async def test_a_backstory_is_stored_and_sent_between_the_house_rules_and_the_rules(db_conn):
    ada = await lib.create_profile(db_conn, name="Ada", description="", text="- Be brief.",
                                   backstory="You grew up by the sea.", updated_by=None)
    assert ada.backstory == "You grew up by the sea."
    await lib.save_house_rules(db_conn, "Be kind.", updated_by=None)
    got = await profile_text.resolve(db_conn, session_id=None, asked_id=ada.id)
    assert got.combined == f"Be kind.\n\n{BACKSTORY_LABEL}\nYou grew up by the sea.\n\n- Be brief."
    assert BACKSTORY_LABEL == ("Who you are (your own life and voice, never a source of facts about documents or "
                               "the world):")


async def test_a_placeholder_in_the_backstory_is_filled_and_the_app_line_steps_aside(conn):
    alice = await member(conn, "alice")
    await lib.create_profile(conn, name="Ada", description="", text="- Be brief.",
                             backstory="You live inside {{app}}.", updated_by=None)
    _, system = await _turn(conn, alice)
    assert "You live inside Natural Reader." in system
    assert "You are working in" not in system


async def test_the_librarian_takes_no_backstory(db_conn):
    lb = await lib.ensure_librarian(db_conn)
    with pytest.raises(lib.LibraryError) as e:
        await lib.update_profile(db_conn, lb.id, backstory="You were a sailor.", updated_by=None)
    assert e.value.code == "builtin"


async def test_a_backstory_is_capped_and_cleaned(db_conn):
    with pytest.raises(lib.LibraryError) as e:
        await lib.create_profile(db_conn, name="Long", description="", text="x", backstory="a" * 6001,
                                 updated_by=None)
    assert e.value.code == "invalid"
    p = await lib.create_profile(db_conn, name="Clean", description="", text="x", backstory="Bell\x07 rings. ",
                                 updated_by=None)
    assert p.backstory == "Bell rings."
    q = await lib.update_profile(db_conn, p.id, backstory="", updated_by=None)
    assert q.backstory == ""


async def test_the_console_and_preview_carry_the_backstory_and_people_never_see_it(db_conn):
    admin = await _principal(db_conn, "s1", "a@x.io", "admin")
    async with _client(_app(db_conn, admin)) as client:
        body = (await client.post("/v1/admin/assistant/profiles", json={
            "name": "Ada", "text": "- Be brief.", "backstory": "SECRET-PAST by the sea."})).json()
        row = body["profiles"][0]
        assert row["backstory"] == "SECRET-PAST by the sea."
        assert row["combinedChars"] == len(profile_text.compose("", "SECRET-PAST by the sea.", "- Be brief."))
        r = await client.put(f"/v1/admin/assistant/profiles/{row['id']}", json={"backstory": "Changed past."})
        assert r.json()["profiles"][0]["backstory"] == "Changed past."
        preview = (await client.post("/v1/admin/assistant/preview", json={
            "name": "Ada", "text": "- Be brief.", "backstory": "PREVIEW-PAST."})).json()["preview"]
        assert f"{BACKSTORY_LABEL}\nPREVIEW-PAST." in preview
    person = await _principal(db_conn, "s2", "b@x.io", "member")
    app = _app(db_conn, person.__class__(user_id=person.user_id, email=person.email, role="member",
                                         capabilities=frozenset({"chat"})))
    app.include_router(chat_profiles.router)
    async with _client(app) as c:
        r = await c.get("/v1/chat/profiles")
    assert "backstory" not in r.json()["profiles"][0] and "PAST" not in r.text
