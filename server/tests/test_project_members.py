"""A0 §5, §9.2, §3.3: members — roles, the Maintainer/Owner boundary, the
last-owner rule (including two Owners racing), leaving, admin self-add."""
import asyncio
import logging
import secrets

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport
from psycopg import AsyncConnection

from server.auth import authz, deps
from server.auth.users import resolve_or_provision_user, set_status
from server.routers import projects as projects_router
from server.services import project_members
from server.tests import seed
from server.tests.dbutil import TEST_URL

pytestmark = pytest.mark.asyncio


async def _user(conn, sub, *, first=None, status="active"):
    u = await resolve_or_provision_user(conn, iss="i", sub=sub, email=f"{sub}@x.io", first_name=first)
    await set_status(conn, u["id"], status)
    return u["id"]


def _p(uid, *, admin=False):
    return deps.Principal(user_id=uid, email=f"{uid}@x.io", role="admin" if admin else "member",
                          capabilities=frozenset({"reader"}))


def _client(db_conn, principal):
    app = FastAPI()

    async def _conn():
        yield db_conn
    app.dependency_overrides[deps.get_conn] = _conn
    app.dependency_overrides[deps.get_current_user] = lambda: principal
    app.include_router(projects_router.router)
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _role(db_conn, pid, uid):
    return await authz.project_role(db_conn, uid, pid)


async def _kinds(db_conn, pid):
    cur = await db_conn.execute(
        "SELECT kind, details FROM project_events WHERE project_id=%s ORDER BY id", (pid,))
    return [(r[0], r[1]) for r in await cur.fetchall()]


async def test_owner_adds_a_member_with_a_role(db_conn):
    owner, ben = await _user(db_conn, "o", first="Olu"), await _user(db_conn, "b", first="Ben")
    pid = await seed.make_project(db_conn, owner)
    async with _client(db_conn, _p(owner)) as c:
        r = await c.put(f"/v1/projects/{pid}/members/{ben}", json={"role": "contributor"})
    assert r.status_code == 200
    m = r.json()
    assert (m["user_id"], m["name"], m["role"], m["added_via"], m["status"]) == (
        ben, "Ben", "contributor", "member", "active")
    assert m["added_by"] == {"id": owner, "name": "Olu"}
    assert "email" not in m
    assert await _kinds(db_conn, pid) == [("member.added", {"role": "contributor", "via": "member"})]


@pytest.mark.parametrize("payload", [None, {}, {"role": "admin"}, {"role": "owner", "x": 1}])
async def test_put_needs_a_known_role(db_conn, payload):
    owner, ben = await _user(db_conn, "o"), await _user(db_conn, "b")
    pid = await seed.make_project(db_conn, owner)
    async with _client(db_conn, _p(owner)) as c:
        kwargs = {} if payload is None else {"json": payload}
        assert (await c.put(f"/v1/projects/{pid}/members/{ben}", **kwargs)).status_code == 422


async def test_same_role_is_a_no_op_and_a_change_is_recorded(db_conn):
    owner, ben = await _user(db_conn, "o"), await _user(db_conn, "b")
    pid = await seed.make_project(db_conn, owner, members=[(ben, "reader")])
    async with _client(db_conn, _p(owner)) as c:
        assert (await c.put(f"/v1/projects/{pid}/members/{ben}", json={"role": "reader"})).status_code == 200
        assert (await c.put(f"/v1/projects/{pid}/members/{ben}", json={"role": "maintainer"})).status_code == 200
    assert await _kinds(db_conn, pid) == [("member.role_changed", {"from": "reader", "to": "maintainer"})]


async def test_maintainers_manage_up_to_maintainer_but_never_owners(db_conn):
    owner, m, x = await _user(db_conn, "o"), await _user(db_conn, "m"), await _user(db_conn, "x")
    pid = await seed.make_project(db_conn, owner, members=[(m, "maintainer")])
    async with _client(db_conn, _p(m)) as c:
        assert (await c.put(f"/v1/projects/{pid}/members/{x}", json={"role": "maintainer"})).status_code == 200
        for call in (c.put(f"/v1/projects/{pid}/members/{x}", json={"role": "owner"}),
                     c.put(f"/v1/projects/{pid}/members/{owner}", json={"role": "reader"}),
                     c.delete(f"/v1/projects/{pid}/members/{owner}"),
                     c.put(f"/v1/projects/{pid}/members/{m}", json={"role": "owner"})):
            r = await call
            assert r.status_code == 403 and r.json()["detail"]["required"] == "owner"
        assert (await c.delete(f"/v1/projects/{pid}/members/{x}")).status_code == 204
    assert await _role(db_conn, pid, owner) == "owner"


async def test_readers_and_contributors_cannot_manage_members(db_conn):
    owner, r_, x = await _user(db_conn, "o"), await _user(db_conn, "r"), await _user(db_conn, "x")
    pid = await seed.make_project(db_conn, owner, members=[(r_, "contributor")])
    async with _client(db_conn, _p(r_)) as c:
        r = await c.put(f"/v1/projects/{pid}/members/{x}", json={"role": "reader"})
    assert r.status_code == 403 and r.json()["detail"]["required"] == "maintainer"


async def test_non_member_gets_404_before_the_user_id_is_examined(db_conn):
    owner, stranger = await _user(db_conn, "o"), await _user(db_conn, "s")
    pid = await seed.make_project(db_conn, owner)
    async with _client(db_conn, _p(stranger)) as c:
        r = await c.put(f"/v1/projects/{pid}/members/not-a-uuid", json={"role": "reader"})
    assert r.status_code == 404
    assert r.json()["detail"]["message"] == authz.PROJECT_NOT_FOUND


@pytest.mark.parametrize("target", ["missing", "malformed", "disabled"])
async def test_unknown_malformed_or_disabled_people_are_not_found(db_conn, target):
    owner = await _user(db_conn, "o")
    pid = await seed.make_project(db_conn, owner)
    uid = {"missing": "00000000-0000-0000-0000-00000000beef", "malformed": "nope",
           "disabled": await _user(db_conn, "d", status="disabled")}[target]
    async with _client(db_conn, _p(owner)) as c:
        r = await c.put(f"/v1/projects/{pid}/members/{uid}", json={"role": "reader"})
        assert r.status_code == 404
        assert r.json()["detail"]["message"] == "User not found"
        assert (await c.get(f"/v1/projects/{pid}")).status_code == 200  # transaction still usable


async def test_pending_people_can_be_added(db_conn):
    owner, pending = await _user(db_conn, "o"), await _user(db_conn, "p", status="pending")
    pid = await seed.make_project(db_conn, owner)
    async with _client(db_conn, _p(owner)) as c:
        r = await c.put(f"/v1/projects/{pid}/members/{pending}", json={"role": "reader"})
    assert r.status_code == 200 and r.json()["status"] == "pending"


async def test_the_last_owner_cannot_demote_remove_or_leave(db_conn, caplog):
    owner, second = await _user(db_conn, "o"), await _user(db_conn, "s")
    pid = await seed.make_project(db_conn, owner)
    async with _client(db_conn, _p(owner)) as c:
        for call in (c.put(f"/v1/projects/{pid}/members/{owner}", json={"role": "maintainer"}),
                     c.delete(f"/v1/projects/{pid}/members/{owner}")):
            r = await call
            assert r.status_code == 409 and r.json()["detail"]["error"] == "last_owner"
        assert (await c.put(f"/v1/projects/{pid}/members/{second}", json={"role": "owner"})).status_code == 200
        assert (await c.put(f"/v1/projects/{pid}/members/{owner}", json={"role": "maintainer"})).status_code == 200
    assert "last-owner guard" in caplog.text


async def test_any_member_can_leave(db_conn):
    owner, ben = await _user(db_conn, "o"), await _user(db_conn, "b")
    pid = await seed.make_project(db_conn, owner, members=[(ben, "reader")])
    async with _client(db_conn, _p(ben)) as c:
        assert (await c.delete(f"/v1/projects/{pid}/members/{ben}")).status_code == 204
        assert (await c.get(f"/v1/projects/{pid}")).status_code == 404
    assert await _kinds(db_conn, pid) == [("member.left", {"role": "reader"})]


async def test_admin_adds_themselves_to_an_ownerless_project(db_conn, caplog):
    admin = await _user(db_conn, "a")
    pid = await seed.make_project(db_conn, None)
    with caplog.at_level(logging.WARNING):
        async with _client(db_conn, _p(admin, admin=True)) as c:
            r = await c.put(f"/v1/projects/{pid}/members/{admin}", json={"role": "owner"})
    assert r.status_code == 200 and r.json()["added_via"] == "admin"
    assert f"admin {admin} added themselves to project {pid} as owner" in caplog.text


async def test_members_list_owners_first_and_email_only_when_shown(db_conn, monkeypatch):
    owner, ann, zed = (await _user(db_conn, "o", first="Olu"), await _user(db_conn, "a", first="Ann"),
                       await _user(db_conn, "z", first="Zed"))
    pid = await seed.make_project(db_conn, owner, members=[(zed, "maintainer"), (ann, "reader")])
    async with _client(db_conn, _p(ann)) as c:
        rows = (await c.get(f"/v1/projects/{pid}/members")).json()
        assert [(m["name"], m["role"]) for m in rows] == [
            ("Olu", "owner"), ("Zed", "maintainer"), ("Ann", "reader")]
        assert all("email" not in m for m in rows)
        monkeypatch.setenv("USER_DIRECTORY_SHOW_EMAIL", "true")
        rows = (await c.get(f"/v1/projects/{pid}/members")).json()
    assert rows[0]["email"] == "o@x.io"


async def test_a_removed_member_immediately_loses_read_access(db_conn):
    """§11 read access: membership is the only path; removing it closes it."""
    owner, ben = await _user(db_conn, "o"), await _user(db_conn, "b")
    pid = await seed.make_project(db_conn, owner, members=[(ben, "reader")])
    await seed.seed_doc(db_conn, "c" * 64, owner, project_ids=[pid])
    await authz.assert_can_read_doc(db_conn, "c" * 64, ben)  # a Reader reads
    async with _client(db_conn, _p(owner)) as c:
        assert (await c.delete(f"/v1/projects/{pid}/members/{ben}")).status_code == 204
    with pytest.raises(HTTPException) as e:
        await authz.assert_can_read_doc(db_conn, "c" * 64, ben)
    assert e.value.status_code == 404


async def test_two_owners_demoting_each_other_at_once_leave_exactly_one_owner():
    """§3.3. Without the project row lock, B's request would read the state
    before A's commit, see "another Owner remains", and both demotions would
    commit: zero Owners. With it, B waits for A and is then refused."""
    tag = secrets.token_hex(4)
    setup = await AsyncConnection.connect(TEST_URL, autocommit=True)
    a_conn = await AsyncConnection.connect(TEST_URL)
    b_conn = await AsyncConnection.connect(TEST_URL)
    pid, ids, b_task = None, [], None
    try:
        for who in ("a", "b"):
            # Direct INSERTs: on a committed connection, resolve_or_provision_user's
            # first-login branch would claim the seed admin row, and this test's
            # cleanup would then delete it.
            cur = await setup.execute(
                "INSERT INTO users (oidc_iss, oidc_sub, email, role, status) "
                "VALUES ('race', %s, %s, 'member', 'active') RETURNING id",
                (f"{who}-{tag}", f"{who}-{tag}@race.example.com"))
            ids.append(str((await cur.fetchone())[0]))
        a, b = ids
        pid = await seed.make_project(setup, a, f"Race {tag}", members=[(b, "owner")])

        await project_members.put_member(a_conn, _p(a), pid, b, "maintainer", show_email=False)
        b_task = asyncio.create_task(
            project_members.put_member(b_conn, _p(b), pid, a, "maintainer", show_email=False))
        await asyncio.sleep(0.5)
        assert not b_task.done()  # waiting on the project row lock A holds
        await a_conn.commit()
        with pytest.raises(HTTPException) as e:
            await b_task
        assert e.value.status_code == 403  # B is a Maintainer now; Owner rows are Owner-only
        await b_conn.rollback()
        cur = await setup.execute(
            "SELECT user_id FROM project_members WHERE project_id = %s AND role = 'owner'", (pid,))
        assert [str(r[0]) for r in await cur.fetchall()] == [a]
    finally:
        await _race_cleanup(setup, a_conn, b_conn, b_task, pid, ids)
    assert await _seed_admin_survives(setup)
    await setup.close()


async def test_two_sole_owners_leaving_at_once_leave_exactly_one_owner():
    """§3.3, the leave route. Two Owners, nobody else. Without the row lock
    both leaves see "another Owner remains" and commit: zero Owners."""
    tag = secrets.token_hex(4)
    setup = await AsyncConnection.connect(TEST_URL, autocommit=True)
    a_conn = await AsyncConnection.connect(TEST_URL)
    b_conn = await AsyncConnection.connect(TEST_URL)
    pid, ids, b_task = None, [], None
    try:
        for who in ("a", "b"):
            cur = await setup.execute(
                "INSERT INTO users (oidc_iss, oidc_sub, email, role, status) "
                "VALUES ('race', %s, %s, 'member', 'active') RETURNING id",
                (f"{who}-{tag}", f"{who}-{tag}@race.example.com"))
            ids.append(str((await cur.fetchone())[0]))
        a, b = ids
        pid = await seed.make_project(setup, a, f"Race {tag}", members=[(b, "owner")])

        await project_members.remove_member(a_conn, _p(a), pid, a)  # A's transaction stays open
        b_task = asyncio.create_task(project_members.remove_member(b_conn, _p(b), pid, b))
        await asyncio.sleep(0.5)
        assert not b_task.done()  # waiting on the project row lock A holds
        await a_conn.commit()
        with pytest.raises(HTTPException) as e:
            await b_task
        assert e.value.status_code == 409 and e.value.detail["error"] == "last_owner"
        await b_conn.rollback()
        cur = await setup.execute(
            "SELECT user_id FROM project_members WHERE project_id = %s AND role = 'owner'", (pid,))
        assert [str(r[0]) for r in await cur.fetchall()] == [b]
    finally:
        await _race_cleanup(setup, a_conn, b_conn, b_task, pid, ids)
    assert await _seed_admin_survives(setup)
    await setup.close()


async def _race_cleanup(setup, a_conn, b_conn, b_task, pid, ids):
    """Stop a still-waiting task before closing its connection; delete only this test's rows."""
    if b_task is not None and not b_task.done():
        b_task.cancel()
        try:
            await b_task
        except (asyncio.CancelledError, HTTPException):
            pass
    await a_conn.close()
    await b_conn.close()
    if pid:
        await setup.execute("DELETE FROM projects WHERE id = %s", (pid,))
    if ids:
        await setup.execute("DELETE FROM users WHERE id = ANY(%s::uuid[])", (ids,))


async def _seed_admin_survives(setup) -> bool:
    cur = await setup.execute("SELECT 1 FROM users WHERE id = %s",
                              ("00000000-0000-0000-0000-000000000001",))
    return await cur.fetchone() == (1,)
