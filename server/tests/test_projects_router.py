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
