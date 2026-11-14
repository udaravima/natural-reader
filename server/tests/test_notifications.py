"""Notifications (spec 2026-11-06): sending, merging a repeat of the same occurrence, admins, resolving, and a send
that fails never breaking the caller's transaction."""
import logging

import pytest

from server.auth.users import resolve_or_provision_user
from server.services import notifications

pytestmark = pytest.mark.asyncio


async def _user(conn, sub, *, admin=False, status="active"):
    uid = (await resolve_or_provision_user(conn, iss="i", sub=sub, email=f"{sub}@x.io"))["id"]
    caps = "{admin}" if admin else "{reader}"
    await conn.execute("UPDATE users SET capabilities = %s, status = %s WHERE id = %s", (caps, status, uid))
    return uid


async def _rows(conn, uid=None):
    cur = await conn.execute(
        "SELECT user_id::text, kind, level, title, body, repeats, read_at, resolved_at, target FROM notifications "
        "WHERE (%s::uuid IS NULL OR user_id = %s::uuid) ORDER BY id", (uid, uid))
    return await cur.fetchall()


async def test_send_writes_one_row_per_person_with_the_kinds_level(db_conn):
    a, b = await _user(db_conn, "a"), await _user(db_conn, "b")
    n = await notifications.send(db_conn, [a, b], "doc.indexed", title="Q3.pdf finished indexing.",
                                 target=notifications.doc_target("d1", "Q3.pdf"))
    assert n == 2
    rows = await _rows(db_conn)
    assert sorted(r[0] for r in rows) == sorted([a, b])
    for r in rows:
        assert r[1:4] == ("doc.indexed", "info", "Q3.pdf finished indexing.")
        assert r[5] == 1 and r[6] is None and r[7] is None
        assert r[8] == {"doc": "d1", "name": "Q3.pdf"}


async def test_a_needs_action_kind_is_stored_as_action(db_conn):
    a = await _user(db_conn, "a")
    await notifications.send(db_conn, [a], "doc.index_failed", title="Couldn't index Q3.pdf.")
    assert (await _rows(db_conn))[0][2] == "action"


async def test_unknown_kind_is_refused(db_conn):
    a = await _user(db_conn, "a")
    with pytest.raises(ValueError):
        await notifications.send(db_conn, [a], "doc.teleported", title="x")


async def test_same_key_while_unread_merges(db_conn):
    a = await _user(db_conn, "a")
    await notifications.send(db_conn, [a], "doc.index_failed", title="first", key="index:d1")
    await notifications.resolve(db_conn, "index:d1")
    await notifications.send(db_conn, [a], "doc.index_failed", title="second", body="why", key="index:d1")
    rows = await _rows(db_conn)
    assert len(rows) == 1
    assert rows[0][3] == "second" and rows[0][4] == "why" and rows[0][5] == 2
    assert rows[0][7] is None  # the problem is back: no longer resolved


async def test_same_key_after_read_is_a_new_notice(db_conn):
    a = await _user(db_conn, "a")
    await notifications.send(db_conn, [a], "doc.index_failed", title="first", key="index:d1")
    await db_conn.execute("UPDATE notifications SET read_at = now()")
    await notifications.send(db_conn, [a], "doc.index_failed", title="second", key="index:d1")
    assert [r[3] for r in await _rows(db_conn)] == ["first", "second"]


async def test_send_admins_reaches_active_admins_only(db_conn):
    admin = await _user(db_conn, "adm", admin=True)
    await _user(db_conn, "gone", admin=True, status="disabled")
    await _user(db_conn, "reader")
    n = await notifications.send_admins(db_conn, "reembed.done", title="The library now uses m.",
                                        target=notifications.ADMIN_MODELS)
    assert n == 1
    rows = await _rows(db_conn)
    assert [r[0] for r in rows] == [admin] and rows[0][8] == {"admin": "models"}


async def test_resolve_marks_every_recipient(db_conn):
    await _user(db_conn, "a1", admin=True)
    await _user(db_conn, "a2", admin=True)
    await notifications.send_admins(db_conn, "rebuild.failed", title="x", key="rebuild:d1",
                                    target=notifications.ADMIN_OVERVIEW)
    assert await notifications.resolve(db_conn, "rebuild:d1") == 2
    assert all(r[7] is not None for r in await _rows(db_conn))
    assert await notifications.resolve(db_conn, "rebuild:d1") == 0


async def test_a_failing_send_does_not_abort_the_callers_transaction(db_conn, caplog):
    with caplog.at_level(logging.WARNING, logger="server.services.notifications"):
        n = await notifications.send(db_conn, ["not-a-uuid"], "doc.indexed", title="x")
    assert n == 0
    assert "not sent" in caplog.text
    cur = await db_conn.execute("SELECT 1")
    assert (await cur.fetchone()) == (1,)


async def test_sending_to_nobody_writes_nothing(db_conn):
    assert await notifications.send(db_conn, [], "doc.indexed", title="x") == 0
    assert await _rows(db_conn) == []
