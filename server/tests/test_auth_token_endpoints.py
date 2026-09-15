import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.auth.users import resolve_or_provision_user
from server.routers import auth as auth_router


def _app(db_conn, principal):
    app = FastAPI()

    async def _conn_override():
        yield db_conn

    app.dependency_overrides[deps.get_conn] = _conn_override
    app.dependency_overrides[deps.get_current_user] = lambda: principal
    app.include_router(auth_router.router)
    return app


async def test_create_list_revoke(db_conn):
    u = await resolve_or_provision_user(db_conn, iss="i", sub="s", email="a@x.io")
    p = deps.Principal(user_id=u["id"], email=u["email"], role="admin")
    transport = ASGITransport(app=_app(db_conn, p))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        created = await client.post("/v1/auth/tokens", json={"name": "cli"})
        assert created.status_code == 200
        body = created.json()
        assert body["token"].startswith("nrp_")
        tid = body["id"]

        listed = (await client.get("/v1/auth/tokens")).json()
        assert listed[0]["name"] == "cli" and "token" not in listed[0]

        assert (await client.delete(f"/v1/auth/tokens/{tid}")).status_code == 204
