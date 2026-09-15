import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.auth.users import resolve_or_provision_user
from server.routers import admin as admin_router


def _app(db_conn, principal):
    app = FastAPI()

    async def _conn_override():
        yield db_conn

    app.dependency_overrides[deps.get_conn] = _conn_override
    app.dependency_overrides[deps.get_current_user] = lambda: principal
    app.include_router(admin_router.router)
    return app


def _client(app):
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_admin_lists_and_activates(db_conn):
    admin = await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")
    pending = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    p = deps.Principal(user_id=admin["id"], email=admin["email"], role="admin")
    async with _client(_app(db_conn, p)) as client:
        assert len((await client.get("/v1/admin/users")).json()) == 2
        r = await client.patch(
            f"/v1/admin/users/{pending['id']}", json={"status": "active"}
        )
        assert r.status_code == 200 and r.json()["status"] == "active"


async def test_member_forbidden(db_conn):
    await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")
    m = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    p = deps.Principal(user_id=m["id"], email=m["email"], role="member")
    async with _client(_app(db_conn, p)) as client:
        assert (await client.get("/v1/admin/users")).status_code == 403
