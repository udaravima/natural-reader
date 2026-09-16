from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.auth.users import resolve_or_provision_user, set_status
from server.routers import docs as docs_router

HEX = "a" * 64
HEX2 = "b" * 64


@pytest.fixture
def docs_app(db_conn, monkeypatch):
    # Route the docs router's pool access at our transactional test connection.
    class _PoolShim:
        @asynccontextmanager
        async def connection(self):
            yield db_conn

    monkeypatch.setattr(docs_router, "get_pool", lambda: _PoolShim())
    monkeypatch.setattr(docs_router, "is_ready", lambda: True)

    app = FastAPI()
    app.include_router(docs_router.router)
    return app


def _client(app):
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _member(db_conn, sub):
    u = await resolve_or_provision_user(db_conn, iss="i", sub=sub, email=f"{sub}@x.io")
    await set_status(db_conn, u["id"], "active")
    return deps.Principal(user_id=u["id"], email=u["email"], role="member")


async def _insert_doc(db_conn, doc_id, owner_id):
    await db_conn.execute(
        "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, user_id) "
        "VALUES (%s,'f','text',1,%s)",
        (doc_id, owner_id),
    )


async def test_get_others_doc_is_404(db_conn, docs_app):
    owner = await _member(db_conn, "owner")
    other = await _member(db_conn, "other")
    await _insert_doc(db_conn, HEX, owner.user_id)
    docs_app.dependency_overrides[deps.get_current_user] = lambda: other
    async with _client(docs_app) as client:
        assert (await client.get(f"/v1/docs/{HEX}")).status_code == 404


async def test_owner_can_get(db_conn, docs_app):
    owner = await _member(db_conn, "owner")
    await _insert_doc(db_conn, HEX2, owner.user_id)
    docs_app.dependency_overrides[deps.get_current_user] = lambda: owner
    async with _client(docs_app) as client:
        assert (await client.get(f"/v1/docs/{HEX2}")).status_code == 200


async def test_register_stamps_owner(db_conn, docs_app):
    owner = await _member(db_conn, "owner")
    docs_app.dependency_overrides[deps.get_current_user] = lambda: owner
    async with _client(docs_app) as client:
        r = await client.post(
            "/v1/docs",
            json={"doc_id": HEX, "file_name": "f", "file_type": "text", "size_bytes": 1},
        )
        assert r.status_code == 200
    cur = await db_conn.execute("SELECT user_id FROM documents WHERE doc_id=%s", (HEX,))
    assert str((await cur.fetchone())[0]) == owner.user_id


async def test_unauthenticated_is_401(db_conn, docs_app):
    # No get_current_user override -> real dependency -> 401 without credentials.
    async def _conn_override():
        yield db_conn

    docs_app.dependency_overrides[deps.get_conn] = _conn_override
    async with _client(docs_app) as client:
        assert (await client.get(f"/v1/docs/{HEX}")).status_code == 401
