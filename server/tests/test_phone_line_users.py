"""Line users (users.kind = 'line') stay out of every path people use: sign-in linking, people lists, adding
members, deleting accounts."""
import pytest
from fastapi import HTTPException

from server.auth import users
from server.services import people
from server.services.people import DirectoryConfig

pytestmark = pytest.mark.asyncio

LINE_EMAIL = "line-x@phone-line.invalid"
OPEN = DirectoryConfig(mode="open", domains=frozenset(), show_email=True)


async def _line_user(conn, email=LINE_EMAIL, name="Phone line: Front desk"):
    cur = await conn.execute(
        "INSERT INTO users (email, display_name, role, status, kind) "
        "VALUES (%s, %s, 'member', 'active', 'line') RETURNING id", (email, name))
    return str((await cur.fetchone())[0])


async def _person(conn, email="ada@x.io", name="Ada Lovelace"):
    cur = await conn.execute(
        "INSERT INTO users (email, display_name, role, status) "
        "VALUES (%s, %s, 'member', 'active') RETURNING id", (email, name))
    return str((await cur.fetchone())[0])


async def test_login_never_links_a_line_row(db_conn):
    line_id = await _line_user(db_conn)
    # Failing closed is the answer: the email is taken, and the line's row is never claimed.
    with pytest.raises(ValueError):
        await users.resolve_or_provision_user(
            db_conn, iss="https://idp", sub="s-1", email=LINE_EMAIL, email_verified=True)
    cur = await db_conn.execute("SELECT oidc_sub, kind FROM users WHERE id = %s", (line_id,))
    assert await cur.fetchone() == (None, "line")


async def test_people_lists_skip_lines(db_conn):
    line_id = await _line_user(db_conn)
    person_id = await _person(db_conn)
    ids = [u["id"] for u in await users.list_users(db_conn)]
    assert person_id in ids and line_id not in ids
    found, total = await users.search_users(db_conn, q="Phone line", status=None, capability=None,
                                            limit=None, offset=0)
    assert found == [] and total == 0
    found, total = await users.search_users(db_conn, q=None, status=None, capability=None,
                                            limit=None, offset=0)
    assert line_id not in [u["id"] for u in found]
    assert total == len(found)
    for q in ("Phone line", LINE_EMAIL):
        assert await people.lookup(db_conn, OPEN, person_id, "ada@x.io", q, everyone=True) == []


async def test_assert_addable_refuses_lines(db_conn):
    line_id = await _line_user(db_conn)
    with pytest.raises(HTTPException) as e:
        await people.assert_addable(db_conn, line_id)
    assert e.value.status_code == 404
    await people.assert_addable(db_conn, line_id, allow_line=True)


async def test_delete_user_refuses_a_line(db_conn):
    line_id = await _line_user(db_conn)
    with pytest.raises(ValueError):
        await users.delete_user(db_conn, line_id)
    cur = await db_conn.execute("SELECT count(*) FROM users WHERE id = %s", (line_id,))
    assert (await cur.fetchone())[0] == 1


async def test_delete_user_still_deletes_a_person(db_conn):
    person_id = await _person(db_conn)
    await users.delete_user(db_conn, person_id)
    assert await users.get_user(db_conn, person_id) is None


@pytest.mark.parametrize("role", ["contributor", "maintainer", "owner"])
async def test_a_line_user_can_only_ever_be_a_reader(db_conn, role):
    # Final fix F10: put_member(line=True) is the one way a line joins a project. Anything above reader would let
    # anonymous callers change the project (or, as owner, hold it).
    from server.auth.authz import project_role
    from server.services.project_members import put_member
    from server.tests import seed
    from server.tests.test_project_members import _p, _user
    owner = await _user(db_conn, "line-owner")
    project = await seed.make_project(db_conn, owner)
    line_id = await _line_user(db_conn)
    with pytest.raises(ValueError):
        await put_member(db_conn, _p(owner), project, line_id, role, show_email=False, line=True)
    assert await project_role(db_conn, line_id, project) is None
    await put_member(db_conn, _p(owner), project, line_id, "reader", show_email=False, line=True)
    assert await project_role(db_conn, line_id, project) == "reader"
