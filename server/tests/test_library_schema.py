import pytest

from server.tests import seed

pytestmark = pytest.mark.asyncio


async def test_project_and_membership_roundtrip(db_conn):
    cur = await db_conn.execute(
        "INSERT INTO users (oidc_iss, oidc_sub, email, role, status) "
        "VALUES ('i','o1','o@x.io','member','active') RETURNING id")
    owner = str((await cur.fetchone())[0])
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'Proj') RETURNING id",
        (owner,))
    pid = str((await cur.fetchone())[0])
    await db_conn.execute(
        "INSERT INTO project_members (project_id, user_id) VALUES (%s,%s)", (pid, owner))
    cur = await db_conn.execute(
        "SELECT count(*) FROM project_members WHERE project_id=%s", (pid,))
    assert (await cur.fetchone())[0] == 1


async def test_documents_have_tags_and_no_project_column(db_conn):
    cur = await db_conn.execute(
        "INSERT INTO users (oidc_iss, oidc_sub, email, role, status) "
        "VALUES ('i','o2','o2@x.io','member','active') RETURNING id")
    owner = str((await cur.fetchone())[0])
    await seed.seed_doc(db_conn, "d1", owner, file_name="f.pdf", tags=("alpha", "beta"))
    cur = await db_conn.execute("SELECT tags FROM documents WHERE doc_id='d1'")
    assert set((await cur.fetchone())[0]) == {"alpha", "beta"}
    cur = await db_conn.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name='documents' AND column_name='project_id'")
    assert await cur.fetchone() is None


async def test_project_documents_links_and_cascades(db_conn):
    cur = await db_conn.execute(
        "INSERT INTO users (oidc_iss, oidc_sub, email, role, status) "
        "VALUES ('i','o3','o3@x.io','member','active') RETURNING id")
    owner = str((await cur.fetchone())[0])
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P') RETURNING id", (owner,))
    pid = str((await cur.fetchone())[0])
    await seed.seed_doc(db_conn, "d2", owner, file_name="f.pdf", project_ids=[pid])
    await db_conn.execute("DELETE FROM projects WHERE id = %s", (pid,))
    cur = await db_conn.execute("SELECT count(*) FROM project_documents WHERE doc_id='d2'")
    assert (await cur.fetchone())[0] == 0
    cur = await db_conn.execute("SELECT count(*) FROM documents WHERE doc_id='d2'")
    assert (await cur.fetchone())[0] == 1  # the doc survives its project
