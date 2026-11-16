"""The Librarian: the built-in profile that is always there (character profiles spec, "The Librarian")."""
import pytest

from server.services import profile_library as lib


async def _new(conn, name, text="You are helpful.", **kw):
    return await lib.create_profile(conn, name=name, description=kw.pop("description", ""), text=text, updated_by=None)


async def test_the_librarian_is_added_once_and_is_the_default_of_an_empty_library(db_conn):
    a = await lib.ensure_librarian(db_conn)
    b = await lib.ensure_librarian(db_conn)
    assert a.id == b.id
    assert (a.name, a.builtin, a.enabled, a.is_default, a.text) == ("The Librarian", True, True, True, "")
    assert a.description == "Careful answers from your documents, with page numbers."
    assert len(await lib.list_profiles(db_conn)) == 1


async def test_on_the_upgrade_the_default_stays_and_the_librarian_goes_last(db_conn):
    story = await _new(db_conn, "The Story Teller")          # the first profile: the default
    librarian = await lib.ensure_librarian(db_conn)
    assert librarian.is_default is False
    assert (await lib.default_profile(db_conn)).id == story.id
    assert [p.name for p in await lib.list_profiles(db_conn)] == ["The Story Teller", "The Librarian"]


async def test_a_profile_already_called_the_librarian_keeps_its_name(db_conn):
    await _new(db_conn, "the librarian")
    assert (await lib.ensure_librarian(db_conn)).name == "The Librarian (built in)"


async def test_the_librarian_can_be_renamed_and_described_but_not_deleted_off_or_given_text(db_conn):
    lb = await lib.ensure_librarian(db_conn)
    renamed = await lib.update_profile(db_conn, lb.id, name="Ada", description="Reads with you", updated_by=None)
    assert (renamed.name, renamed.description, renamed.builtin) == ("Ada", "Reads with you", True)
    for kw in ({"enabled": False}, {"text": "You are a pirate."}):
        with pytest.raises(lib.LibraryError) as e:
            await lib.update_profile(db_conn, lb.id, updated_by=None, **kw)
        assert e.value.code == "builtin"
    with pytest.raises(lib.LibraryError) as e:
        await lib.delete_profile(db_conn, lb.id)
    assert e.value.code == "builtin"
    assert (await lib.get_profile(db_conn, lb.id)).text == ""


async def test_a_new_profile_is_not_the_default_while_the_librarian_is(db_conn):
    await lib.ensure_librarian(db_conn)
    assert (await _new(db_conn, "Tutor")).is_default is False


async def test_the_database_refuses_a_second_builtin_or_one_turned_off(db_conn):
    from psycopg import errors
    lb = await lib.ensure_librarian(db_conn)
    with pytest.raises(errors.CheckViolation):
        async with db_conn.transaction():
            await db_conn.execute("UPDATE assistant_profiles SET enabled = FALSE, is_default = FALSE WHERE id = %s",
                                  (lb.id,))
    with pytest.raises(errors.UniqueViolation):
        async with db_conn.transaction():
            await db_conn.execute("INSERT INTO assistant_profiles (name, text, builtin) VALUES ('Second', '', TRUE)")
