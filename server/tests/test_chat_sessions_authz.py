from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.auth.users import resolve_or_provision_user, set_status
from server.routers import chat_sessions as chat_router


@pytest.fixture
def chat_app(db_conn, monkeypatch):
    class _PoolShim:
        @asynccontextmanager
        async def connection(self):
            yield db_conn

    monkeypatch.setattr(chat_router, "get_pool", lambda: _PoolShim())
    monkeypatch.setattr(chat_router, "is_ready", lambda: True)

    app = FastAPI()
    app.include_router(chat_router.router)
    return app


def _client(app):
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _member(db_conn, sub):
    u = await resolve_or_provision_user(db_conn, iss="i", sub=sub, email=f"{sub}@x.io")
    await set_status(db_conn, u["id"], "active")
    return deps.Principal(user_id=u["id"], email=u["email"], role="member")


async def _session(db_conn, sid, owner_id):
    await db_conn.execute(
        "INSERT INTO chat_sessions (id, user_id) VALUES (%s, %s)", (sid, owner_id)
    )


async def test_list_returns_only_own(db_conn, chat_app):
    owner = await _member(db_conn, "owner")
    other = await _member(db_conn, "other")
    await _session(db_conn, "s-own", owner.user_id)
    await _session(db_conn, "s-other", other.user_id)
    chat_app.dependency_overrides[deps.get_current_user] = lambda: owner
    async with _client(chat_app) as client:
        rows = (await client.get("/v1/chat/sessions")).json()
    assert [r["id"] for r in rows] == ["s-own"]


async def test_get_others_session_is_404(db_conn, chat_app):
    owner = await _member(db_conn, "owner")
    other = await _member(db_conn, "other")
    await _session(db_conn, "s-own", owner.user_id)
    chat_app.dependency_overrides[deps.get_current_user] = lambda: other
    async with _client(chat_app) as client:
        assert (await client.get("/v1/chat/sessions/s-own")).status_code == 404


async def test_upsert_stamps_owner(db_conn, chat_app):
    owner = await _member(db_conn, "owner")
    chat_app.dependency_overrides[deps.get_current_user] = lambda: owner
    async with _client(chat_app) as client:
        r = await client.put("/v1/chat/sessions/s-new", json={"id": "s-new"})
        assert r.status_code == 200
    cur = await db_conn.execute(
        "SELECT user_id FROM chat_sessions WHERE id='s-new'"
    )
    assert str((await cur.fetchone())[0]) == owner.user_id


async def test_unauthenticated_list_is_401(db_conn, chat_app):
    async def _conn_override():
        yield db_conn

    chat_app.dependency_overrides[deps.get_conn] = _conn_override
    async with _client(chat_app) as client:
        assert (await client.get("/v1/chat/sessions")).status_code == 401
