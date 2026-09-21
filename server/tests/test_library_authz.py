import pytest
from fastapi import HTTPException
from server.auth import authz
pytestmark = pytest.mark.asyncio


async def _user(conn, sub, email):
    cur = await conn.execute(
        "INSERT INTO users (oidc_iss, oidc_sub, email, role, status) "
        "VALUES ('i',%s,%s,'member','active') RETURNING id", (sub, email))
    return str((await cur.fetchone())[0])


async def _doc(conn, doc_id, owner, project_id=None):
    await conn.execute(
        "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, user_id, project_id) "
        "VALUES (%s,'f.pdf','pdf',1,%s,%s)", (doc_id, owner, project_id))


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
    await _doc(db_conn, "d1", o, pid)
    await authz.assert_can_read_doc(db_conn, "d1", m)  # no raise


async def test_grantee_can_read(db_conn):
    o = await _user(db_conn, "o", "o@x.io")
    g = await _user(db_conn, "g", "g@x.io")
    await _doc(db_conn, "d1", o)
    await db_conn.execute(
        "INSERT INTO doc_grants (doc_id, grantee_user_id) VALUES ('d1',%s)", (g,))
    await authz.assert_can_read_doc(db_conn, "d1", g)  # no raise
