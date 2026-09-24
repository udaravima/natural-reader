import uuid

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from server.auth import deps
from server.auth.users import resolve_or_provision_user
from server.routers import projects as projects_router


def _app(db_conn, principal):
    app = FastAPI()
    async def _conn(): yield db_conn
    app.dependency_overrides[deps.get_conn] = _conn
    app.dependency_overrides[deps.get_current_user] = lambda: principal
    app.include_router(projects_router.router)
    return app


async def test_create_and_list_project(db_conn):
    u = await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")
    p = deps.Principal(user_id=u["id"], email=u["email"], role="member")
    async with httpx.AsyncClient(transport=ASGITransport(app=_app(db_conn, p)),
                                 base_url="http://t") as c:
        r = await c.post("/v1/projects", json={"name": "Alpha"})
        assert r.status_code == 201 and r.json()["name"] == "Alpha"
        rows = (await c.get("/v1/projects")).json()
        assert len(rows) == 1 and rows[0]["is_owner"] is True


async def test_non_owner_cannot_patch(db_conn):
    owner = await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="o@x.io")
    other = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="x@x.io")
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P') RETURNING id",
        (owner["id"],))
    pid = str((await cur.fetchone())[0])
    p = deps.Principal(user_id=other["id"], email=other["email"], role="member")
    async with httpx.AsyncClient(transport=ASGITransport(app=_app(db_conn, p)),
                                 base_url="http://t") as c:
        r = await c.patch(f"/v1/projects/{pid}", json={"name": "Hijack"})
        assert r.status_code == 404


async def test_owner_adds_and_removes_member(db_conn):
    owner = await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="o@x.io")
    member = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="m@x.io")
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P') RETURNING id",
        (owner["id"],))
    pid = str((await cur.fetchone())[0])
    p = deps.Principal(user_id=owner["id"], email=owner["email"], role="member")
    async with httpx.AsyncClient(transport=ASGITransport(app=_app(db_conn, p)),
                                 base_url="http://t") as c:
        assert (await c.put(f"/v1/projects/{pid}/members/{member['id']}", json={})).status_code == 204
        assert (await c.delete(f"/v1/projects/{pid}/members/{member['id']}", )).status_code == 204


async def test_non_owner_cannot_delete_project(db_conn):
    owner = await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="o@x.io")
    other = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="x@x.io")
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P') RETURNING id",
        (owner["id"],))
    pid = str((await cur.fetchone())[0])
    p = deps.Principal(user_id=other["id"], email=other["email"], role="member")
    async with httpx.AsyncClient(transport=ASGITransport(app=_app(db_conn, p)),
                                 base_url="http://t") as c:
        r = await c.delete(f"/v1/projects/{pid}")
        assert r.status_code == 404


async def test_non_owner_cannot_add_member(db_conn):
    owner = await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="o@x.io")
    other = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="x@x.io")
    target = await resolve_or_provision_user(db_conn, iss="i", sub="s3", email="t@x.io")
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P') RETURNING id",
        (owner["id"],))
    pid = str((await cur.fetchone())[0])
    p = deps.Principal(user_id=other["id"], email=other["email"], role="member")
    async with httpx.AsyncClient(transport=ASGITransport(app=_app(db_conn, p)),
                                 base_url="http://t") as c:
        r = await c.put(f"/v1/projects/{pid}/members/{target['id']}", json={})
        assert r.status_code == 404


async def test_non_owner_cannot_remove_member(db_conn):
    owner = await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="o@x.io")
    other = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="x@x.io")
    target = await resolve_or_provision_user(db_conn, iss="i", sub="s3", email="t@x.io")
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P') RETURNING id",
        (owner["id"],))
    pid = str((await cur.fetchone())[0])
    p = deps.Principal(user_id=other["id"], email=other["email"], role="member")
    async with httpx.AsyncClient(transport=ASGITransport(app=_app(db_conn, p)),
                                 base_url="http://t") as c:
        r = await c.delete(f"/v1/projects/{pid}/members/{target['id']}")
        assert r.status_code == 404


async def test_list_excludes_other_users_projects(db_conn):
    """The owner-or-member listing predicate in list_projects excludes
    projects the caller has no relationship to — the security property that
    was previously only verified by reading the SQL."""
    a = await resolve_or_provision_user(db_conn, iss="i", sub="a1", email="a@x.io")
    b = await resolve_or_provision_user(db_conn, iss="i", sub="b1", email="b@x.io")
    pa = deps.Principal(user_id=a["id"], email=a["email"], role="member")
    pb = deps.Principal(user_id=b["id"], email=b["email"], role="member")
    async with httpx.AsyncClient(transport=ASGITransport(app=_app(db_conn, pa)),
                                 base_url="http://t") as c:
        r = await c.post("/v1/projects", json={"name": "A's project"})
        assert r.status_code == 201
    async with httpx.AsyncClient(transport=ASGITransport(app=_app(db_conn, pb)),
                                 base_url="http://t") as c:
        rows = (await c.get("/v1/projects")).json()
        assert all(row["name"] != "A's project" for row in rows)


async def test_add_member_nonexistent_user_404(db_conn):
    owner = await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="o@x.io")
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P') RETURNING id",
        (owner["id"],))
    pid = str((await cur.fetchone())[0])
    p = deps.Principal(user_id=owner["id"], email=owner["email"], role="member")
    fake_uid = str(uuid.uuid4())
    async with httpx.AsyncClient(transport=ASGITransport(app=_app(db_conn, p)),
                                 base_url="http://t") as c:
        r = await c.put(f"/v1/projects/{pid}/members/{fake_uid}", json={})
        assert r.status_code == 404
        # Prove the FK-violation rollback (savepoint) didn't abort the
        # outer transaction — a follow-up query on the same connection
        # should still succeed.
        rows = (await c.get("/v1/projects")).json()
        assert len(rows) == 1


async def test_add_member_malformed_user_404(db_conn):
    owner = await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="o@x.io")
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P') RETURNING id",
        (owner["id"],))
    pid = str((await cur.fetchone())[0])
    p = deps.Principal(user_id=owner["id"], email=owner["email"], role="member")
    async with httpx.AsyncClient(transport=ASGITransport(app=_app(db_conn, p)),
                                 base_url="http://t") as c:
        r = await c.put(f"/v1/projects/{pid}/members/not-a-uuid", json={})
        assert r.status_code == 404


DOC_A = "a" * 64


def _reader(u, role="member"):
    return deps.Principal(user_id=u["id"], email=u["email"], role=role,
                          capabilities=frozenset({"reader"}))


async def _mk_project(db_conn, owner_id, name="P"):
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,%s) RETURNING id",
        (owner_id, name))
    return str((await cur.fetchone())[0])


async def _mk_doc(db_conn, doc_id, owner_id):
    await db_conn.execute(
        "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, user_id) "
        "VALUES (%s,'f.pdf','pdf',1,%s)", (doc_id, owner_id))


async def _linked(db_conn, pid, doc_id):
    cur = await db_conn.execute(
        "SELECT 1 FROM project_documents WHERE project_id=%s AND doc_id=%s", (pid, doc_id))
    return await cur.fetchone() is not None


def _client_for(db_conn, principal):
    return httpx.AsyncClient(transport=ASGITransport(app=_app(db_conn, principal)),
                             base_url="http://t")


async def test_link_own_doc_into_own_project(db_conn):
    u = await resolve_or_provision_user(db_conn, iss="i", sub="l1", email="l1@x.io")
    pid = await _mk_project(db_conn, u["id"])
    await _mk_doc(db_conn, DOC_A, u["id"])
    async with _client_for(db_conn, _reader(u)) as c:
        assert (await c.put(f"/v1/projects/{pid}/docs/{DOC_A}")).status_code == 204
        assert (await c.put(f"/v1/projects/{pid}/docs/{DOC_A}")).status_code == 204  # idempotent
    assert await _linked(db_conn, pid, DOC_A)


async def test_link_own_doc_into_project_where_member(db_conn):
    owner = await resolve_or_provision_user(db_conn, iss="i", sub="l2o", email="l2o@x.io")
    u = await resolve_or_provision_user(db_conn, iss="i", sub="l2", email="l2@x.io")
    pid = await _mk_project(db_conn, owner["id"])
    await db_conn.execute(
        "INSERT INTO project_members (project_id, user_id) VALUES (%s,%s)", (pid, u["id"]))
    await _mk_doc(db_conn, DOC_A, u["id"])
    async with _client_for(db_conn, _reader(u)) as c:
        assert (await c.put(f"/v1/projects/{pid}/docs/{DOC_A}")).status_code == 204


async def test_link_into_invisible_project_is_404(db_conn):
    owner = await resolve_or_provision_user(db_conn, iss="i", sub="l3o", email="l3o@x.io")
    u = await resolve_or_provision_user(db_conn, iss="i", sub="l3", email="l3@x.io")
    pid = await _mk_project(db_conn, owner["id"])
    await _mk_doc(db_conn, DOC_A, u["id"])
    async with _client_for(db_conn, _reader(u)) as c:
        r = await c.put(f"/v1/projects/{pid}/docs/{DOC_A}")
        assert r.status_code == 404 and r.json()["detail"] == "Project not found"
    assert not await _linked(db_conn, pid, DOC_A)


async def test_link_foreign_doc_is_404(db_conn):
    u = await resolve_or_provision_user(db_conn, iss="i", sub="l4", email="l4@x.io")
    other = await resolve_or_provision_user(db_conn, iss="i", sub="l4x", email="l4x@x.io")
    pid = await _mk_project(db_conn, u["id"])
    await _mk_doc(db_conn, DOC_A, other["id"])
    async with _client_for(db_conn, _reader(u)) as c:
        r = await c.put(f"/v1/projects/{pid}/docs/{DOC_A}")
        assert r.status_code == 404 and r.json()["detail"] == "Document not found"


async def test_project_owner_cannot_link_a_granted_doc(db_conn):
    # Deliberate asymmetry (spec §6): project owners remove, but only doc owners add.
    u = await resolve_or_provision_user(db_conn, iss="i", sub="l5", email="l5@x.io")
    other = await resolve_or_provision_user(db_conn, iss="i", sub="l5x", email="l5x@x.io")
    pid = await _mk_project(db_conn, u["id"])
    await _mk_doc(db_conn, DOC_A, other["id"])
    await db_conn.execute(
        "INSERT INTO doc_grants (doc_id, grantee_user_id) VALUES (%s,%s)", (DOC_A, u["id"]))
    async with _client_for(db_conn, _reader(u)) as c:
        assert (await c.put(f"/v1/projects/{pid}/docs/{DOC_A}")).status_code == 404
    assert not await _linked(db_conn, pid, DOC_A)


async def test_admin_links_own_doc_into_any_project(db_conn):
    owner = await resolve_or_provision_user(db_conn, iss="i", sub="l6o", email="l6o@x.io")
    admin = await resolve_or_provision_user(db_conn, iss="i", sub="l6", email="l6@x.io")
    pid = await _mk_project(db_conn, owner["id"])
    await _mk_doc(db_conn, DOC_A, admin["id"])
    async with _client_for(db_conn, _reader(admin, role="admin")) as c:
        assert (await c.put(f"/v1/projects/{pid}/docs/{DOC_A}")).status_code == 204


@pytest.mark.parametrize("path", [
    f"/v1/projects/not-a-uuid/docs/{DOC_A}",
    "/v1/projects/00000000-0000-0000-0000-000000000001/docs/NOT-HEX",
])
async def test_link_malformed_ids_are_422(db_conn, path):
    u = await resolve_or_provision_user(db_conn, iss="i", sub="l7", email="l7@x.io")
    async with _client_for(db_conn, _reader(u)) as c:
        assert (await c.put(path)).status_code == 422
        assert (await c.delete(path)).status_code == 422


# Unlink resolution table (spec §6):
#   link exists  -> doc owner 204, project owner 204, other 404
#   link missing -> doc owner 204, project owner 404, other 404
@pytest.mark.parametrize("linked,caller,expected", [
    (True, "doc_owner", 204), (True, "project_owner", 204), (True, "other", 404),
    (False, "doc_owner", 204), (False, "project_owner", 404), (False, "other", 404),
])
async def test_unlink_resolution_table(db_conn, linked, caller, expected):
    doc_owner = await resolve_or_provision_user(db_conn, iss="i", sub="u1", email="u1@x.io")
    proj_owner = await resolve_or_provision_user(db_conn, iss="i", sub="u2", email="u2@x.io")
    other = await resolve_or_provision_user(db_conn, iss="i", sub="u3", email="u3@x.io")
    pid = await _mk_project(db_conn, proj_owner["id"])
    # `other` is a plain member: members must not unlink someone else's doc.
    await db_conn.execute(
        "INSERT INTO project_members (project_id, user_id) VALUES (%s,%s),(%s,%s)",
        (pid, doc_owner["id"], pid, other["id"]))
    await _mk_doc(db_conn, DOC_A, doc_owner["id"])
    if linked:
        await db_conn.execute(
            "INSERT INTO project_documents (project_id, doc_id) VALUES (%s,%s)", (pid, DOC_A))
    who = {"doc_owner": doc_owner, "project_owner": proj_owner, "other": other}[caller]
    async with _client_for(db_conn, _reader(who)) as c:
        assert (await c.delete(f"/v1/projects/{pid}/docs/{DOC_A}")).status_code == expected
    assert await _linked(db_conn, pid, DOC_A) is (linked and expected == 404)


async def test_doc_owner_who_left_the_project_can_still_unlink(db_conn):
    # Review Focus 2: projects[] hides this project from them now, but the
    # API must still let the doc's owner withdraw it.
    doc_owner = await resolve_or_provision_user(db_conn, iss="i", sub="rf2", email="rf2@x.io")
    proj_owner = await resolve_or_provision_user(db_conn, iss="i", sub="rf2p", email="rf2p@x.io")
    pid = await _mk_project(db_conn, proj_owner["id"])
    await _mk_doc(db_conn, DOC_A, doc_owner["id"])
    await db_conn.execute(
        "INSERT INTO project_documents (project_id, doc_id) VALUES (%s,%s)", (pid, DOC_A))
    async with _client_for(db_conn, _reader(doc_owner)) as c:
        assert (await c.delete(f"/v1/projects/{pid}/docs/{DOC_A}")).status_code == 204
    assert not await _linked(db_conn, pid, DOC_A)


async def test_deleting_project_keeps_doc_and_drops_link(db_conn):
    # Review Focus 3.
    u = await resolve_or_provision_user(db_conn, iss="i", sub="rf3", email="rf3@x.io")
    pid = await _mk_project(db_conn, u["id"])
    await _mk_doc(db_conn, DOC_A, u["id"])
    async with _client_for(db_conn, _reader(u)) as c:
        assert (await c.put(f"/v1/projects/{pid}/docs/{DOC_A}")).status_code == 204
        assert (await c.delete(f"/v1/projects/{pid}")).status_code == 204
    assert not await _linked(db_conn, pid, DOC_A)
    cur = await db_conn.execute("SELECT 1 FROM documents WHERE doc_id=%s", (DOC_A,))
    assert await cur.fetchone() is not None


async def test_new_owner_after_reassignment_can_unlink(db_conn):
    # Review Focus 4: links survive an admin ownership reassignment.
    old = await resolve_or_provision_user(db_conn, iss="i", sub="rf4a", email="rf4a@x.io")
    new = await resolve_or_provision_user(db_conn, iss="i", sub="rf4b", email="rf4b@x.io")
    pid = await _mk_project(db_conn, old["id"])
    await _mk_doc(db_conn, DOC_A, old["id"])
    await db_conn.execute(
        "INSERT INTO project_documents (project_id, doc_id) VALUES (%s,%s)", (pid, DOC_A))
    await db_conn.execute("UPDATE documents SET user_id=%s WHERE doc_id=%s", (new["id"], DOC_A))
    async with _client_for(db_conn, _reader(new)) as c:
        assert (await c.delete(f"/v1/projects/{pid}/docs/{DOC_A}")).status_code == 204
    assert not await _linked(db_conn, pid, DOC_A)
