"""GET /v1/docs — the library list endpoint.

Harness: this router does all DB work via `get_pool()` (see test_docs_authz.py),
not `Depends(get_conn)`, so we follow the same convention here — monkeypatch
`docs_router.get_pool` to a shim that hands back the test's transactional
`db_conn`. Keeps this test consistent with its neighbor rather than introducing
a second DB-access pattern for one route.
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


async def _member(db_conn, sub):
    u = await resolve_or_provision_user(db_conn, iss="i", sub=sub, email=f"{sub}@x.io")
    await set_status(db_conn, u["id"], "active")
    return deps.Principal(user_id=u["id"], email=u["email"], role="member", capabilities=frozenset({"reader"}))


async def _doc(db_conn, doc_id, owner_id, *, tags=None, project_id=None, file_name="f.pdf"):
    await db_conn.execute(
        "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, user_id, "
        "project_id, tags) VALUES (%s,%s,'pdf',1,%s,%s,%s)",
        (doc_id, file_name, owner_id, project_id, tags or []),
    )


async def _grant(db_conn, doc_id, grantee_id):
    await db_conn.execute(
        "INSERT INTO doc_grants (doc_id, grantee_user_id) VALUES (%s,%s)",
        (doc_id, grantee_id),
    )


DOC_C = "c" * 64  # caller's own doc
DOC_S = "s" * 64  # stranger's doc — must NEVER appear for the caller
DOC_G = "9" * 64  # owner's doc, granted to the caller
DOC_P = "1" * 64  # owner's doc, shared via project membership


async def test_list_returns_own_and_granted_not_stranger(db_conn, docs_app):
    caller = await _member(db_conn, "caller")
    owner = await _member(db_conn, "owner")
    stranger = await _member(db_conn, "stranger")

    await _doc(db_conn, DOC_C, caller.user_id, tags=["mine"])
    await _doc(db_conn, DOC_S, stranger.user_id, tags=["secret"])
    await _doc(db_conn, DOC_G, owner.user_id, tags=["shared"])
    await _grant(db_conn, DOC_G, caller.user_id)

    docs_app.dependency_overrides[deps.get_current_user] = lambda: caller
    async with _client(docs_app) as client:
        r = await client.get("/v1/docs")
        assert r.status_code == 200
        body = r.json()
        ids = {row["doc_id"] for row in body}
        assert ids == {DOC_C, DOC_G}
        assert DOC_S not in ids

        by_id = {row["doc_id"]: row for row in body}
        assert by_id[DOC_C]["is_owner"] is True
        assert by_id[DOC_C]["owner_user_id"] == caller.user_id
        assert by_id[DOC_G]["is_owner"] is False
        assert by_id[DOC_G]["owner_user_id"] == owner.user_id

        # The stranger's doc must stay invisible even when a q/tag filter
        # would otherwise match it — access is resolved in SQL, not by
        # filtering a Python list after the fact.
        r = await client.get("/v1/docs", params={"q": "secret"})
        assert r.status_code == 200
        assert DOC_S not in {row["doc_id"] for row in r.json()}

        r = await client.get("/v1/docs", params={"tag": "secret"})
        assert r.status_code == 200
        assert DOC_S not in {row["doc_id"] for row in r.json()}


async def test_list_project_member_sees_project_doc_stranger_still_excluded(db_conn, docs_app):
    owner = await _member(db_conn, "owner2")
    member = await _member(db_conn, "member2")
    stranger = await _member(db_conn, "stranger2")

    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P') RETURNING id",
        (owner.user_id,),
    )
    project_id = str((await cur.fetchone())[0])
    await db_conn.execute(
        "INSERT INTO project_members (project_id, user_id) VALUES (%s,%s)",
        (project_id, member.user_id),
    )
    await _doc(db_conn, DOC_P, owner.user_id, project_id=project_id)
    await _doc(db_conn, DOC_S, stranger.user_id)

    docs_app.dependency_overrides[deps.get_current_user] = lambda: member
    async with _client(docs_app) as client:
        r = await client.get("/v1/docs")
        assert r.status_code == 200
        body = r.json()
        ids = {row["doc_id"] for row in body}
        assert DOC_P in ids
        assert DOC_S not in ids

        row = next(row for row in body if row["doc_id"] == DOC_P)
        assert row["project_id"] == project_id
        assert row["project_name"] == "P"
        assert row["is_owner"] is False

        # project_id filter narrows within the readable set.
        r = await client.get("/v1/docs", params={"project_id": project_id})
        assert {row["doc_id"] for row in r.json()} == {DOC_P}


async def test_list_q_filters_by_filename_or_tag_within_readable_set(db_conn, docs_app):
    caller = await _member(db_conn, "caller3")
    await _doc(db_conn, DOC_C, caller.user_id, tags=["alpha"], file_name="report.pdf")
    other_own_doc = "2" * 64
    await _doc(db_conn, other_own_doc, caller.user_id, tags=["beta"], file_name="notes.pdf")

    docs_app.dependency_overrides[deps.get_current_user] = lambda: caller
    async with _client(docs_app) as client:
        r = await client.get("/v1/docs", params={"q": "report"})
        assert {row["doc_id"] for row in r.json()} == {DOC_C}

        r = await client.get("/v1/docs", params={"q": "alpha"})
        assert {row["doc_id"] for row in r.json()} == {DOC_C}

        r = await client.get("/v1/docs", params={"tag": "beta"})
        assert {row["doc_id"] for row in r.json()} == {other_own_doc}


async def test_list_unauthenticated_is_401(db_conn, docs_app):
    async def _conn_override():
        yield db_conn

    docs_app.dependency_overrides[deps.get_conn] = _conn_override
    async with _client(docs_app) as client:
        assert (await client.get("/v1/docs")).status_code == 401
