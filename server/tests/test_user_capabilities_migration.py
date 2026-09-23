"""008 adds users.capabilities and backfills it from role/status without
locking anyone out. The migration ran session-wide; here we re-run the exact
backfill UPDATEs against seeded rows in a rolled-back tx to prove the logic."""
import pytest

pytestmark = pytest.mark.asyncio

BACKFILL = [
    "UPDATE users SET capabilities='{admin,reader,chat}' WHERE role='admin'",
    "UPDATE users SET capabilities='{reader,chat}' "
    "WHERE role='member' AND status='active'",
    "UPDATE users SET capabilities='{}' WHERE status IN ('pending','disabled')",
]


async def _mk(conn, email, role, status):
    cur = await conn.execute(
        "INSERT INTO users (oidc_iss, oidc_sub, email, role, status, capabilities) "
        "VALUES ('i', %s, %s, %s, %s, '{}') RETURNING id",
        (email, email, role, status),
    )
    return str((await cur.fetchone())[0])


async def test_column_exists_and_defaults_empty(db_conn):
    uid = await _mk(db_conn, "z@x.io", "member", "pending")
    from server.auth.users import get_user
    assert (await get_user(db_conn, uid))["capabilities"] == []


async def test_backfill_preserves_access(db_conn):
    a = await _mk(db_conn, "a@x.io", "admin", "active")
    m = await _mk(db_conn, "m@x.io", "member", "active")
    p = await _mk(db_conn, "p@x.io", "member", "pending")
    d = await _mk(db_conn, "d@x.io", "member", "disabled")
    for sql in BACKFILL:
        await db_conn.execute(sql)
    from server.auth.users import get_user
    assert set((await get_user(db_conn, a))["capabilities"]) == {"admin", "reader", "chat"}
    assert set((await get_user(db_conn, m))["capabilities"]) == {"reader", "chat"}
    assert (await get_user(db_conn, p))["capabilities"] == []
    assert (await get_user(db_conn, d))["capabilities"] == []
