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
from server.tests import seed

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


async def _doc(db_conn, doc_id, owner_id, *, tags=None, project_ids=(), file_name="f.pdf"):
    await seed.seed_doc(db_conn, doc_id, owner_id, file_name=file_name, tags=tags or (),
                        project_ids=project_ids)


async def _project(db_conn, owner_id, name):
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,%s) RETURNING id",
        (owner_id, name))
    return str((await cur.fetchone())[0])


DOC_C = "c" * 64  # caller's own upload
DOC_S = "s" * 64  # stranger's doc — must NEVER appear for the caller
DOC_G = "9" * 64  # owner's upload, shared with the caller
DOC_P = "1" * 64  # owner's upload, reachable via project membership


async def test_list_returns_own_and_shared_not_stranger(db_conn, docs_app):
    caller = await _member(db_conn, "caller")
    owner = await _member(db_conn, "owner")
    stranger = await _member(db_conn, "stranger")

    await _doc(db_conn, DOC_C, caller.user_id, tags=["mine"])
    await _doc(db_conn, DOC_S, stranger.user_id, tags=["secret"])
    await _doc(db_conn, DOC_G, owner.user_id, tags=["shared"])
    await seed.share_doc(db_conn, DOC_G, owner.user_id, caller.user_id)

    docs_app.dependency_overrides[deps.get_current_user] = lambda: caller
    async with _client(docs_app) as client:
        r = await client.get("/v1/docs")
        assert r.status_code == 200
        body = r.json()
        ids = {row["doc_id"] for row in body}
        assert ids == {DOC_C, DOC_G}
        assert DOC_S not in ids

        by_id = {row["doc_id"]: row for row in body}
        own, shared = by_id[DOC_C], by_id[DOC_G]
        assert set(own) == {"doc_id", "state", "file_name", "tags", "added_via",
                            "shared_by", "in_library", "projects"}  # no owner fields
        assert own["in_library"] is True
        assert own["added_via"] == "upload"
        assert own["shared_by"] is None
        assert own["tags"] == ["mine"]
        assert shared["in_library"] is True
        assert shared["added_via"] == "shared"
        assert shared["shared_by"] == {"id": owner.user_id, "name": "owner@x.io"}
        assert shared["tags"] == []  # the sharer's tags are theirs, not mine

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
    await _doc(db_conn, DOC_P, owner.user_id, project_ids=[project_id])
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
        assert row["projects"] == [{"id": project_id, "name": "P"}]
        # Project-only: no entry of mine, so canonical name and no tags.
        assert row["in_library"] is False
        assert row["added_via"] is None
        assert row["shared_by"] is None
        assert row["file_name"] == "f.pdf" and row["tags"] == []

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


async def test_list_projects_hide_names_the_caller_cannot_see(db_conn, docs_app):
    owner = await _member(db_conn, "owner4")
    caller = await _member(db_conn, "caller4")
    visible = await _project(db_conn, owner.user_id, "Visible")
    hidden = await _project(db_conn, owner.user_id, "Hidden")
    await db_conn.execute(
        "INSERT INTO project_members (project_id, user_id) VALUES (%s,%s)",
        (visible, caller.user_id))
    await _doc(db_conn, DOC_P, owner.user_id, project_ids=[visible, hidden])

    docs_app.dependency_overrides[deps.get_current_user] = lambda: caller
    async with _client(docs_app) as client:
        row = next(r for r in (await client.get("/v1/docs")).json() if r["doc_id"] == DOC_P)
        assert row["projects"] == [{"id": visible, "name": "Visible"}]


async def test_uploader_does_not_see_links_to_projects_they_cannot_see(db_conn, docs_app):
    # A1 §3 (inverts C0's owner exception): the uploader, removed from the
    # project, no longer sees the link — they can't unlink it anyway, and
    # listing it would leak the project's name.
    owner = await _member(db_conn, "owner8")
    proj_owner = await _member(db_conn, "projowner8")
    pid = await _project(db_conn, proj_owner.user_id, "TheirProject")
    await _doc(db_conn, DOC_C, owner.user_id, project_ids=[pid])  # owner is NOT a member

    docs_app.dependency_overrides[deps.get_current_user] = lambda: owner
    async with _client(docs_app) as client:
        row = (await client.get("/v1/docs")).json()[0]
        assert row["projects"] == []
        status = (await client.get(f"/v1/docs/{DOC_C}")).json()
        assert status["projects"] == []


async def test_list_tags_and_q_use_my_entry(db_conn, docs_app):
    # Each holder's name and tags are personal: filters match MY entry only.
    owner = await _member(db_conn, "owner9")
    caller = await _member(db_conn, "caller9")
    await _doc(db_conn, DOC_G, owner.user_id, tags=["theirs"], file_name="canon.pdf")
    await seed.share_doc(db_conn, DOC_G, owner.user_id, caller.user_id)

    docs_app.dependency_overrides[deps.get_current_user] = lambda: caller
    async with _client(docs_app) as client:
        r = await client.patch(f"/v1/docs/{DOC_G}",
                               json={"file_name": "my-name.pdf", "tags": ["mine"]})
        assert r.status_code == 200
        row = (await client.get("/v1/docs")).json()[0]
        assert row["file_name"] == "my-name.pdf" and row["tags"] == ["mine"]
        for params, hit in (({"tag": "mine"}, True), ({"tag": "theirs"}, False),
                            ({"q": "my-name"}, True), ({"q": "mine"}, True),
                            ({"q": "theirs"}, False), ({"q": "canon"}, False)):
            ids = {r["doc_id"] for r in (await client.get("/v1/docs", params=params)).json()}
            assert (DOC_G in ids) is hit, params


async def test_doc_projects_placeholders_match_params():
    assert docs_router._doc_projects_sql("d").count("%s") == len(
        docs_router._doc_projects_params("u"))


async def test_list_filter_by_invisible_project_is_empty(db_conn, docs_app):
    owner = await _member(db_conn, "owner5")
    caller = await _member(db_conn, "caller5")
    hidden = await _project(db_conn, owner.user_id, "Hidden")
    await _doc(db_conn, DOC_G, owner.user_id, project_ids=[hidden])
    await seed.share_doc(db_conn, DOC_G, owner.user_id, caller.user_id)  # readable, but the project is not

    docs_app.dependency_overrides[deps.get_current_user] = lambda: caller
    async with _client(docs_app) as client:
        r = await client.get("/v1/docs", params={"project_id": hidden})
        assert r.status_code == 200 and r.json() == []


async def test_list_all_three_filters_bind_in_order(db_conn, docs_app):
    # Review Focus 1: the projects sub-select's params come BEFORE the WHERE
    # params. All three filters together exercise every placeholder.
    caller = await _member(db_conn, "caller6")
    pid = await _project(db_conn, caller.user_id, "Mine")
    await _doc(db_conn, DOC_C, caller.user_id, tags=["alpha"], file_name="report.pdf",
               project_ids=[pid])
    await _doc(db_conn, "2" * 64, caller.user_id, tags=["alpha"], file_name="report2.pdf")

    docs_app.dependency_overrides[deps.get_current_user] = lambda: caller
    async with _client(docs_app) as client:
        r = await client.get("/v1/docs",
                             params={"q": "report", "project_id": pid, "tag": "alpha"})
        assert r.status_code == 200
        assert [row["doc_id"] for row in r.json()] == [DOC_C]


async def test_list_same_named_projects_are_both_listed_in_stable_order(db_conn, docs_app):
    # Review Focus 5: own "Infra" + member of someone else's "Infra".
    caller = await _member(db_conn, "caller7")
    other = await _member(db_conn, "other7")
    mine = await _project(db_conn, caller.user_id, "Infra")
    theirs = await _project(db_conn, other.user_id, "Infra")
    await db_conn.execute(
        "INSERT INTO project_members (project_id, user_id) VALUES (%s,%s)",
        (theirs, caller.user_id))
    await _doc(db_conn, DOC_C, caller.user_id, project_ids=[mine, theirs])

    docs_app.dependency_overrides[deps.get_current_user] = lambda: caller
    async with _client(docs_app) as client:
        row = (await client.get("/v1/docs")).json()[0]
        assert row["projects"] == [{"id": i, "name": "Infra"} for i in sorted([mine, theirs])]


async def test_project_only_row_shows_the_filers_name_not_the_first_uploaders(db_conn, docs_app):
    """The canonical name is whoever uploaded the bytes first, anywhere — a
    stranger to the project. A project-only row shows the name the user who
    filed it gave it; search matches that name, never the hidden one."""
    first = await _member(db_conn, "i6-first")
    filer = await _member(db_conn, "i6-filer")
    member = await _member(db_conn, "i6-member")
    pid = await _project(db_conn, filer.user_id, "P")
    await db_conn.execute(
        "INSERT INTO project_members (project_id, user_id) VALUES (%s,%s)", (pid, member.user_id))
    await _doc(db_conn, DOC_P, first.user_id, file_name="first-uploader-secret.pdf")
    await seed.share_doc(db_conn, DOC_P, first.user_id, filer.user_id)
    await db_conn.execute(
        "UPDATE library_entries SET added_via = 'upload', shared_by = NULL, "
        "file_name = 'team-notes.pdf' WHERE user_id = %s AND doc_id = %s",
        (filer.user_id, DOC_P))  # the filer uploaded the same bytes under their own name
    await seed.place_doc(db_conn, pid, DOC_P, filer.user_id)

    docs_app.dependency_overrides[deps.get_current_user] = lambda: member
    async with _client(docs_app) as client:
        row = next(r for r in (await client.get("/v1/docs")).json() if r["doc_id"] == DOC_P)
        assert row["file_name"] == "team-notes.pdf" and row["in_library"] is False
        assert (await client.get(f"/v1/docs/{DOC_P}")).json()["file_name"] == "team-notes.pdf"
        assert (await client.get("/v1/docs", params={"q": "secret"})).json() == []
        hits = (await client.get("/v1/docs", params={"q": "team"})).json()
        assert [r["doc_id"] for r in hits] == [DOC_P]


async def test_display_name_placeholders_match_params():
    assert docs_router._DISPLAY_NAME.count("%s") == len(docs_router._display_name_params("u"))
