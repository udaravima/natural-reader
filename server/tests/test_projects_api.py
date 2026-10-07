"""A0 §9.1: projects — the project object with `my_role` and `can`, the
creation policy and per-user limit, and events for changes."""
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import authz, deps
from server.auth.users import resolve_or_provision_user, set_status
from server.routers import projects as projects_router
from server.tests import seed

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


async def _events(db_conn, pid):
    cur = await db_conn.execute(
        "SELECT kind, details FROM project_events WHERE project_id=%s ORDER BY id", (pid,))
    return [(r[0], r[1]) for r in await cur.fetchall()]


async def test_create_returns_the_project_object_and_records_creation(db_conn):
    me = await _user(db_conn, "c1", first="Cara")
    async with _client(db_conn, _p(me)) as c:
        r = await c.post("/v1/projects", json={"name": "  Q3 Audit ", "description": "Evidence"})
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Q3 Audit"
    assert body["my_role"] == "owner" and body["member_count"] == 1 and body["doc_count"] == 0
    assert body["created_by"] == {"id": me, "name": "Cara"}
    assert body["can"] == authz.can_for("owner", False)
    assert "is_owner" not in body and "owner_user_id" not in body
    assert await _events(db_conn, body["id"]) == [("project.created", {"name": "Q3 Audit"})]


@pytest.mark.parametrize("method,payload", [
    ("post", {"name": "   "}), ("patch", {"name": "  "}), ("patch", {"name": None}),
])
async def test_blank_project_name_is_422(db_conn, method, payload):
    me = await _user(db_conn, "b1")
    pid = await seed.make_project(db_conn, me)
    async with _client(db_conn, _p(me)) as c:
        url = "/v1/projects" if method == "post" else f"/v1/projects/{pid}"
        assert (await getattr(c, method)(url, json=payload)).status_code == 422


async def test_list_shows_only_my_projects_with_my_role_and_counts(db_conn):
    me, other = await _user(db_conn, "l1"), await _user(db_conn, "l2")
    mine = await seed.make_project(db_conn, other, "Theirs, I read", members=[(me, "reader")])
    await seed.make_project(db_conn, other, "Not mine")
    await seed.seed_doc(db_conn, "a" * 64, other, project_ids=[mine])
    async with _client(db_conn, _p(me)) as c:
        rows = (await c.get("/v1/projects")).json()
    assert [(r["name"], r["my_role"], r["member_count"], r["doc_count"]) for r in rows] == [
        ("Theirs, I read", "reader", 2, 1)]
    assert rows[0]["can"] == authz.can_for("reader", False)


async def test_get_project_member_200_non_member_404_bad_id_422(db_conn):
    owner, other = await _user(db_conn, "g1"), await _user(db_conn, "g2")
    pid = await seed.make_project(db_conn, owner)
    async with _client(db_conn, _p(owner)) as c:
        assert (await c.get(f"/v1/projects/{pid}")).json()["my_role"] == "owner"
        assert (await c.get("/v1/projects/not-a-uuid")).status_code == 422
    async with _client(db_conn, _p(other)) as c:
        r = await c.get(f"/v1/projects/{pid}")
    assert r.status_code == 404
    assert r.json()["detail"] == {"error": "not_found", "message": authz.PROJECT_NOT_FOUND}


async def test_admin_who_is_not_a_member_sees_the_project_with_admin_powers(db_conn):
    owner, admin = await _user(db_conn, "a1"), await _user(db_conn, "a2")
    pid = await seed.make_project(db_conn, owner)
    async with _client(db_conn, _p(admin, admin=True)) as c:
        body = (await c.get(f"/v1/projects/{pid}")).json()
    assert body["my_role"] is None
    assert body["can"] == authz.can_for(None, True)


async def test_patch_needs_maintainer_and_records_only_real_changes(db_conn):
    owner, m, c_ = await _user(db_conn, "p1"), await _user(db_conn, "p2"), await _user(db_conn, "p3")
    pid = await seed.make_project(db_conn, owner, "Old", members=[(m, "maintainer"), (c_, "contributor")])
    async with _client(db_conn, _p(c_)) as c:
        r = await c.patch(f"/v1/projects/{pid}", json={"name": "Hijack"})
    assert r.status_code == 403 and r.json()["detail"]["required"] == "maintainer"
    async with _client(db_conn, _p(m)) as c:
        r = await c.patch(f"/v1/projects/{pid}", json={"name": "New", "description": "Why"})
        assert r.status_code == 200 and r.json()["name"] == "New"
        await c.patch(f"/v1/projects/{pid}", json={"name": "New"})  # no change, no event
    assert await _events(db_conn, pid) == [
        ("project.renamed", {"from": "Old", "to": "New"}), ("project.described", {})]


async def test_delete_needs_owner_or_admin(db_conn):
    owner, m, admin = await _user(db_conn, "d1"), await _user(db_conn, "d2"), await _user(db_conn, "d3")
    p1 = await seed.make_project(db_conn, owner, members=[(m, "maintainer")])
    p2 = await seed.make_project(db_conn, owner)
    async with _client(db_conn, _p(m)) as c:
        assert (await c.delete(f"/v1/projects/{p1}")).status_code == 403
    async with _client(db_conn, _p(owner)) as c:
        assert (await c.delete(f"/v1/projects/{p1}")).status_code == 204
    async with _client(db_conn, _p(admin, admin=True)) as c:
        assert (await c.delete(f"/v1/projects/{p2}")).status_code == 204


async def test_creation_restricted_to_admins(db_conn, monkeypatch):
    monkeypatch.setenv("PROJECT_CREATION", "admins")
    me, admin = await _user(db_conn, "r1"), await _user(db_conn, "r2")
    async with _client(db_conn, _p(me)) as c:
        r = await c.post("/v1/projects", json={"name": "X"})
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "project_creation_restricted"
    async with _client(db_conn, _p(admin, admin=True)) as c:
        assert (await c.post("/v1/projects", json={"name": "X"})).status_code == 201


async def test_project_limit_default_override_unlimited_and_admins(db_conn, monkeypatch, caplog):
    monkeypatch.setenv("PROJECT_LIMIT_PER_USER", "1")
    me, admin = await _user(db_conn, "m1"), await _user(db_conn, "m2")
    async with _client(db_conn, _p(me)) as c:
        assert (await c.post("/v1/projects", json={"name": "One"})).status_code == 201
        r = await c.post("/v1/projects", json={"name": "Two"})
        assert r.status_code == 429
        assert r.json()["detail"]["error"] == "project_limit" and r.json()["detail"]["limit"] == 1
        await db_conn.execute("UPDATE users SET project_limit = 2 WHERE id = %s", (me,))
        assert (await c.post("/v1/projects", json={"name": "Two"})).status_code == 201
        await db_conn.execute("UPDATE users SET project_limit = 0 WHERE id = %s", (me,))
        assert (await c.post("/v1/projects", json={"name": "Three"})).status_code == 201
    assert "project limit reached" in caplog.text
    async with _client(db_conn, _p(admin, admin=True)) as c:
        for n in range(3):
            assert (await c.post("/v1/projects", json={"name": f"A{n}"})).status_code == 201


async def test_admin_creates_a_project_for_someone_else(db_conn):
    admin, owner = await _user(db_conn, "o1"), await _user(db_conn, "o2", status="pending")
    gone = await _user(db_conn, "o3", status="disabled")
    async with _client(db_conn, _p(admin, admin=True)) as c:
        r = await c.post("/v1/projects", json={"name": "Theirs", "owner_user_id": owner})
        assert r.status_code == 201
        body = r.json()
        assert body["my_role"] is None and body["created_by"]["id"] == admin
        assert (await c.post("/v1/projects", json={"name": "X", "owner_user_id": gone})).status_code == 404
    cur = await db_conn.execute(
        "SELECT user_id, role, added_via FROM project_members WHERE project_id=%s", (body["id"],))
    assert [(str(r[0]), r[1], r[2]) for r in await cur.fetchall()] == [(owner, "owner", "admin")]
    async with _client(db_conn, _p(owner)) as c:
        r = await c.post("/v1/projects", json={"name": "X", "owner_user_id": admin})
    assert r.status_code == 403 and r.json()["detail"]["required"] == "admin"


async def test_project_routes_need_the_reader_capability(db_conn):
    me = await _user(db_conn, "n1")
    capless = deps.Principal(user_id=me, email="n1@x.io", role="member", capabilities=frozenset())
    async with _client(db_conn, capless) as c:
        assert (await c.get("/v1/projects")).status_code == 403
        assert (await c.post("/v1/projects", json={"name": "X"})).status_code == 403
