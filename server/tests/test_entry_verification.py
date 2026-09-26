"""Verified holdings (A1 final review C1, migration 012).

Before A1 the browser sent only a hash, so migration 011 turned every pre-A1
*claim* into an upload entry (plus the claimant's shares and placements).
Those rows are unverified. They keep today's trust on legacy content
(extracted_by='client'); once verified bytes make the content server-derived,
they stop granting read, and they never let anyone share or file. Uploading
the bytes verifies the uploader's entry and the shares/placements they made.
"""
from __future__ import annotations

import hashlib

import pytest

from server.auth import authz, deps
from server.auth.users import resolve_or_provision_user, set_status
from server.routers import docs as docs_router
from server.routers import projects as projects_router
from server.tests import seed
from server.tests.docs_harness import build_docs_app, fake_embed

TEXT = b"The author wrote this sentence. Nobody else had these bytes before."
DOC = hashlib.sha256(TEXT).hexdigest()


@pytest.fixture
def as_user(db_conn, monkeypatch, tmp_path):
    app, as_user = build_docs_app(db_conn, monkeypatch, storage_dir=tmp_path)

    async def embed_one(text):
        return (await fake_embed([text]))[0]
    monkeypatch.setattr(docs_router, "embed_one", embed_one)

    async def _conn():
        yield db_conn
    app.dependency_overrides[deps.get_conn] = _conn
    app.include_router(projects_router.router)
    return as_user


async def _member(db_conn, sub):
    u = await resolve_or_provision_user(db_conn, iss="i", sub=sub, email=f"{sub}@x.io")
    await set_status(db_conn, u["id"], "active")
    return deps.Principal(user_id=u["id"], email=u["email"], role="member",
                          capabilities=frozenset({"reader"}))


async def _project(db_conn, owner, *members):
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P') RETURNING id", (owner,))
    pid = str((await cur.fetchone())[0])
    for m in members:
        await db_conn.execute(
            "INSERT INTO project_members (project_id, user_id) VALUES (%s,%s)", (pid, m))
    return pid


async def _can_read(db_conn, user_id, doc_id=DOC) -> bool:
    try:
        await authz.assert_can_read_doc(db_conn, doc_id, user_id)
    except Exception:
        return False
    return True


async def _reads_over_http(client, doc_id=DOC):
    """(GET, search, markdown, listed) — every route that reveals content."""
    get = (await client.get(f"/v1/docs/{doc_id}")).status_code
    search = (await client.post(f"/v1/docs/{doc_id}/search", json={"query": "x"})).status_code
    md = (await client.get(f"/v1/docs/{doc_id}/markdown")).status_code
    listed = doc_id in [r["doc_id"] for r in (await client.get("/v1/docs")).json()]
    return get, search, md, listed


async def _verified(db_conn, table, **where):
    cond = " AND ".join(f"{k} = %s" for k in where)
    cur = await db_conn.execute(f"SELECT verified FROM {table} WHERE {cond}", list(where.values()))
    return (await cur.fetchone())[0]


async def test_pre_a1_hash_squat_gives_no_access_once_the_author_uploads(db_conn, as_user):
    squatter = await _member(db_conn, "v-squatter")
    friend = await _member(db_conn, "v-friend")      # got a backfilled grant from the squatter
    member = await _member(db_conn, "v-member")      # member of the squatter's project
    author = await _member(db_conn, "v-author")
    pid = await _project(db_conn, squatter.user_id, member.user_id)
    # What 011 + 012 leave behind for a pre-A1 hash-only registration.
    await seed.seed_doc(db_conn, DOC, squatter.user_id, file_name="x.txt", file_type="text",
                        state="registered", extracted_by="client", verified=False,
                        project_ids=[pid])
    await seed.share_doc(db_conn, DOC, squatter.user_id, friend.user_id, verified=False)
    assert await _can_read(db_conn, squatter.user_id)  # legacy content: today's trust

    async with as_user(author) as c:
        r = await c.post("/v1/docs", files={"file": ("mine.txt", TEXT)})
    assert r.status_code == 202
    cur = await db_conn.execute(
        "SELECT state, extracted_by FROM documents WHERE doc_id = %s", (DOC,))
    assert await cur.fetchone() == ("indexed", "server")

    for who in (squatter, friend, member):
        assert not await _can_read(db_conn, who.user_id)
        async with as_user(who) as c:
            assert await _reads_over_http(c) == (404, 404, 404, False)
    async with as_user(squatter) as c:
        assert (await c.put(f"/v1/docs/{DOC}/shares/{friend.user_id}")).status_code == 404
        assert (await c.put(f"/v1/projects/{pid}/docs/{DOC}")).status_code == 404
    async with as_user(author) as c:
        assert (await c.get(f"/v1/docs/{DOC}")).status_code == 200


async def test_legacy_owner_reupload_restores_access_and_their_shares_and_placements(
        db_conn, as_user):
    owner = await _member(db_conn, "v-owner")
    recipient = await _member(db_conn, "v-recipient")
    member = await _member(db_conn, "v-pmember")
    other = await _member(db_conn, "v-other")
    pid = await _project(db_conn, owner.user_id, member.user_id)
    await seed.seed_doc(db_conn, DOC, owner.user_id, file_name="x.txt", file_type="text",
                        state="registered", extracted_by="client", verified=False,
                        project_ids=[pid])
    await seed.share_doc(db_conn, DOC, owner.user_id, recipient.user_id, verified=False)

    async with as_user(other) as c:  # someone else's verified upload comes first
        assert (await c.post("/v1/docs", files={"file": ("o.txt", TEXT)})).status_code == 202
    for who in (owner, recipient, member):
        assert not await _can_read(db_conn, who.user_id)

    async with as_user(owner) as c:  # the old owner re-uploads (the Index button does this)
        r = await c.post("/v1/docs", files={"file": ("x.txt", TEXT)})
    assert r.status_code == 200 and r.json()["dedup"] is True
    for who in (owner, recipient, member):
        assert await _can_read(db_conn, who.user_id)
        async with as_user(who) as c:
            assert await _reads_over_http(c) == (200, 200, 404, True)  # 404 md: never converted
    assert await _verified(db_conn, "library_entries", user_id=owner.user_id, doc_id=DOC)
    assert await _verified(db_conn, "library_entries", user_id=recipient.user_id, doc_id=DOC)
    assert await _verified(db_conn, "project_documents", project_id=pid, doc_id=DOC)


async def test_legacy_client_content_stays_readable_by_unverified_holders(db_conn):
    owner = await resolve_or_provision_user(db_conn, iss="i", sub="v-lo", email="lo@x.io")
    recipient = await resolve_or_provision_user(db_conn, iss="i", sub="v-lr", email="lr@x.io")
    member = await resolve_or_provision_user(db_conn, iss="i", sub="v-lm", email="lm@x.io")
    pid = await _project(db_conn, owner["id"], member["id"])
    await seed.seed_doc(db_conn, DOC, owner["id"], state="indexed", extracted_by="client",
                        verified=False, project_ids=[pid])
    await seed.share_doc(db_conn, DOC, owner["id"], recipient["id"], verified=False)
    for uid in (owner["id"], recipient["id"], member["id"]):
        assert await _can_read(db_conn, uid)
    await db_conn.execute("UPDATE documents SET extracted_by = 'server' WHERE doc_id = %s", (DOC,))
    for uid in (owner["id"], recipient["id"], member["id"]):
        assert not await _can_read(db_conn, uid)


async def test_unverified_holder_cannot_share_or_file_even_legacy_content(db_conn, as_user):
    owner = await _member(db_conn, "v-uo")
    friend = await _member(db_conn, "v-uf")
    pid = await _project(db_conn, owner.user_id)
    await seed.seed_doc(db_conn, DOC, owner.user_id, state="indexed", extracted_by="client",
                        verified=False)
    async with as_user(owner) as c:
        assert (await c.get(f"/v1/docs/{DOC}")).status_code == 200  # still reads legacy
        assert (await c.put(f"/v1/docs/{DOC}/shares/{friend.user_id}")).status_code == 404
        assert (await c.put(f"/v1/projects/{pid}/docs/{DOC}")).status_code == 404


async def test_verified_uploaders_new_shares_and_placements_are_verified(db_conn, as_user):
    author = await _member(db_conn, "v-va")
    friend = await _member(db_conn, "v-vf")
    member = await _member(db_conn, "v-vm")
    pid = await _project(db_conn, author.user_id, member.user_id)
    async with as_user(author) as c:
        assert (await c.post("/v1/docs", files={"file": ("a.txt", TEXT)})).status_code == 202
        assert (await c.put(f"/v1/docs/{DOC}/shares/{friend.user_id}")).status_code == 204
        assert (await c.put(f"/v1/projects/{pid}/docs/{DOC}")).status_code == 204
    assert await _verified(db_conn, "library_entries", user_id=author.user_id, doc_id=DOC)
    assert await _verified(db_conn, "library_entries", user_id=friend.user_id, doc_id=DOC)
    assert await _verified(db_conn, "project_documents", project_id=pid, doc_id=DOC)
    for who in (friend, member):
        async with as_user(who) as c:
            assert (await c.get(f"/v1/docs/{DOC}")).status_code == 200


async def test_verified_share_takes_over_an_unverified_legacy_share(db_conn, as_user):
    """A recipient still holding an unproven legacy share gets the verified
    sharer's share instead — otherwise the new share would silently do nothing."""
    legacy_owner = await _member(db_conn, "v-to")
    recipient = await _member(db_conn, "v-tr")
    author = await _member(db_conn, "v-ta")
    await seed.seed_doc(db_conn, DOC, legacy_owner.user_id, state="registered",
                        extracted_by="client", verified=False, file_type="text")
    await seed.share_doc(db_conn, DOC, legacy_owner.user_id, recipient.user_id, verified=False)
    async with as_user(author) as c:
        assert (await c.post("/v1/docs", files={"file": ("a.txt", TEXT)})).status_code == 202
        assert (await c.put(f"/v1/docs/{DOC}/shares/{recipient.user_id}")).status_code == 204
    cur = await db_conn.execute(
        "SELECT added_via, shared_by::text, verified FROM library_entries "
        "WHERE user_id = %s AND doc_id = %s", (recipient.user_id, DOC))
    assert await cur.fetchone() == ("shared", author.user_id, True)
    assert await _can_read(db_conn, recipient.user_id)


async def test_ineffective_placement_is_not_listed_nor_filterable(db_conn, as_user):
    """A placement that doesn't grant read doesn't show up as a project link or
    match the project filter either — the two views can't disagree."""
    squatter = await _member(db_conn, "v-ps")
    reader = await _member(db_conn, "v-pr")  # verified holder AND member of the project
    pid = await _project(db_conn, squatter.user_id, reader.user_id)
    await seed.seed_doc(db_conn, DOC, squatter.user_id, state="indexed", extracted_by="server",
                        verified=False, project_ids=[pid])
    await seed.share_doc(db_conn, DOC, squatter.user_id, reader.user_id)  # verified share
    async with as_user(reader) as c:
        assert (await c.get(f"/v1/docs/{DOC}")).json()["projects"] == []
        rows = (await c.get("/v1/docs")).json()
        assert [r["projects"] for r in rows if r["doc_id"] == DOC] == [[]]
        assert (await c.get("/v1/docs", params={"project_id": pid})).json() == []


async def test_unverified_holder_can_remove_but_not_see_their_entry(db_conn, as_user):
    squatter = await _member(db_conn, "v-rs")
    await seed.seed_doc(db_conn, DOC, squatter.user_id, state="indexed", extracted_by="server",
                        verified=False)
    async with as_user(squatter) as c:
        assert (await c.patch(f"/v1/docs/{DOC}", json={"file_name": "x"})).status_code == 404
        assert (await c.delete(f"/v1/docs/{DOC}")).status_code == 204
