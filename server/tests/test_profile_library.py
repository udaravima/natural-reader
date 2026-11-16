"""The profile library's storage (profile library spec, "What is stored" and "The upgrade")."""
from pathlib import Path

import pytest
from psycopg.types.json import Jsonb

from server.services import profile_library as lib
from server.tests.chat_harness import member

SQL = Path(__file__).resolve().parents[1] / "sql" / "030_assistant_profiles.sql"


async def _new(conn, name, text="You are helpful.", **kw):
    return await lib.create_profile(conn, name=name, description=kw.pop("description", ""), text=text, updated_by=None)


async def test_the_first_profile_is_the_default_and_later_ones_follow_in_order(db_conn):
    a = await _new(db_conn, "Tutor", description="Explains step by step")
    b = await _new(db_conn, "Reviewer")
    assert (a.is_default, a.enabled, b.is_default) == (True, True, False)
    assert [(p.name, p.description) for p in await lib.list_profiles(db_conn)] == [
        ("Tutor", "Explains step by step"), ("Reviewer", "")]
    assert (await lib.default_profile(db_conn)).id == a.id and (await lib.get_profile(db_conn, b.id)).name == "Reviewer"
    assert await lib.get_profile(db_conn, "not-a-uuid") is None


async def test_names_are_unique_whatever_their_case_and_values_are_cleaned(db_conn):
    await _new(db_conn, "Tutor")
    with pytest.raises(lib.LibraryError) as e:
        await _new(db_conn, "  tutor ")
    assert e.value.code == "name_taken"
    p = await _new(db_conn, "  Careful\n reviewer ", text="Rules.\x07  \n", description=" Reads\ttwice ")
    assert (p.name, p.text, p.description) == ("Careful reviewer", "Rules.", "Reads twice")
    for bad in ({"name": ""}, {"name": "n" * 61}, {"text": "  "}, {"description": "d" * 201}):
        with pytest.raises(lib.LibraryError) as e:
            await lib.create_profile(db_conn, **{"name": "X", "description": "", "text": "t", "updated_by": None, **bad})
        assert e.value.code == "invalid"


async def test_making_another_the_default_moves_the_mark(db_conn):
    a, b = await _new(db_conn, "A"), await _new(db_conn, "B")
    got = await lib.update_profile(db_conn, b.id, is_default=True, updated_by=None)
    assert got.is_default
    assert [(p.name, p.is_default) for p in await lib.list_profiles(db_conn)] == [("A", False), ("B", True)]


async def test_the_default_cannot_be_turned_off_or_deleted(db_conn):
    a, b = await _new(db_conn, "A"), await _new(db_conn, "B")
    for act in (lib.update_profile(db_conn, a.id, enabled=False, updated_by=None), lib.delete_profile(db_conn, a.id),
                lib.update_profile(db_conn, a.id, is_default=False, updated_by=None)):
        with pytest.raises(lib.LibraryError) as e:
            await act
        assert e.value.code == "default_required"
    off = await lib.update_profile(db_conn, b.id, enabled=False, updated_by=None)
    assert off.enabled is False
    with pytest.raises(lib.LibraryError) as e:                        # a profile that is off can't be the default
        await lib.update_profile(db_conn, b.id, is_default=True, updated_by=None)
    assert e.value.code == "invalid"
    assert [p.name for p in await lib.list_profiles(db_conn, enabled_only=True)] == ["A"]


async def test_the_last_profile_can_be_deleted(db_conn):
    a = await _new(db_conn, "A")
    assert await lib.delete_profile(db_conn, a.id) == 0 and await lib.list_profiles(db_conn) == []
    assert await lib.default_profile(db_conn) is None


async def test_editing_and_missing(db_conn):
    a = await _new(db_conn, "A")
    got = await lib.update_profile(db_conn, a.id, name="Ada", description="d", text="New text.", updated_by=None)
    assert (got.name, got.description, got.text, got.is_default) == ("Ada", "d", "New text.", True)
    with pytest.raises(lib.LibraryError) as e:
        await lib.update_profile(db_conn, "00000000-0000-0000-0000-000000000000", name="X", updated_by=None)
    assert e.value.code == "not_found"
    with pytest.raises(lib.LibraryError) as e:
        await lib.delete_profile(db_conn, "junk")
    assert e.value.code == "not_found"


async def test_deleting_a_profile_leaves_its_chats_the_name(db_conn):
    alice = await member(db_conn, "alice")
    await _new(db_conn, "A")
    b = await _new(db_conn, "Tutor")
    await db_conn.execute("INSERT INTO chat_sessions (id, title, model, user_id, profile_id, profile_name) "
                          "VALUES ('s-1', 't', 'm', %s, %s, 'Tutor')", (alice.user_id, b.id))
    assert await lib.chats_using(db_conn, b.id) == 1
    assert await lib.delete_profile(db_conn, b.id) == 1
    cur = await db_conn.execute("SELECT profile_id, profile_name FROM chat_sessions WHERE id = 's-1'")
    assert await cur.fetchone() == (None, "Tutor")


async def test_reorder(db_conn):
    a, b, c = await _new(db_conn, "A"), await _new(db_conn, "B"), await _new(db_conn, "C")
    await lib.reorder(db_conn, [c.id, a.id])                             # one left out keeps its place after them
    assert [p.name for p in await lib.list_profiles(db_conn)] == ["C", "A", "B"]


async def test_house_rules(db_conn):
    assert await lib.house_rules(db_conn) == ""
    await lib.save_house_rules(db_conn, "Be kind.\x07\n", updated_by=None)
    assert await lib.house_rules(db_conn) == "Be kind."
    with pytest.raises(lib.LibraryError):
        await lib.save_house_rules(db_conn, "x" * 2001, updated_by=None)
    await lib.save_house_rules(db_conn, "  ", updated_by=None)
    assert await lib.house_rules(db_conn) == ""


async def test_the_upgrade_turns_the_single_profile_into_the_default_one(db_conn):
    await db_conn.execute("INSERT INTO app_settings (key, value) VALUES ('assistant_profile', %s)",
                          (Jsonb({"text": "You are Ada.\n- Be brief."}),))
    await db_conn.execute(SQL.read_text())
    got = await lib.list_profiles(db_conn)
    assert [(p.name, p.text, p.enabled, p.is_default) for p in got] == [("Assistant", "You are Ada.\n- Be brief.", True, True)]
    cur = await db_conn.execute("SELECT count(*) FROM app_settings WHERE key = 'assistant_profile'")
    assert (await cur.fetchone())[0] == 0
    await db_conn.execute(SQL.read_text())                               # a second run adds nothing
    assert len(await lib.list_profiles(db_conn)) == 1


async def test_the_upgrade_with_no_saved_profile_leaves_the_library_empty(db_conn):
    await db_conn.execute(SQL.read_text())
    assert await lib.list_profiles(db_conn) == []


async def test_house_rules_of_another_shape_read_as_none(db_conn):
    """A row edited by hand into a bare string must not end every chat turn with an error."""
    await db_conn.execute("INSERT INTO app_settings (key, value) VALUES ('assistant_house_rules', %s)",
                          (Jsonb("Be kind."),))
    assert await lib.house_rules(db_conn) == ""
