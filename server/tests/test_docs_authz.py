from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.auth.users import resolve_or_provision_user, set_status
from server.routers import docs as docs_router
from server.tests import seed

HEX = "a" * 64
HEX2 = "b" * 64
HEX3 = "c" * 64


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
    return deps.Principal(user_id=u["id"], email=u["email"], role="member",
                          capabilities=frozenset({"reader"}))


async def _insert_doc(db_conn, doc_id, owner_id):
    await seed.seed_doc(db_conn, doc_id, owner_id, file_name="f", file_type="text")


async def test_get_others_doc_is_404(db_conn, docs_app):
    owner = await _member(db_conn, "owner")
    other = await _member(db_conn, "other")
    await _insert_doc(db_conn, HEX, owner.user_id)
    docs_app.dependency_overrides[deps.get_current_user] = lambda: other
    async with _client(docs_app) as client:
        assert (await client.get(f"/v1/docs/{HEX}")).status_code == 404


async def test_upload_holder_can_get(db_conn, docs_app):
    owner = await _member(db_conn, "owner")
    await _insert_doc(db_conn, HEX2, owner.user_id)
    docs_app.dependency_overrides[deps.get_current_user] = lambda: owner
    async with _client(docs_app) as client:
        assert (await client.get(f"/v1/docs/{HEX2}")).status_code == 200


async def test_register_gives_caller_an_upload_entry(db_conn, docs_app):
    first = await _member(db_conn, "first")
    second = await _member(db_conn, "second")
    body = {"doc_id": HEX, "file_name": "f", "file_type": "text", "size_bytes": 1}
    for who in (first, second):  # content has no owner: nobody can "hijack" it
        docs_app.dependency_overrides[deps.get_current_user] = lambda who=who: who
        async with _client(docs_app) as client:
            r = await client.post("/v1/docs", json=body)
            assert r.status_code == 200
            assert r.json()["added_via"] == "upload"
    cur = await db_conn.execute(
        "SELECT user_id, added_via FROM library_entries WHERE doc_id=%s", (HEX,))
    assert {(str(u), v) for u, v in await cur.fetchall()} == {
        (first.user_id, "upload"), (second.user_id, "upload")}


async def test_recipient_can_read_but_not_change_content(db_conn, docs_app):
    # A share recipient reads via GET, but upload-holder routes (convert,
    # markdown delete, sharing) stay closed to them — 404. Re-indexing
    # `indexed` content is a content op: 409 content_shared (spec §4; a
    # recipient MAY resume content that isn't indexed — test_doc_pipeline).
    owner = await _member(db_conn, "owner3")
    recipient = await _member(db_conn, "recipient")
    await seed.seed_doc(db_conn, HEX3, owner.user_id, file_name="f", file_type="text",
                        state="indexed")
    await seed.share_doc(db_conn, HEX3, owner.user_id, recipient.user_id)
    docs_app.dependency_overrides[deps.get_current_user] = lambda: recipient
    async with _client(docs_app) as client:
        assert (await client.get(f"/v1/docs/{HEX3}")).status_code == 200
        r = await client.post(f"/v1/docs/{HEX3}/index")
        assert r.status_code == 409 and r.json()["detail"]["error"] == "content_shared"
        assert (await client.delete(f"/v1/docs/{HEX3}/markdown")).status_code == 404


async def test_recipient_delete_removes_only_their_entry(db_conn, docs_app):
    owner = await _member(db_conn, "owner4")
    recipient = await _member(db_conn, "recipient4")
    await _insert_doc(db_conn, HEX3, owner.user_id)
    await seed.share_doc(db_conn, HEX3, owner.user_id, recipient.user_id)
    docs_app.dependency_overrides[deps.get_current_user] = lambda: recipient
    async with _client(docs_app) as client:
        assert (await client.delete(f"/v1/docs/{HEX3}")).status_code == 204
        assert (await client.get(f"/v1/docs/{HEX3}")).status_code == 404
        r = await client.delete(f"/v1/docs/{HEX3}")  # nothing left to remove
        assert r.status_code == 404
        assert r.json()["detail"] == {"error": "not_found", "message": "Document not found"}
    docs_app.dependency_overrides[deps.get_current_user] = lambda: owner
    async with _client(docs_app) as client:
        r = await client.get(f"/v1/docs/{HEX3}")
        assert r.status_code == 200 and r.json()["added_via"] == "upload"


async def test_sole_holder_delete_gcs_content(db_conn, docs_app):
    owner = await _member(db_conn, "owner5")
    await _insert_doc(db_conn, HEX2, owner.user_id)
    docs_app.dependency_overrides[deps.get_current_user] = lambda: owner
    async with _client(docs_app) as client:
        assert (await client.delete(f"/v1/docs/{HEX2}")).status_code == 204
    cur = await db_conn.execute("SELECT 1 FROM documents WHERE doc_id=%s", (HEX2,))
    assert await cur.fetchone() is None


async def test_stranger_denied_on_search_and_markdown(db_conn, docs_app):
    # The two read routes besides GET /{id} must also enforce can_read: a
    # stranger gets the same structural 404, not a 403 or a leaked result.
    owner = await _member(db_conn, "owner-rd")
    stranger = await _member(db_conn, "stranger-rd")
    doc_id = "d" * 64
    await _insert_doc(db_conn, doc_id, owner.user_id)

    docs_app.dependency_overrides[deps.get_current_user] = lambda: stranger
    async with _client(docs_app) as client:
        # Valid body so the reader guard (not body validation) is what rejects.
        r = await client.post(f"/v1/docs/{doc_id}/search", json={"query": "x"})
        assert r.status_code == 404
        r = await client.get(f"/v1/docs/{doc_id}/markdown")
        assert r.status_code == 404


async def test_unauthenticated_is_401(db_conn, docs_app):
    # No get_current_user override -> real dependency -> 401 without credentials.
    async def _conn_override():
        yield db_conn

    docs_app.dependency_overrides[deps.get_conn] = _conn_override
    async with _client(docs_app) as client:
        assert (await client.get(f"/v1/docs/{HEX}")).status_code == 401
