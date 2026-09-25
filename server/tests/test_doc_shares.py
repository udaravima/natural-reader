"""PUT/DELETE /v1/docs/{id}/shares/{user_id} (A1 spec §5). Only an upload-entry
holder shares; a share creates the recipient's `shared` entry and never
downgrades an entry they already hold; revoke removes only a share the caller
created, then GCs."""
import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.auth.users import resolve_or_provision_user, set_status
from server.routers import docs as docs_router
from server.services import doc_content
from server.tests import seed

HEX = "a" * 64


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
    return deps.Principal(user_id=u["id"], email=u["email"], role="member", capabilities=frozenset({"reader"}))


async def _insert_doc(db_conn, doc_id, holder_id):
    await seed.seed_doc(db_conn, doc_id, holder_id, file_name="f", file_type="text")


async def _entry(db_conn, user_id, doc_id=HEX):
    cur = await db_conn.execute(
        "SELECT added_via, shared_by FROM library_entries WHERE user_id=%s AND doc_id=%s",
        (user_id, doc_id))
    row = await cur.fetchone()
    return (row[0], str(row[1]) if row[1] else None) if row else None


async def _as(docs_app, principal, method, url):
    docs_app.dependency_overrides[deps.get_current_user] = lambda: principal
    async with _client(docs_app) as client:
        return await client.request(method, url)


async def test_upload_holder_shares_and_recipient_can_read(db_conn, docs_app):
    sharer, recipient = await _member(db_conn, "sharer"), await _member(db_conn, "recipient")
    await _insert_doc(db_conn, HEX, sharer.user_id)
    assert (await _as(docs_app, recipient, "GET", f"/v1/docs/{HEX}")).status_code == 404
    r = await _as(docs_app, sharer, "PUT", f"/v1/docs/{HEX}/shares/{recipient.user_id}")
    assert r.status_code == 204
    assert await _entry(db_conn, recipient.user_id) == ("shared", sharer.user_id)
    r = await _as(docs_app, recipient, "GET", f"/v1/docs/{HEX}")
    assert r.status_code == 200
    assert r.json()["shared_by"]["id"] == sharer.user_id


async def test_recipient_cannot_reshare(db_conn, docs_app):
    sharer, recipient, third = [await _member(db_conn, s) for s in ("s", "r", "t")]
    await _insert_doc(db_conn, HEX, sharer.user_id)
    await seed.share_doc(db_conn, HEX, sharer.user_id, recipient.user_id)
    r = await _as(docs_app, recipient, "PUT", f"/v1/docs/{HEX}/shares/{third.user_id}")
    assert r.status_code == 404
    assert await _entry(db_conn, third.user_id) is None


async def test_non_holder_cannot_share(db_conn, docs_app):
    holder, other = await _member(db_conn, "holder"), await _member(db_conn, "other")
    await _insert_doc(db_conn, HEX, holder.user_id)
    r = await _as(docs_app, other, "PUT", f"/v1/docs/{HEX}/shares/{other.user_id}")
    assert r.status_code == 404
    assert r.json()["detail"] == {"error": "not_found", "message": "Document not found"}
    assert await _entry(db_conn, other.user_id) is None


async def test_share_with_unknown_user_404_and_conn_stays_usable(db_conn, docs_app):
    holder = await _member(db_conn, "holder")
    await _insert_doc(db_conn, HEX, holder.user_id)
    r = await _as(docs_app, holder, "PUT", f"/v1/docs/{HEX}/shares/{uuid.uuid4()}")
    assert r.status_code == 404
    # The FK-violation rollback (savepoint) didn't abort the outer transaction.
    assert (await _as(docs_app, holder, "GET", f"/v1/docs/{HEX}")).status_code == 200


async def test_share_with_malformed_user_404(db_conn, docs_app):
    holder = await _member(db_conn, "holder")
    await _insert_doc(db_conn, HEX, holder.user_id)
    r = await _as(docs_app, holder, "PUT", f"/v1/docs/{HEX}/shares/not-a-uuid")
    assert r.status_code == 404
    assert r.json()["detail"] == {"error": "not_found", "message": "User not found"}


async def test_share_never_downgrades_recipients_upload_entry(db_conn, docs_app):
    a, b = await _member(db_conn, "a"), await _member(db_conn, "b")
    await _insert_doc(db_conn, HEX, a.user_id)
    await doc_content.add_entry(db_conn, b.user_id, HEX, via="upload")  # b uploaded it too
    r = await _as(docs_app, a, "PUT", f"/v1/docs/{HEX}/shares/{b.user_id}")
    assert r.status_code == 204
    assert await _entry(db_conn, b.user_id) == ("upload", None)


async def test_sharer_revokes_and_recipient_loses_read(db_conn, docs_app):
    sharer, recipient = await _member(db_conn, "sharer"), await _member(db_conn, "recipient")
    await _insert_doc(db_conn, HEX, sharer.user_id)
    await seed.share_doc(db_conn, HEX, sharer.user_id, recipient.user_id)
    assert (await _as(docs_app, recipient, "GET", f"/v1/docs/{HEX}")).status_code == 200
    r = await _as(docs_app, sharer, "DELETE", f"/v1/docs/{HEX}/shares/{recipient.user_id}")
    assert r.status_code == 204
    assert (await _as(docs_app, recipient, "GET", f"/v1/docs/{HEX}")).status_code == 404
    # The sharer still holds it, so the content survives.
    assert (await _as(docs_app, sharer, "GET", f"/v1/docs/{HEX}")).status_code == 200


async def test_revoking_someone_elses_share_is_404(db_conn, docs_app):
    a, b, c = [await _member(db_conn, s) for s in ("a", "b", "c")]
    await _insert_doc(db_conn, HEX, a.user_id)
    await seed.share_doc(db_conn, HEX, a.user_id, b.user_id)
    await doc_content.add_entry(db_conn, c.user_id, HEX, via="upload")  # c uploaded it too
    r = await _as(docs_app, c, "DELETE", f"/v1/docs/{HEX}/shares/{b.user_id}")
    assert r.status_code == 404
    assert r.json()["detail"] == {"error": "not_found", "message": "Share not found"}
    assert await _entry(db_conn, b.user_id) == ("shared", a.user_id)


async def test_revoking_an_upload_entry_is_404(db_conn, docs_app):
    a, b = await _member(db_conn, "a"), await _member(db_conn, "b")
    await _insert_doc(db_conn, HEX, a.user_id)
    await doc_content.add_entry(db_conn, b.user_id, HEX, via="upload")  # b uploaded it too
    r = await _as(docs_app, a, "DELETE", f"/v1/docs/{HEX}/shares/{b.user_id}")
    assert r.status_code == 404
    assert await _entry(db_conn, b.user_id) == ("upload", None)


async def test_revoking_the_last_reference_gcs_the_content(db_conn, docs_app):
    # Removing my own upload entry does NOT revoke my shares (spec §5); revoking
    # the recipient's afterwards drops the last reference, so the content goes.
    sharer, recipient = await _member(db_conn, "sharer"), await _member(db_conn, "recipient")
    await _insert_doc(db_conn, HEX, sharer.user_id)
    await seed.share_doc(db_conn, HEX, sharer.user_id, recipient.user_id)
    assert (await _as(docs_app, sharer, "DELETE", f"/v1/docs/{HEX}")).status_code == 204
    assert (await _as(docs_app, recipient, "GET", f"/v1/docs/{HEX}")).status_code == 200
    r = await _as(docs_app, sharer, "DELETE", f"/v1/docs/{HEX}/shares/{recipient.user_id}")
    assert r.status_code == 204
    cur = await db_conn.execute("SELECT 1 FROM documents WHERE doc_id=%s", (HEX,))
    assert await cur.fetchone() is None
