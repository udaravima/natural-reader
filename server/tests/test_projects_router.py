import uuid

import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from server.auth import deps
from server.auth.users import resolve_or_provision_user
from server.routers import projects as projects_router


def _app(db_conn, principal):
    app = FastAPI()
    async def _conn(): yield db_conn
    app.dependency_overrides[deps.get_conn] = _conn
    app.dependency_overrides[deps.get_current_user] = lambda: principal
    app.include_router(projects_router.router)
    return app


async def test_create_and_list_project(db_conn):
    u = await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")
    p = deps.Principal(user_id=u["id"], email=u["email"], role="member")
    async with httpx.AsyncClient(transport=ASGITransport(app=_app(db_conn, p)),
                                 base_url="http://t") as c:
        r = await c.post("/v1/projects", json={"name": "Alpha"})
        assert r.status_code == 201 and r.json()["name"] == "Alpha"
        rows = (await c.get("/v1/projects")).json()
        assert len(rows) == 1 and rows[0]["is_owner"] is True


async def test_non_owner_cannot_patch(db_conn):
    owner = await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="o@x.io")
    other = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="x@x.io")
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P') RETURNING id",
        (owner["id"],))
    pid = str((await cur.fetchone())[0])
    p = deps.Principal(user_id=other["id"], email=other["email"], role="member")
    async with httpx.AsyncClient(transport=ASGITransport(app=_app(db_conn, p)),
                                 base_url="http://t") as c:
        r = await c.patch(f"/v1/projects/{pid}", json={"name": "Hijack"})
        assert r.status_code == 404


async def test_owner_adds_and_removes_member(db_conn):
    owner = await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="o@x.io")
    member = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="m@x.io")
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P') RETURNING id",
        (owner["id"],))
    pid = str((await cur.fetchone())[0])
    p = deps.Principal(user_id=owner["id"], email=owner["email"], role="member")
    async with httpx.AsyncClient(transport=ASGITransport(app=_app(db_conn, p)),
                                 base_url="http://t") as c:
        assert (await c.put(f"/v1/projects/{pid}/members/{member['id']}", json={})).status_code == 204
        assert (await c.delete(f"/v1/projects/{pid}/members/{member['id']}", )).status_code == 204


async def test_non_owner_cannot_delete_project(db_conn):
    owner = await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="o@x.io")
    other = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="x@x.io")
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P') RETURNING id",
        (owner["id"],))
    pid = str((await cur.fetchone())[0])
    p = deps.Principal(user_id=other["id"], email=other["email"], role="member")
    async with httpx.AsyncClient(transport=ASGITransport(app=_app(db_conn, p)),
                                 base_url="http://t") as c:
        r = await c.delete(f"/v1/projects/{pid}")
        assert r.status_code == 404


async def test_non_owner_cannot_add_member(db_conn):
    owner = await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="o@x.io")
    other = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="x@x.io")
    target = await resolve_or_provision_user(db_conn, iss="i", sub="s3", email="t@x.io")
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P') RETURNING id",
        (owner["id"],))
    pid = str((await cur.fetchone())[0])
    p = deps.Principal(user_id=other["id"], email=other["email"], role="member")
    async with httpx.AsyncClient(transport=ASGITransport(app=_app(db_conn, p)),
                                 base_url="http://t") as c:
        r = await c.put(f"/v1/projects/{pid}/members/{target['id']}", json={})
        assert r.status_code == 404


async def test_non_owner_cannot_remove_member(db_conn):
    owner = await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="o@x.io")
    other = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="x@x.io")
    target = await resolve_or_provision_user(db_conn, iss="i", sub="s3", email="t@x.io")
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P') RETURNING id",
        (owner["id"],))
    pid = str((await cur.fetchone())[0])
    p = deps.Principal(user_id=other["id"], email=other["email"], role="member")
    async with httpx.AsyncClient(transport=ASGITransport(app=_app(db_conn, p)),
                                 base_url="http://t") as c:
        r = await c.delete(f"/v1/projects/{pid}/members/{target['id']}")
        assert r.status_code == 404


async def test_list_excludes_other_users_projects(db_conn):
    """The owner-or-member listing predicate in list_projects excludes
    projects the caller has no relationship to — the security property that
    was previously only verified by reading the SQL."""
    a = await resolve_or_provision_user(db_conn, iss="i", sub="a1", email="a@x.io")
    b = await resolve_or_provision_user(db_conn, iss="i", sub="b1", email="b@x.io")
    pa = deps.Principal(user_id=a["id"], email=a["email"], role="member")
    pb = deps.Principal(user_id=b["id"], email=b["email"], role="member")
    async with httpx.AsyncClient(transport=ASGITransport(app=_app(db_conn, pa)),
                                 base_url="http://t") as c:
        r = await c.post("/v1/projects", json={"name": "A's project"})
        assert r.status_code == 201
    async with httpx.AsyncClient(transport=ASGITransport(app=_app(db_conn, pb)),
                                 base_url="http://t") as c:
        rows = (await c.get("/v1/projects")).json()
        assert all(row["name"] != "A's project" for row in rows)


async def test_add_member_nonexistent_user_404(db_conn):
    owner = await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="o@x.io")
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P') RETURNING id",
        (owner["id"],))
    pid = str((await cur.fetchone())[0])
    p = deps.Principal(user_id=owner["id"], email=owner["email"], role="member")
    fake_uid = str(uuid.uuid4())
    async with httpx.AsyncClient(transport=ASGITransport(app=_app(db_conn, p)),
                                 base_url="http://t") as c:
        r = await c.put(f"/v1/projects/{pid}/members/{fake_uid}", json={})
        assert r.status_code == 404
        # Prove the FK-violation rollback (savepoint) didn't abort the
        # outer transaction — a follow-up query on the same connection
        # should still succeed.
        rows = (await c.get("/v1/projects")).json()
        assert len(rows) == 1


async def test_add_member_malformed_user_404(db_conn):
    owner = await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="o@x.io")
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P') RETURNING id",
        (owner["id"],))
    pid = str((await cur.fetchone())[0])
    p = deps.Principal(user_id=owner["id"], email=owner["email"], role="member")
    async with httpx.AsyncClient(transport=ASGITransport(app=_app(db_conn, p)),
                                 base_url="http://t") as c:
        r = await c.put(f"/v1/projects/{pid}/members/not-a-uuid", json={})
        assert r.status_code == 404
