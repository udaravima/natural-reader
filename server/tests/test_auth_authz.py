import pytest
from fastapi import HTTPException

from server.auth.authz import assert_owns_doc, assert_owns_session
from server.auth.users import resolve_or_provision_user

SEED = "00000000-0000-0000-0000-000000000001"


async def _doc(db_conn, doc_id, owner):
    await db_conn.execute(
        "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, user_id) "
        "VALUES (%s,'f','text',1,%s)",
        (doc_id, owner),
    )


async def test_owner_ok(db_conn):
    await _doc(db_conn, "d1", SEED)
    await assert_owns_doc(db_conn, "d1", SEED)  # no raise


async def test_missing_doc_is_404(db_conn):
    with pytest.raises(HTTPException) as e:
        await assert_owns_doc(db_conn, "nope", SEED)
    assert e.value.status_code == 404


async def test_non_owner_is_404_not_403(db_conn):
    # First login claims the seed admin; the second is a distinct user.
    await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="admin@x.io")
    other = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    assert other["id"] != SEED
    await _doc(db_conn, "d2", SEED)
    with pytest.raises(HTTPException) as e:
        await assert_owns_doc(db_conn, "d2", other["id"])
    assert e.value.status_code == 404


async def test_session_ownership(db_conn):
    await db_conn.execute(
        "INSERT INTO chat_sessions (id, user_id) VALUES ('sess1', %s)", (SEED,)
    )
    await assert_owns_session(db_conn, "sess1", SEED)  # no raise
    with pytest.raises(HTTPException) as e:
        await assert_owns_session(db_conn, "sess1", "00000000-0000-0000-0000-000000000009")
    assert e.value.status_code == 404
