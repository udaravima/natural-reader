"""Notification cleanup (spec 2026-11-06, Cleanup): read ones 30 days after they were read, unread ones 90 days
after they arrived, at startup and then every 24 hours; NOTIFICATIONS_READ_DAYS / NOTIFICATIONS_UNREAD_DAYS
override, 0 keeps forever, a bad value warns and keeps the default."""
import logging

import pytest

from server.auth.users import resolve_or_provision_user
from server.services import notifications


def test_defaults_are_30_and_90():
    assert notifications.retention({}) == (30, 90)


def test_overrides_and_zero():
    assert notifications.retention({"NOTIFICATIONS_READ_DAYS": "7", "NOTIFICATIONS_UNREAD_DAYS": "0"}) == (7, 0)


@pytest.mark.parametrize("bad", ["-3", "ten", "1.5"])
def test_a_bad_value_warns_and_keeps_the_default(bad, caplog):
    with caplog.at_level(logging.WARNING, logger="server.services.notifications"):
        assert notifications.retention({"NOTIFICATIONS_READ_DAYS": bad, "NOTIFICATIONS_UNREAD_DAYS": bad}) == (30, 90)
    assert "NOTIFICATIONS_READ_DAYS" in caplog.text and "NOTIFICATIONS_UNREAD_DAYS" in caplog.text


async def _seed(conn):
    uid = (await resolve_or_provision_user(conn, iss="i", sub="a", email="a@x.io"))["id"]
    rows = {  # title: (read days ago or None, created days ago)
        "read 31": (31, 40), "read 29": (29, 40), "unread 91": (None, 91), "unread 89": (None, 89)}
    for title, (read_ago, created_ago) in rows.items():
        await conn.execute(
            "INSERT INTO notifications (user_id, kind, level, title, created_at, read_at) VALUES "
            "(%s, 'doc.indexed', 'info', %s, now() - make_interval(days => %s), "
            "CASE WHEN %s::int IS NULL THEN NULL ELSE now() - make_interval(days => %s::int) END)",
            (uid, title, created_ago, read_ago, read_ago))


async def _titles(conn):
    cur = await conn.execute("SELECT title FROM notifications ORDER BY title")
    return [r[0] for r in await cur.fetchall()]


@pytest.mark.asyncio
async def test_purge_deletes_read_after_30_days_and_unread_after_90(db_conn):
    await _seed(db_conn)
    assert await notifications.purge(db_conn, 30, 90) == 2
    assert await _titles(db_conn) == ["read 29", "unread 89"]


@pytest.mark.asyncio
async def test_zero_keeps_forever(db_conn):
    await _seed(db_conn)
    assert await notifications.purge(db_conn, 0, 0) == 0
    assert await notifications.purge(db_conn, 0, 90) == 1     # only the old unread one
    assert await _titles(db_conn) == ["read 29", "read 31", "unread 89"]


@pytest.mark.asyncio
async def test_retention_starts_and_stops(monkeypatch):
    calls = []

    async def fake_loop(read_days, unread_days):
        calls.append((read_days, unread_days))

    monkeypatch.setattr(notifications, "_purge_loop", fake_loop)
    monkeypatch.setenv("NOTIFICATIONS_READ_DAYS", "0")
    monkeypatch.setenv("NOTIFICATIONS_UNREAD_DAYS", "0")
    notifications.start_retention()
    assert notifications._task is None                        # both 0: nothing to do
    monkeypatch.setenv("NOTIFICATIONS_UNREAD_DAYS", "5")
    notifications.start_retention()
    await notifications.stop_retention()
    assert notifications._task is None
    assert calls in ([(0, 5)], [])                             # cancelled before or after it ran
