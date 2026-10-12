"""doc_versions' writes (V1 spec §4): every change appends or removes a
version under the document's lock, applies the same-bytes rules, records
one event, and refuses before writing anything."""
import pytest
from fastapi import HTTPException

from server.auth import authz
from server.services import doc_versions
from server.tests import seed
from server.tests.chat_harness import member

pytestmark = pytest.mark.asyncio

C1, C2, C3, C4 = "1" * 64, "2" * 64, "3" * 64, "4" * 64


@pytest.fixture
async def proj(db_conn):
    alice = await member(db_conn, "w-alice")
    pid = await seed.make_project(db_conn, alice.user_id, "P")
    for i, doc in enumerate((C1, C2, C3, C4), start=1):
        await seed.seed_doc(db_conn, doc, alice.user_id, file_name=f"f{i}.pdf", state="indexed")
    x = await seed.place_doc(db_conn, pid, C1, alice.user_id, file_name="report.pdf")
    return alice, pid, x


async def _versions(db_conn, pdoc):
    cur = await db_conn.execute(
        "SELECT version, doc_id, file_name, origin, origin_version, is_current FROM project_documents "
        "WHERE project_doc_id = %s ORDER BY version", (pdoc,))
    return [tuple(r) for r in await cur.fetchall()]


async def _events(db_conn, pid):
    cur = await db_conn.execute(
        "SELECT kind, doc_id, details FROM project_events WHERE project_id = %s "
        "AND kind LIKE 'document.%%' ORDER BY id", (pid,))
    return [tuple(r) for r in await cur.fetchall()]


async def _refused(coro, status, code=None):
    with pytest.raises(HTTPException) as e:
        await coro
    assert e.value.status_code == status
    if code:
        assert e.value.detail["error"] == code
    return e.value.detail


async def test_add_version_appends_a_current_version_and_records_it(db_conn, proj):
    alice, pid, x = proj
    out = await doc_versions.add_version(db_conn, alice.user_id, pid, x, doc_id=C2,
                                         file_name="report-v2.pdf", note="  fixed table 4 ")
    assert out == {"version": 2, "doc_id": C2, "restored_from": None}
    assert await _versions(db_conn, x) == [
        (1, C1, "report.pdf", "upload", None, False), (2, C2, "report-v2.pdf", "upload", None, True)]
    assert await _events(db_conn, pid) == [("document.version_added", C2,
                                            {"name": "report-v2.pdf", "version": 2,
                                             "note": "fixed table 4"})]


async def test_the_current_files_bytes_are_refused_and_nothing_changes(db_conn, proj):
    alice, pid, x = proj
    detail = await _refused(doc_versions.add_version(db_conn, alice.user_id, pid, x, doc_id=C1,
                                                     file_name="again.pdf"), 409, "already_current")
    assert detail["document"] == {"id": x, "name": "report.pdf", "version": 1}
    assert len(await _versions(db_conn, x)) == 1 and await _events(db_conn, pid) == []


async def test_an_older_versions_bytes_become_a_restore_named_like_it(db_conn, proj):
    alice, pid, x = proj
    await doc_versions.add_version(db_conn, alice.user_id, pid, x, doc_id=C2, file_name="v2.pdf")
    out = await doc_versions.add_version(db_conn, alice.user_id, pid, x, doc_id=C1,
                                         file_name="whatever.pdf")
    assert out == {"version": 3, "doc_id": C1, "restored_from": 1}
    assert (await _versions(db_conn, x))[-1] == (3, C1, "report.pdf", "restore", 1, True)


async def test_another_documents_current_bytes_are_refused_naming_it(db_conn, proj):
    alice, pid, x = proj
    y = await seed.place_doc(db_conn, pid, C3, alice.user_id, file_name="other.pdf")
    detail = await _refused(doc_versions.add_version(db_conn, alice.user_id, pid, x, doc_id=C3,
                                                     file_name="o.pdf"), 409, "current_elsewhere")
    assert detail["document"]["id"] == y
    await _refused(doc_versions.add_version(db_conn, alice.user_id, pid, x, doc_id=C2,
                                            file_name="x.pdf", note="n" * 501), 422, "note_too_long")


async def test_a_lost_race_is_a_409_that_still_names_a_document(db_conn, proj):
    """Controller ruling P16: the unique index is the last net. When the
    conflicting current version can't be named (here it is unverified, so
    invisible), the 409 falls back to the document being changed, never null."""
    alice, pid, x = proj
    bob = await member(db_conn, "w-bob0")
    await seed.place_doc(db_conn, pid, C3, bob.user_id, verified=False)
    detail = await _refused(
        doc_versions._append(db_conn, pid, x, doc_id=C3, file_name="r.pdf", note=None,
                             origin="upload", origin_version=None, added_by=alice.user_id),
        409, "current_elsewhere")
    assert detail["document"] == {"id": x, "name": "report.pdf", "version": 1}
    assert len(await _versions(db_conn, x)) == 1  # the savepoint rolled the append back


async def test_a_lost_race_on_a_visible_document_names_it(db_conn, proj):
    alice, pid, x = proj
    y = await seed.place_doc(db_conn, pid, C3, alice.user_id, file_name="other.pdf")
    detail = await _refused(
        doc_versions._append(db_conn, pid, x, doc_id=C3, file_name="r.pdf", note=None,
                             origin="upload", origin_version=None, added_by=alice.user_id),
        409, "current_elsewhere")
    assert detail["document"]["id"] == y


async def test_restore_appends_a_copy_of_the_old_version(db_conn, proj):
    alice, pid, x = proj
    await doc_versions.add_version(db_conn, alice.user_id, pid, x, doc_id=C2, file_name="v2.pdf")
    out = await doc_versions.restore_version(db_conn, alice.user_id, pid, x, 1)
    assert out == {"version": 3, "doc_id": C1, "restored_from": 1}
    assert (await _events(db_conn, pid))[-1] == (
        "document.version_restored", C1, {"name": "report.pdf", "version": 3, "from_version": 1})
    await _refused(doc_versions.restore_version(db_conn, alice.user_id, pid, x, 3), 409, "already_current")
    await _refused(doc_versions.restore_version(db_conn, alice.user_id, pid, x, 9), 404, "not_found")


async def test_restoring_bytes_a_later_version_already_holds_is_refused(db_conn, proj):
    alice, pid, x = proj
    await doc_versions.add_version(db_conn, alice.user_id, pid, x, doc_id=C2, file_name="v2.pdf")
    assert (await doc_versions.restore_version(db_conn, alice.user_id, pid, x, 1))["version"] == 3
    detail = await _refused(doc_versions.restore_version(db_conn, alice.user_id, pid, x, 1),
                            409, "already_current")
    assert detail["document"] == {"id": x, "name": "report.pdf", "version": 3}
    cur = await db_conn.execute("SELECT last_version FROM project_docs WHERE id = %s", (x,))
    assert (await cur.fetchone())[0] == 3 and len(await _versions(db_conn, x)) == 3


async def test_delete_old_version_gcs_content_nobody_else_holds(db_conn, proj, tmp_path):
    alice, pid, x = proj
    f = tmp_path / "c4.pdf"
    f.write_bytes(b"%PDF-1.4")
    await db_conn.execute("UPDATE documents SET bytes_path = %s WHERE doc_id = %s", (str(f), C4))
    await db_conn.execute("DELETE FROM library_entries WHERE doc_id = %s", (C4,))
    await seed.add_version(db_conn, x, C4, alice.user_id, current=False)
    await doc_versions.add_version(db_conn, alice.user_id, pid, x, doc_id=C2, file_name="v3.pdf")
    await doc_versions.delete_version(db_conn, alice.user_id, pid, x, 2)
    assert [v[0] for v in await _versions(db_conn, x)] == [1, 3]
    cur = await db_conn.execute("SELECT 1 FROM documents WHERE doc_id = %s", (C4,))
    assert await cur.fetchone() is None and not f.exists()
    assert (await _events(db_conn, pid))[-1][0] == "document.version_deleted"
    await _refused(doc_versions.delete_version(db_conn, alice.user_id, pid, x, 3), 409, "is_current")


async def test_delete_keeps_content_someone_still_holds(db_conn, proj):
    """Review Focus 5: only the version row goes; the holder still reads it."""
    alice, pid, x = proj
    bob = await member(db_conn, "w-bob")
    await seed.share_doc(db_conn, C1, alice.user_id, bob.user_id)
    await doc_versions.add_version(db_conn, alice.user_id, pid, x, doc_id=C2, file_name="v2.pdf")
    await doc_versions.delete_version(db_conn, alice.user_id, pid, x, 1)
    await authz.assert_can_read_doc(db_conn, C1, bob.user_id)  # no 404


async def test_merge_moves_the_history_after_the_targets(db_conn, proj):
    alice, pid, x = proj
    y = await seed.place_doc(db_conn, pid, C2, alice.user_id, file_name="report-final.pdf")
    await seed.add_version(db_conn, y, C3, alice.user_id, file_name="report-final-2.pdf")
    await seed.add_version(db_conn, y, C4, alice.user_id, current=False, verified=False)
    await doc_versions.merge_documents(db_conn, alice.user_id, pid, x, y)
    assert await _versions(db_conn, x) == [
        (1, C1, "report.pdf", "upload", None, False),
        (2, C2, "report-final.pdf", "merge", 1, False),
        (3, C3, "report-final-2.pdf", "merge", 2, True),
        (4, C4, "f4.pdf", "merge", 3, False)]
    cur = await db_conn.execute("SELECT count(*) FROM project_docs WHERE id = %s", (y,))
    assert (await cur.fetchone())[0] == 0
    assert (await _events(db_conn, pid))[-1] == (
        "document.merged", C3,
        {"from_name": "report-final-2.pdf", "into_name": "report.pdf", "versions": 2})
    cur = await db_conn.execute("SELECT last_version FROM project_docs WHERE id = %s", (x,))
    assert (await cur.fetchone())[0] == 4


async def test_merge_refusals(db_conn, proj):
    alice, pid, x = proj
    await _refused(doc_versions.merge_documents(db_conn, alice.user_id, pid, x, x), 409, "merge_self")
    other = await seed.make_project(db_conn, alice.user_id, "Q")
    z = await seed.place_doc(db_conn, other, C2, alice.user_id)
    await _refused(doc_versions.merge_documents(db_conn, alice.user_id, pid, x, z), 404, "not_found")


async def test_merge_self_is_404_unless_the_document_is_visible_here(db_conn, proj):
    alice, pid, x = proj
    other = await seed.make_project(db_conn, alice.user_id, "Q")
    z = await seed.place_doc(db_conn, other, C2, alice.user_id)
    await _refused(doc_versions.merge_documents(db_conn, alice.user_id, pid, z, z), 404, "not_found")
    ghost = "00000000-0000-4000-8000-000000000000"
    await _refused(doc_versions.merge_documents(db_conn, alice.user_id, pid, ghost, ghost), 404, "not_found")
    detail = await _refused(doc_versions.merge_documents(db_conn, alice.user_id, pid, x, x),
                            409, "merge_self")
    assert detail["document"]["id"] == x


async def test_remove_document_gcs_every_versions_content(db_conn, proj):
    alice, pid, x = proj
    await db_conn.execute("DELETE FROM library_entries WHERE doc_id IN (%s,%s)", (C1, C2))
    await doc_versions.add_version(db_conn, alice.user_id, pid, x, doc_id=C2, file_name="v2.pdf")
    await doc_versions.remove_document(db_conn, alice.user_id, pid, x)
    cur = await db_conn.execute("SELECT count(*) FROM documents WHERE doc_id IN (%s,%s)", (C1, C2))
    assert (await cur.fetchone())[0] == 0
    assert (await _events(db_conn, pid))[-1] == (
        "document.removed", C2, {"name": "v2.pdf", "versions": 2})


async def test_numbers_are_never_reused(db_conn, proj):
    alice, pid, x = proj
    await doc_versions.add_version(db_conn, alice.user_id, pid, x, doc_id=C2, file_name="v2.pdf")
    await doc_versions.add_version(db_conn, alice.user_id, pid, x, doc_id=C3, file_name="v3.pdf")
    await doc_versions.delete_version(db_conn, alice.user_id, pid, x, 2)
    out = await doc_versions.add_version(db_conn, alice.user_id, pid, x, doc_id=C4, file_name="v4.pdf")
    assert out["version"] == 4


async def test_a_legacy_unverified_current_version_is_verified_in_place(db_conn, proj):
    alice, pid, _x = proj
    bob = await member(db_conn, "w-bob2")
    y = await seed.place_doc(db_conn, pid, C3, bob.user_id, verified=False)
    assert await doc_versions.verify_legacy_current(db_conn, pid, C3, alice.user_id) == y
    assert await doc_versions.document_ref(db_conn, y) is not None
    assert await doc_versions.verify_legacy_current(db_conn, pid, C3, alice.user_id) is None


async def test_clean_note():
    assert doc_versions.clean_note(None) is None
    assert doc_versions.clean_note("   ") is None
    assert doc_versions.clean_note(" ok ") == "ok"
    assert doc_versions.clean_note("x" * 500) == "x" * 500
    with pytest.raises(HTTPException) as e:
        doc_versions.clean_note("x" * 501)
    assert e.value.status_code == 422
