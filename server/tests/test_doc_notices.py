"""Document notices (spec 2026-11-06; plan ruling P1): the person whose request started an indexing or a conversion
hears how it ended, read from the document's committed state after the job, under their own name for it; admins
hear about passages the model shortened."""
import hashlib

import pytest

from server.auth import deps
from server.auth.users import resolve_or_provision_user
from server.services import doc_notices, embeddings, notifications
from server.tests import seed
from server.tests.docs_harness import _PoolShim, build_docs_app

D = "d" * 64


async def _user(conn, sub):
    return (await resolve_or_provision_user(conn, iss="i", sub=sub, email=f"{sub}@x.io"))["id"]


@pytest.fixture
def pool(db_conn, monkeypatch):
    monkeypatch.setattr(doc_notices, "get_pool", lambda: _PoolShim(db_conn))


async def _notices(conn, uid=None, kind=None):
    cur = await conn.execute(
        "SELECT user_id::text, kind, title, body, key, target, resolved_at FROM notifications "
        "WHERE (%s::uuid IS NULL OR user_id = %s::uuid) AND (%s::text IS NULL OR kind = %s) ORDER BY id",
        (uid, uid, kind, kind))
    return await cur.fetchall()


def _ends(conn, **cols):
    """A stand-in job that leaves the document in the given state."""
    async def job(doc_id, *args):
        sets = ", ".join(f"{k} = %s" for k in cols)
        await conn.execute(f"UPDATE documents SET {sets} WHERE doc_id = %s", (*cols.values(), doc_id))
    return job


@pytest.fixture
async def ann_and_ben(db_conn):
    ann, ben = await _user(db_conn, "ann"), await _user(db_conn, "ben")
    await seed.seed_doc(db_conn, D, ann, file_name="canonical.pdf", state="indexing")
    await db_conn.execute("UPDATE library_entries SET file_name = 'Q3.pdf' WHERE user_id = %s", (ann,))
    await db_conn.execute("INSERT INTO library_entries (user_id, doc_id, added_via) VALUES (%s, %s, 'upload')",
                          (ben, D))
    return ann, ben


async def test_indexed_tells_the_requester_by_their_own_name_for_it(db_conn, pool, ann_and_ben):
    ann, ben = ann_and_ben
    await doc_notices.run_and_notify(_ends(db_conn, state="indexed"), D, requested_by=ann, via="index")
    assert await _notices(db_conn, ben) == []
    [(_, kind, title, body, key, target, _r)] = await _notices(db_conn, ann)
    assert (kind, title, body, key) == ("doc.indexed", "Q3.pdf finished indexing.", None, None)
    assert target == {"doc": D, "name": "Q3.pdf"}


async def test_failed_indexing_needs_action_and_a_later_success_resolves_it(db_conn, pool, ann_and_ben):
    ann, _ = ann_and_ben
    await doc_notices.run_and_notify(
        _ends(db_conn, state="failed", error_message="No text found — try Convert (OCR)."), D,
        requested_by=ann, via="index")
    [(_, kind, title, body, key, _t, resolved)] = await _notices(db_conn, ann)
    assert (kind, title, body, key, resolved) == (
        "doc.index_failed", "Couldn't index Q3.pdf.", "No text found — try Convert (OCR).", f"index:{D}", None)
    await doc_notices.run_and_notify(_ends(db_conn, state="indexed", error_message=None), D,
                                     requested_by=ann, via="index")
    rows = await _notices(db_conn, ann)
    assert [r[1] for r in rows] == ["doc.index_failed", "doc.indexed"] and rows[0][6] is not None


async def test_a_conversion_and_its_indexing_send_one_notice(db_conn, pool, ann_and_ben):
    ann, _ = ann_and_ben
    await notifications.send(db_conn, [ann], "doc.convert_failed", title="old", key=f"convert:{D}")
    await doc_notices.run_and_notify(_ends(db_conn, state="indexed", conversion_state="converted"), D, {"a": 1},
                                     requested_by=ann, via="convert")
    rows = await _notices(db_conn, ann)
    assert [r[1] for r in rows] == ["doc.convert_failed", "doc.converted"]
    assert rows[1][2] == "Q3.pdf is converted and ready to chat with."
    assert rows[0][6] is not None                                  # the old failure is resolved


async def test_a_failed_conversion(db_conn, pool, ann_and_ben):
    ann, _ = ann_and_ben
    await doc_notices.run_and_notify(
        _ends(db_conn, state="indexed", conversion_state="conversion_failed", conversion_error="Docling crashed"),
        D, {}, requested_by=ann, via="convert")
    [(_, kind, title, body, key, _t, _r)] = await _notices(db_conn, ann)
    assert (kind, title, body, key) == ("doc.convert_failed", "Couldn't convert Q3.pdf.", "Docling crashed",
                                        f"convert:{D}")


async def test_a_conversion_whose_indexing_fails_sends_index_failed_only(db_conn, pool, ann_and_ben):
    ann, _ = ann_and_ben
    await doc_notices.run_and_notify(
        _ends(db_conn, state="failed", conversion_state="converted", error_message="embedding service down"),
        D, {}, requested_by=ann, via="convert")
    assert [r[1] for r in await _notices(db_conn, ann)] == ["doc.index_failed"]


async def test_a_job_still_running_elsewhere_sends_nothing(db_conn, pool, ann_and_ben):
    ann, _ = ann_and_ben
    await doc_notices.run_and_notify(_ends(db_conn, state="indexing"), D, requested_by=ann, via="index")
    assert await _notices(db_conn) == []


async def test_shortened_passages_tell_the_admins_once(db_conn, pool, ann_and_ben):
    ann, _ = ann_and_ben
    admins = await notifications.admin_ids(db_conn)

    async def job(doc_id):
        for _ in range(12):
            embeddings.note_shortened()
        await db_conn.execute("UPDATE documents SET state = 'indexed' WHERE doc_id = %s", (doc_id,))

    await doc_notices.run_and_notify(job, D, requested_by=ann, via="index")
    rows = await _notices(db_conn, kind="embed.shortened")
    assert len(rows) == len(admins) >= 1
    assert rows[0][2].endswith("refused 12 passages of canonical.pdf; they were shortened to fit.")
    assert rows[0][5] == {"admin": "models"}


async def test_a_requester_who_no_longer_holds_it_still_hears(db_conn, pool, ann_and_ben):
    ann, _ = ann_and_ben

    async def job(doc_id):
        await db_conn.execute("DELETE FROM library_entries WHERE user_id = %s", (ann,))
        await db_conn.execute("UPDATE documents SET state = 'indexed' WHERE doc_id = %s", (doc_id,))

    await doc_notices.run_and_notify(job, D, requested_by=ann, via="index")
    [(_, kind, title, *_rest)] = await _notices(db_conn, ann)
    assert (kind, title) == ("doc.indexed", "canonical.pdf finished indexing.")


async def test_a_job_that_raises_still_notifies_from_the_state_it_left(db_conn, pool, ann_and_ben):
    ann, _ = ann_and_ben

    async def job(doc_id):
        await db_conn.execute("UPDATE documents SET state = 'failed', error_message = 'boom' WHERE doc_id = %s",
                              (doc_id,))
        raise RuntimeError("unexpected")

    await doc_notices.run_and_notify(job, D, requested_by=ann, via="index")   # logged, not raised
    assert [r[1] for r in await _notices(db_conn, ann)] == ["doc.index_failed"]


async def test_a_deleted_document_sends_nothing(db_conn, pool, ann_and_ben):
    ann, _ = ann_and_ben

    async def job(doc_id):
        await db_conn.execute("DELETE FROM documents WHERE doc_id = %s", (doc_id,))

    await doc_notices.run_and_notify(job, D, requested_by=ann, via="index")
    assert await _notices(db_conn) == []


# ---- through the routes ----

TEXT = b"The first sentence is here. The second one follows! Is this the third one?"


async def test_an_upload_tells_the_uploader_when_it_is_indexed(db_conn, monkeypatch, tmp_path):
    _app, as_user = build_docs_app(db_conn, monkeypatch, storage_dir=tmp_path)
    ann, ben = await _user(db_conn, "ann"), await _user(db_conn, "ben")
    for uid in (ann, ben):
        await db_conn.execute("UPDATE users SET status = 'active', capabilities = '{reader}' WHERE id = %s", (uid,))

    def principal(uid):
        return deps.Principal(user_id=uid, email="x@x.io", role="member", capabilities=frozenset({"reader"}))

    async with as_user(principal(ann)) as c:
        r = await c.post("/v1/docs", files={"file": ("notes.txt", TEXT, "text/plain")})
        assert r.status_code == 202
    doc_id = hashlib.sha256(TEXT).hexdigest()
    [(_, kind, title, *_rest)] = await _notices(db_conn, ann)
    assert (kind, title) == ("doc.indexed", "notes.txt finished indexing.")
    async with as_user(principal(ben)) as c:                     # the same bytes: a dedupe, no job, no notice
        r = await c.post("/v1/docs", files={"file": ("mine.txt", TEXT, "text/plain")})
        assert r.status_code == 200 and r.json()["doc_id"] == doc_id
    assert await _notices(db_conn, ben) == []


async def test_a_conversion_that_never_ran_says_nothing_and_resolves_nothing(db_conn, pool, ann_and_ben):
    # The convert job returned early (database not ready), or failed and couldn't even record that: the document
    # is still "indexed" from before, and nothing was converted.
    ann, _ = ann_and_ben
    await db_conn.execute("UPDATE documents SET state = 'indexed' WHERE doc_id = %s", (D,))
    await notifications.send(db_conn, [ann], "doc.convert_failed", title="old", key=f"convert:{D}")

    async def job(doc_id, *args):
        return None
    await doc_notices.run_and_notify(job, D, {}, requested_by=ann, via="convert")
    [(_, kind, _title, _b, _k, _t, resolved)] = await _notices(db_conn, ann)
    assert kind == "doc.convert_failed" and resolved is None


async def test_a_long_error_is_cut_to_500_characters(db_conn, pool, ann_and_ben):
    ann, _ = ann_and_ben
    await doc_notices.run_and_notify(_ends(db_conn, state="failed", error_message="e" * 5000), D,
                                     requested_by=ann, via="index")
    await doc_notices.run_and_notify(
        _ends(db_conn, state="indexed", conversion_state="conversion_failed", conversion_error="c" * 5000),
        D, {}, requested_by=ann, via="convert")
    bodies = {r[1]: r[3] for r in await _notices(db_conn, ann)}
    assert bodies == {"doc.index_failed": "e" * 500, "doc.convert_failed": "c" * 500}
