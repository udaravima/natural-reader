"""The inbox (spec 2026-11-06, Grouping and Delivery): notices of one kind arriving close together are one row;
unread before read, needing action first among unread, newest first; the badge counts rows; reading by ids."""
from datetime import datetime, timedelta, timezone

import pytest

from server.auth.users import resolve_or_provision_user
from server.services import notifications
from server.services.notifications import Notice, group_rows

T0 = datetime(2026, 11, 6, 9, 0, tzinfo=timezone.utc)
_next_id = iter(range(1, 10_000))


def _n(kind, minutes, *, read=False, resolved=False, title=None, target=None):
    level = notifications.KINDS[kind].level
    return Notice(id=next(_next_id), kind=kind, level=level, title=title or f"{kind} at {minutes}", body=None,
                  doc_id=None, target=target, repeats=1, created_at=T0 + timedelta(minutes=minutes),
                  read_at=(T0 + timedelta(days=1)) if read else None,
                  resolved_at=(T0 + timedelta(hours=1)) if resolved else None)


def test_neighbours_29_minutes_apart_group_31_do_not():
    rows = group_rows([_n("doc.indexed", 0), _n("doc.indexed", 29), _n("doc.indexed", 60)])
    assert [r["count"] for r in rows] == [1, 2]
    assert rows[1]["title"] == "2 documents finished indexing"
    assert rows[0]["title"] == "doc.indexed at 60"


def test_a_slow_run_of_neighbours_stays_one_group():
    rows = group_rows([_n("doc.indexed", m) for m in (0, 25, 50, 75, 100)])
    assert len(rows) == 1 and rows[0]["count"] == 5


def test_a_group_never_mixes_kinds_levels_or_read_state():
    rows = group_rows([_n("doc.indexed", 0), _n("doc.index_failed", 1), _n("doc.indexed", 2),
                       _n("doc.index_failed", 3), _n("doc.indexed", 4, read=True)])
    shapes = sorted((r["kind"], r["unread"], r["count"]) for r in rows)
    assert shapes == [("doc.index_failed", True, 2), ("doc.indexed", False, 1), ("doc.indexed", True, 2)]


def test_a_group_of_one_is_a_plain_row():
    target = notifications.doc_target("d1", "Q3.pdf")
    [row] = group_rows([_n("doc.indexed", 0, title="Q3.pdf finished indexing.", target=target)])
    assert row["count"] == 1 and row["title"] == "Q3.pdf finished indexing."
    assert row["target"] == target and len(row["ids"]) == 1 and row["more"] == 0
    assert row["items"][0]["title"] == "Q3.pdf finished indexing."


def test_a_group_has_no_target_of_its_own():
    [row] = group_rows([_n("doc.indexed", 0, target={"doc": "a"}), _n("doc.indexed", 1, target={"doc": "b"})])
    assert row["target"] is None and row["body"] is None
    assert [i["target"] for i in row["items"]] == [{"doc": "b"}, {"doc": "a"}]  # newest first


def test_order_unread_action_then_unread_info_then_read_newest_first():
    rows = group_rows([
        _n("doc.index_failed", 0),            # unread, needs action, oldest
        _n("doc.indexed", 100),               # unread, info, newest
        _n("doc.converted", 200, read=True),  # read, newest of all
        _n("doc.convert_failed", 150, read=True),
    ])
    assert [r["kind"] for r in rows] == ["doc.index_failed", "doc.indexed", "doc.converted",
                                         "doc.convert_failed"]
    assert [r["unread"] for r in rows] == [True, True, False, False]
    # A read failure still needs action (its problem isn't resolved): it only loses its place at the top.
    assert rows[0]["needs_action"] and not rows[2]["needs_action"] and rows[3]["needs_action"]


def test_a_resolved_failure_sorts_as_information():
    rows = group_rows([_n("doc.index_failed", 0, resolved=True), _n("doc.indexed", 10)])
    assert [r["kind"] for r in rows] == ["doc.indexed", "doc.index_failed"]
    assert rows[1]["needs_action"] is False and rows[1]["resolved"] == 1


def test_a_group_with_one_member_unresolved_still_needs_action():
    [row] = group_rows([_n("doc.index_failed", 0, resolved=True), _n("doc.index_failed", 5),
                        _n("doc.index_failed", 9)])
    assert row["needs_action"] is True and row["resolved"] == 1 and row["count"] == 3


def test_a_group_of_60_gives_50_details_and_60_ids():
    [row] = group_rows([_n("doc.indexed", m) for m in range(60)])
    assert row["count"] == 60 and len(row["ids"]) == 60
    assert len(row["items"]) == 50 and row["more"] == 10
    assert row["items"][0]["title"] == "doc.indexed at 59"
    assert row["at"] == T0 + timedelta(minutes=59)


# ---- in the database ----

async def _user(conn, sub):
    return (await resolve_or_provision_user(conn, iss="i", sub=sub, email=f"{sub}@x.io"))["id"]


async def _at(conn, minutes_ago):
    await conn.execute("UPDATE notifications SET created_at = now() - make_interval(mins => %s) "
                       "WHERE id = (SELECT max(id) FROM notifications)", (minutes_ago,))


@pytest.mark.asyncio
async def test_inbox_pages_by_rows(db_conn):
    a = await _user(db_conn, "a")
    for i, ago in enumerate((300, 200, 100)):          # three rows: more than 30 minutes apart
        await notifications.send(db_conn, [a], "doc.indexed", title=f"t{i}")
        await _at(db_conn, ago)
    first = await notifications.inbox(db_conn, a, offset=0, limit=2)
    assert first["total"] == 3 and [r["title"] for r in first["rows"]] == ["t2", "t1"]
    rest = await notifications.inbox(db_conn, a, offset=2, limit=2)
    assert [r["title"] for r in rest["rows"]] == ["t0"]


@pytest.mark.asyncio
async def test_unread_counts_count_a_group_once(db_conn):
    a = await _user(db_conn, "a")
    for i in range(20):
        await notifications.send(db_conn, [a], "doc.indexed", title=f"t{i}")
        await _at(db_conn, 20 - i)
    await notifications.send(db_conn, [a], "rebuild.failed", title="r", key="rebuild:d1")
    assert await notifications.unread_counts(db_conn, a) == {"count": 2, "action": 1}
    await notifications.resolve(db_conn, "rebuild:d1")
    assert await notifications.unread_counts(db_conn, a) == {"count": 2, "action": 0}


@pytest.mark.asyncio
async def test_mark_read_touches_only_the_persons_unread_ids(db_conn):
    a, b = await _user(db_conn, "a"), await _user(db_conn, "b")
    await notifications.send(db_conn, [a, b], "doc.indexed", title="t")
    cur = await db_conn.execute("SELECT id, user_id::text FROM notifications ORDER BY id")
    ids = {uid: nid for nid, uid in await cur.fetchall()}
    assert await notifications.mark_read(db_conn, a, [ids[a], ids[b]]) == 1
    assert await notifications.mark_read(db_conn, a, [ids[a]]) == 0   # already read
    assert await notifications.unread_counts(db_conn, b) == {"count": 1, "action": 0}


@pytest.mark.asyncio
async def test_mark_all_read(db_conn):
    a, b = await _user(db_conn, "a"), await _user(db_conn, "b")
    await notifications.send(db_conn, [a], "doc.indexed", title="t1")
    await notifications.send(db_conn, [a], "doc.index_failed", title="t2", key="index:d1")
    await notifications.send(db_conn, [b], "doc.indexed", title="t3")
    assert await notifications.mark_all_read(db_conn, a) == 2
    assert await notifications.unread_counts(db_conn, a) == {"count": 0, "action": 0}
    assert await notifications.unread_counts(db_conn, b) == {"count": 1, "action": 0}
