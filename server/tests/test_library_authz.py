import pytest
from fastapi import HTTPException
from server.auth import authz
from server.tests import seed
pytestmark = pytest.mark.asyncio


async def _user(conn, sub, email):
    cur = await conn.execute(
        "INSERT INTO users (oidc_iss, oidc_sub, email, role, status) "
        "VALUES ('i',%s,%s,'member','active') RETURNING id", (sub, email))
    return str((await cur.fetchone())[0])


async def _doc(conn, doc_id, owner, project_ids=()):
    await seed.seed_doc(conn, doc_id, owner, file_name="f.pdf", project_ids=project_ids)


async def _project(conn, owner, name="P"):
    cur = await conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,%s) RETURNING id", (owner, name))
    return str((await cur.fetchone())[0])


async def test_owner_can_read(db_conn):
    o = await _user(db_conn, "o", "o@x.io")
    await _doc(db_conn, "d1", o)
    await authz.assert_can_read_doc(db_conn, "d1", o)  # no raise


async def test_stranger_cannot_read(db_conn):
    o = await _user(db_conn, "o", "o@x.io")
    s = await _user(db_conn, "s", "s@x.io")
    await _doc(db_conn, "d1", o)
    with pytest.raises(HTTPException) as e:
        await authz.assert_can_read_doc(db_conn, "d1", s)
    assert e.value.status_code == 404


async def test_project_member_can_read(db_conn):
    o = await _user(db_conn, "o", "o@x.io")
    m = await _user(db_conn, "m", "m@x.io")
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P') RETURNING id", (o,))
    pid = str((await cur.fetchone())[0])
    await db_conn.execute(
        "INSERT INTO project_members (project_id, user_id) VALUES (%s,%s)", (pid, m))
    await _doc(db_conn, "d1", o, [pid])
    await authz.assert_can_read_doc(db_conn, "d1", m)  # no raise


async def test_grantee_can_read(db_conn):
    o = await _user(db_conn, "o", "o@x.io")
    g = await _user(db_conn, "g", "g@x.io")
    await _doc(db_conn, "d1", o)
    await seed.share_doc(db_conn, "d1", o, g)
    await authz.assert_can_read_doc(db_conn, "d1", g)  # no raise


async def test_member_of_other_project_cannot_read(db_conn):
    # Membership is scoped to the doc's OWN project: being a member of project A
    # grants nothing over a doc filed under project B. Pins the project_documents
    # join to the doc's own links.
    o = await _user(db_conn, "o", "o@x.io")
    m = await _user(db_conn, "m", "m@x.io")
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'A') RETURNING id", (o,))
    proj_a = str((await cur.fetchone())[0])
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'B') RETURNING id", (o,))
    proj_b = str((await cur.fetchone())[0])
    await db_conn.execute(
        "INSERT INTO project_members (project_id, user_id) VALUES (%s,%s)", (proj_a, m))
    await _doc(db_conn, "d1", o, [proj_b])  # doc lives in B, m only belongs to A
    with pytest.raises(HTTPException) as e:
        await authz.assert_can_read_doc(db_conn, "d1", m)
    assert e.value.status_code == 404


async def test_readable_predicate_placeholders_match_params():
    # The predicate and its params are defined side by side; a caller that
    # hand-counts user ids is exactly how a uid gets bound to the wrong column.
    assert authz.readable_docs_where("d").count("%s") == len(authz.readable_docs_params("u"))


async def test_can_read_one_doc_query_placeholders_match_params():
    # The single-doc query has a LEADING doc_id before the user ids.
    assert authz.CAN_READ_ONE_DOC_SQL.count("%s") == 1 + len(authz.readable_docs_params("u"))


async def test_project_owner_reads_member_filed_doc(db_conn):
    # Regression: the owner of a project has no project_members row, and used
    # to be a stranger to docs other members filed into their own project.
    alice = await _user(db_conn, "alice", "alice@x.io")
    bob = await _user(db_conn, "bob", "bob@x.io")
    pid = await _project(db_conn, alice)
    await db_conn.execute(
        "INSERT INTO project_members (project_id, user_id) VALUES (%s,%s)", (pid, bob))
    await _doc(db_conn, "d1", bob, [pid])
    await authz.assert_can_read_doc(db_conn, "d1", alice)  # no raise


async def test_member_of_either_linked_project_can_read(db_conn):
    o = await _user(db_conn, "o", "o@x.io")
    m1 = await _user(db_conn, "m1", "m1@x.io")
    m2 = await _user(db_conn, "m2", "m2@x.io")
    p1 = await _project(db_conn, o, "A")
    p2 = await _project(db_conn, o, "B")
    await db_conn.execute(
        "INSERT INTO project_members (project_id, user_id) VALUES (%s,%s),(%s,%s)",
        (p1, m1, p2, m2))
    await _doc(db_conn, "d1", o, [p1, p2])
    await authz.assert_can_read_doc(db_conn, "d1", m1)
    await authz.assert_can_read_doc(db_conn, "d1", m2)


async def test_visible_projects_placeholders_match_params():
    assert authz.visible_projects_where("p").count("%s") == len(authz.visible_projects_params("u"))
