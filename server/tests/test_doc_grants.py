import uuid
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
HEX3 = "c" * 64
HEX4 = "d" * 64
HEX5 = "e" * 64


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


async def test_owner_adds_and_removes_grant(db_conn, docs_app):
    owner = await _member(db_conn, "owner")
    grantee = await _member(db_conn, "grantee")
    await _insert_doc(db_conn, HEX, owner.user_id)
    docs_app.dependency_overrides[deps.get_current_user] = lambda: owner
    async with _client(docs_app) as client:
        assert (await client.put(f"/v1/docs/{HEX}/grants/{grantee.user_id}", json={})).status_code == 204
        assert (await client.delete(f"/v1/docs/{HEX}/grants/{grantee.user_id}", )).status_code == 204


async def test_non_owner_cannot_add_grant(db_conn, docs_app):
    owner = await _member(db_conn, "owner")
    other = await _member(db_conn, "other")
    await _insert_doc(db_conn, HEX2, owner.user_id)
    docs_app.dependency_overrides[deps.get_current_user] = lambda: other
    async with _client(docs_app) as client:
        assert (await client.put(f"/v1/docs/{HEX2}/grants/{other.user_id}", json={})).status_code == 404


async def test_non_owner_cannot_remove_grant(db_conn, docs_app):
    owner = await _member(db_conn, "owner3")
    other = await _member(db_conn, "other3")
    grantee = await _member(db_conn, "grantee3")
    await _insert_doc(db_conn, HEX3, owner.user_id)
    # Grant as the owner first so there's something a non-owner could try to remove.
    docs_app.dependency_overrides[deps.get_current_user] = lambda: owner
    async with _client(docs_app) as client:
        assert (await client.put(f"/v1/docs/{HEX3}/grants/{grantee.user_id}", json={})).status_code == 204
    docs_app.dependency_overrides[deps.get_current_user] = lambda: other
    async with _client(docs_app) as client:
        r = await client.delete(f"/v1/docs/{HEX3}/grants/{grantee.user_id}")
        assert r.status_code == 404


async def test_grant_nonexistent_user_404(db_conn, docs_app):
    owner = await _member(db_conn, "owner4")
    await _insert_doc(db_conn, HEX4, owner.user_id)
    docs_app.dependency_overrides[deps.get_current_user] = lambda: owner
    fake_uid = str(uuid.uuid4())
    async with _client(docs_app) as client:
        r = await client.put(f"/v1/docs/{HEX4}/grants/{fake_uid}", json={})
        assert r.status_code == 404
        # Prove the FK-violation rollback (savepoint) didn't abort the
        # outer transaction — a follow-up query on the same connection
        # should still succeed.
        r2 = await client.get(f"/v1/docs/{HEX4}")
        assert r2.status_code == 200


async def test_grant_malformed_user_404(db_conn, docs_app):
    owner = await _member(db_conn, "owner5")
    await _insert_doc(db_conn, HEX5, owner.user_id)
    docs_app.dependency_overrides[deps.get_current_user] = lambda: owner
    async with _client(docs_app) as client:
        r = await client.put(f"/v1/docs/{HEX5}/grants/not-a-uuid", json={})
        assert r.status_code == 404
