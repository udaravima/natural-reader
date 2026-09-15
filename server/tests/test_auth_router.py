import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.routers import auth as auth_router


def _app(db_conn, principal=None):
    app = FastAPI()

    async def _conn_override():
        yield db_conn

    app.dependency_overrides[deps.get_conn] = _conn_override
    if principal is not None:
        app.dependency_overrides[deps.get_current_user] = lambda: principal
    app.include_router(auth_router.router)
    return app


async def _get(app, path, headers=None):
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.get(path, headers=headers or {})


async def test_me_returns_principal(db_conn):
    p = deps.Principal(user_id="u1", email="a@x.io", role="admin")
    r = await _get(_app(db_conn, principal=p), "/v1/auth/me")
    assert r.status_code == 200 and r.json()["email"] == "a@x.io"


async def test_me_401_without_principal(db_conn):
    r = await _get(_app(db_conn), "/v1/auth/me")
    assert r.status_code == 401
