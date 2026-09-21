"""PATCH /v1/docs/{doc_id} — tags, project, admin-only owner reassignment.

Harness: this router does all DB work via `get_pool()` (see test_docs_authz.py
and test_docs_list.py), not `Depends(get_conn)`, so we follow the same
convention here — monkeypatch `docs_router.get_pool` to a shim that hands back
the test's transactional `db_conn`.
"""
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.auth.users import resolve_or_provision_user, set_status
from server.routers import docs as docs_router

pytestmark = pytest.mark.asyncio

HEX = "a" * 64
HEX2 = "b" * 64
HEX3 = "c" * 64


@pytest.fixture
def docs_app(db_conn, monkeypatch):
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


async def _user(db_conn, sub, role="member"):
    u = await resolve_or_provision_user(db_conn, iss="i", sub=sub, email=f"{sub}@x.io")
    await set_status(db_conn, u["id"], "active")
    return deps.Principal(user_id=u["id"], email=u["email"], role=role)


async def _insert_doc(db_conn, doc_id, owner_id):
    await db_conn.execute(
        "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, user_id) "
        "VALUES (%s,'f','text',1,%s)",
        (doc_id, owner_id),
    )


async def test_owner_sets_tags_and_project_reflected_in_response(db_conn, docs_app):
    owner = await _user(db_conn, "owner-patch")
    await _insert_doc(db_conn, HEX, owner.user_id)
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P') RETURNING id",
        (owner.user_id,),
    )
    project_id = str((await cur.fetchone())[0])

    docs_app.dependency_overrides[deps.get_current_user] = lambda: owner
    async with _client(docs_app) as client:
        r = await client.patch(
            f"/v1/docs/{HEX}",
            json={"tags": ["b", "a", "a"], "project_id": project_id},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["tags"] == ["a", "b"]  # sorted + deduped
        assert body["project_id"] == project_id


async def test_non_owner_patch_is_404(db_conn, docs_app):
    owner = await _user(db_conn, "owner-patch2")
    other = await _user(db_conn, "other-patch")
    await _insert_doc(db_conn, HEX2, owner.user_id)

    docs_app.dependency_overrides[deps.get_current_user] = lambda: other
    async with _client(docs_app) as client:
        r = await client.patch(f"/v1/docs/{HEX2}", json={"tags": ["x"]})
        assert r.status_code == 404


async def test_non_admin_passing_owner_user_id_is_403(db_conn, docs_app):
    owner = await _user(db_conn, "owner-patch3")
    new_owner = await _user(db_conn, "new-owner")
    await _insert_doc(db_conn, HEX3, owner.user_id)

    # Even the current owner can't self-serve a reassignment without admin role.
    docs_app.dependency_overrides[deps.get_current_user] = lambda: owner
    async with _client(docs_app) as client:
        r = await client.patch(
            f"/v1/docs/{HEX3}", json={"owner_user_id": new_owner.user_id}
        )
        assert r.status_code == 403


async def test_admin_reassigns_owner(db_conn, docs_app):
    owner = await _user(db_conn, "owner-patch4")
    new_owner = await _user(db_conn, "new-owner2")
    admin = await _user(db_conn, "admin-patch", role="admin")
    doc_id = "d" * 64
    await _insert_doc(db_conn, doc_id, owner.user_id)

    docs_app.dependency_overrides[deps.get_current_user] = lambda: admin
    async with _client(docs_app) as client:
        r = await client.patch(
            f"/v1/docs/{doc_id}", json={"owner_user_id": new_owner.user_id}
        )
        assert r.status_code == 200
        assert r.json()["doc_id"] == doc_id

    cur = await db_conn.execute(
        "SELECT user_id FROM documents WHERE doc_id = %s", (doc_id,)
    )
    assert str((await cur.fetchone())[0]) == new_owner.user_id
