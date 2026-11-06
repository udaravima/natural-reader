"""Migration 026 (notifications): the table, the level CHECK, and the partial unique index that keeps one unread
notice per (person, key)."""
import pytest
from psycopg.errors import CheckViolation, UniqueViolation

from server.auth.users import resolve_or_provision_user

pytestmark = pytest.mark.asyncio


async def _user(conn, sub):
    return (await resolve_or_provision_user(conn, iss="i", sub=sub, email=f"{sub}@x.io"))["id"]


async def _insert(conn, uid, *, level="info", key=None, read=False):
    await conn.execute(
        "INSERT INTO notifications (user_id, kind, level, title, key, read_at) "
        "VALUES (%s, 'doc.indexed', %s, 't', %s, CASE WHEN %s THEN now() END)", (uid, level, key, read))


async def test_the_level_check_refuses_anything_but_action_and_info(db_conn):
    uid = await _user(db_conn, "a")
    await _insert(db_conn, uid, level="action")
    await _insert(db_conn, uid, level="info")
    with pytest.raises(CheckViolation):
        async with db_conn.transaction():
            await _insert(db_conn, uid, level="urgent")


async def test_one_unread_notice_per_person_and_key(db_conn):
    a, b = await _user(db_conn, "a"), await _user(db_conn, "b")
    await _insert(db_conn, a, key="index:d1")
    await _insert(db_conn, b, key="index:d1")            # another person: fine
    await _insert(db_conn, a, key="index:d1", read=True)  # a read one: fine
    await _insert(db_conn, a)                             # no key: fine
    await _insert(db_conn, a)
    with pytest.raises(UniqueViolation):
        async with db_conn.transaction():
            await _insert(db_conn, a, key="index:d1")


async def test_a_deleted_document_leaves_the_notice(db_conn):
    uid = await _user(db_conn, "a")
    await db_conn.execute("INSERT INTO documents (doc_id, file_name, file_type, size_bytes) "
                          "VALUES ('d1', 'f.pdf', 'pdf', 1)")
    await db_conn.execute("INSERT INTO notifications (user_id, kind, level, title, doc_id) "
                          "VALUES (%s, 'doc.indexed', 'info', 't', 'd1')", (uid,))
    await db_conn.execute("DELETE FROM documents WHERE doc_id = 'd1'")
    cur = await db_conn.execute("SELECT doc_id FROM notifications WHERE user_id = %s", (uid,))
    assert (await cur.fetchone()) == (None,)
