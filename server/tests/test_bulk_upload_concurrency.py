"""Uploads into one project decide one at a time (bulk upload plan, Task 2), on real, separately committing
connections. The overlap is forced: a third connection holds the project's upload lock, both uploads start, and
only once pg_stat_activity shows both waiting does the holder commit."""
import pytest

from server.services import bulk_upload
from server.tests.test_doc_versions_concurrency import C, _in_tx, _race, scratch  # noqa: F401 — the fixture

pytestmark = pytest.mark.asyncio

LOCK = "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))"


def _upload(uid, pid, doc_id, name):
    """One upload's transaction, as the route runs it: decide and lock, then file when it stores. The content rows
    exist already (the fixture made C[1..5])."""
    async def run(conn):
        outcome = await bulk_upload.decide_and_lock(conn, uid, pid, name=name, sha256=doc_id, document_id=None,
                                                    on_match="version", on_older="leave")
        if outcome.stores:
            await bulk_upload.file_it(conn, uid, pid, outcome, doc_id=doc_id, name=name, note=None)
        return outcome.result
    return run


async def _seed_document(url, uid, pid, doc_id, name):
    from server.services import doc_versions
    return await _in_tx(url, lambda c: doc_versions.create_document(c, uid, pid, doc_id, file_name=name))


async def test_two_uploads_of_one_name_at_once_become_consecutive_versions(scratch):  # noqa: F811
    url, conn, uid, pid = scratch
    x = await _seed_document(url, uid, pid, C[1], "notes.txt")
    results = await _race(url, conn, LOCK, (f"project_upload:{pid}",),
                          _upload(uid, pid, C[2], "notes.txt"), _upload(uid, pid, C[3], "notes.txt"))
    assert results == ["version", "version"], results
    cur = await conn.execute("SELECT version FROM project_documents WHERE project_doc_id = %s ORDER BY 1", (x,))
    assert [r[0] for r in await cur.fetchall()] == [1, 2, 3]


async def test_two_uploads_of_one_new_name_at_once_become_a_document_and_its_version_2(scratch):  # noqa: F811
    url, conn, uid, pid = scratch
    results = await _race(url, conn, LOCK, (f"project_upload:{pid}",),
                          _upload(uid, pid, C[2], "fresh.md"), _upload(uid, pid, C[3], "fresh.md"))
    assert sorted(results) == ["created", "version"], results
    cur = await conn.execute("SELECT count(DISTINCT project_doc_id), count(*) FROM project_documents "
                             "WHERE project_id = %s AND file_name = 'fresh.md'", (pid,))
    assert await cur.fetchone() == (1, 2)


async def test_two_uploads_of_the_same_bytes_at_once_answer_unchanged_not_409(scratch):  # noqa: F811
    url, conn, uid, pid = scratch
    await _seed_document(url, uid, pid, C[1], "notes.txt")
    results = await _race(url, conn, LOCK, (f"project_upload:{pid}",),
                          _upload(uid, pid, C[2], "notes.txt"), _upload(uid, pid, C[2], "notes.txt"))
    assert sorted(results) == ["unchanged", "version"], results
