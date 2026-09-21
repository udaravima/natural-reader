import pytest
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


async def test_documents_gain_project_and_tags(db_conn):
    cur = await db_conn.execute(
        "INSERT INTO users (oidc_iss, oidc_sub, email, role, status) "
        "VALUES ('i','o2','o2@x.io','member','active') RETURNING id")
    owner = str((await cur.fetchone())[0])
    await db_conn.execute(
        "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, user_id, tags) "
        "VALUES ('d1','f.pdf','pdf',10,%s,'{alpha,beta}')", (owner,))
    cur = await db_conn.execute("SELECT tags, project_id FROM documents WHERE doc_id='d1'")
    tags, project_id = await cur.fetchone()
    assert set(tags) == {"alpha", "beta"} and project_id is None
