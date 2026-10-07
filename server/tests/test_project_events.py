"""A0 §3.2: the project activity feed — one row per change, people shown by
their current names, document names kept as they were, paged newest first,
and an optional retention window in days."""
import logging

import pytest

from server.auth.users import resolve_or_provision_user
from server.services import project_events
from server.tests import seed

async def _user(conn, sub, first=None):
    return (await resolve_or_provision_user(conn, iss="i", sub=sub, email=f"{sub}@x.io",
                                            first_name=first))["id"]


async def test_record_rejects_unknown_kinds(db_conn):
    owner = await _user(db_conn, "o")
    pid = await seed.make_project(db_conn, owner)
    with pytest.raises(ValueError):
        await project_events.record(db_conn, pid, owner, "member.teleported")


async def test_record_writes_the_row_and_an_audit_line_without_names(db_conn, caplog):
    owner, ben = await _user(db_conn, "o", "Olu"), await _user(db_conn, "b", "Ben")
    pid = await seed.make_project(db_conn, owner, "Secret Plans")
    with caplog.at_level(logging.INFO, logger="server.audit"):
        await project_events.record(db_conn, pid, owner, "member.added", subject_user_id=ben,
                                    details={"role": "maintainer", "via": "member"})
        await project_events.record(db_conn, pid, owner, "project.renamed",
                                    details={"from": "Secret Plans", "to": "Open Plans"})
    assert f"member.added project={pid} by={owner} user={ben} role=maintainer via=member" in caplog.text
    assert "Secret" not in caplog.text and "Ben" not in caplog.text
    cur = await db_conn.execute(
        "SELECT kind, subject_user_id, details FROM project_events WHERE project_id=%s "
        "AND kind <> 'project.created' ORDER BY id", (pid,))
    rows = await cur.fetchall()
    assert [(r[0], str(r[1]) if r[1] else None) for r in rows] == [
        ("member.added", ben), ("project.renamed", None)]
    assert rows[0][2] == {"role": "maintainer", "via": "member"}


async def test_document_events_are_not_audited_twice(db_conn, caplog):
    owner = await _user(db_conn, "o")
    pid = await seed.make_project(db_conn, owner)
    with caplog.at_level(logging.INFO, logger="server.audit"):
        await project_events.record(db_conn, pid, owner, "document.added", doc_id="d" * 64,
                                    details={"name": "Q3.pdf"})
    assert "document.added" not in caplog.text  # the route audits placement.added


async def test_page_newest_first_with_current_names_and_paging(db_conn):
    owner, ben = await _user(db_conn, "o", "Olu"), await _user(db_conn, "b", "Ben")
    pid = await seed.make_project(db_conn, owner)
    for i in range(3):
        await project_events.record(db_conn, pid, owner, "member.added", subject_user_id=ben,
                                    details={"role": "reader", "via": "member", "n": i})
    first = await project_events.page(db_conn, pid, limit=2)
    assert [e["details"]["n"] for e in first["events"]] == [2, 1]
    assert first["events"][0]["actor"] == {"id": owner, "name": "Olu"}
    assert first["events"][0]["subject"] == {"id": ben, "name": "Ben"}
    assert first["events"][0]["doc"] is None
    # seed.make_project writes no project.created event, so page two holds only n=0.
    second = await project_events.page(db_conn, pid, before=first["next_before"], limit=2)
    assert [e["details"]["n"] for e in second["events"]] == [0]
    assert second["next_before"] is None


async def test_page_shows_a_deleted_actor_as_null_and_keeps_document_names(db_conn):
    owner, gone = await _user(db_conn, "o"), await _user(db_conn, "g")
    pid = await seed.make_project(db_conn, owner, members=[(gone, "contributor")])
    await project_events.record(db_conn, pid, gone, "document.removed", doc_id="e" * 64,
                                details={"name": "Old.pdf"})
    await db_conn.execute("DELETE FROM users WHERE id = %s", (gone,))
    ev = (await project_events.page(db_conn, pid))["events"][0]
    assert ev["actor"] is None
    assert ev["doc"] == {"id": "e" * 64, "name": "Old.pdf"}


async def test_page_clamps_limit(db_conn):
    owner = await _user(db_conn, "o")
    pid = await seed.make_project(db_conn, owner)
    for _ in range(3):
        await project_events.record(db_conn, pid, owner, "project.described")
    assert len((await project_events.page(db_conn, pid, limit=0))["events"]) == 1
    assert len((await project_events.page(db_conn, pid, limit=500))["events"]) == 3


def test_retention_days_parsing(caplog):
    assert project_events.retention_days({}) == 0
    assert project_events.retention_days({"PROJECT_EVENTS_RETENTION_DAYS": "365"}) == 365
    with caplog.at_level(logging.WARNING):
        assert project_events.retention_days({"PROJECT_EVENTS_RETENTION_DAYS": "-1"}) == 0
        assert project_events.retention_days({"PROJECT_EVENTS_RETENTION_DAYS": "a year"}) == 0
    assert caplog.text.count("PROJECT_EVENTS_RETENTION_DAYS") == 2


async def test_purge_removes_only_older_events_and_zero_keeps_all(db_conn):
    owner = await _user(db_conn, "o")
    pid = await seed.make_project(db_conn, owner)
    await db_conn.execute(
        "INSERT INTO project_events (project_id, at, kind) VALUES (%s, now() - interval '40 days', "
        "'project.described')", (pid,))
    assert await project_events.purge(db_conn, 0) == 0
    assert await project_events.purge(db_conn, 30) == 1
    cur = await db_conn.execute("SELECT kind FROM project_events WHERE project_id=%s", (pid,))
    assert [r[0] for r in await cur.fetchall()] == []
