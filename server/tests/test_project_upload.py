"""POST /v1/projects/{pid}/uploads — one file stored and filed into a project in one step (bulk upload spec,
phase 2): decided from the project as it is at send time, through the same decision as the plan route."""
import base64
import hashlib

import pytest

from server.services import bulk_upload
from server.tests import seed
from server.tests.chat_harness import member
from server.tests.versions_harness import build_versions_app

pytestmark = pytest.mark.asyncio

MIB = 1024 * 1024
V1 = b"Version one. It has two sentences."
V2 = b"Version two. It has different words in it."
FRESH = b"# Fresh\n\nSome new words here."


def _sha(b):
    return hashlib.sha256(b).hexdigest()


@pytest.fixture
def store(tmp_path):
    d = tmp_path / "store"
    d.mkdir()
    return d


@pytest.fixture
def roomy(monkeypatch):
    monkeypatch.setattr(bulk_upload, "free_mb", lambda: 10_000_000)


async def _seed_text(conn, data, who, name):
    await seed.seed_doc(conn, _sha(data), who, file_name=name, file_type="text", state="indexed",
                        extracted_by="server")


@pytest.fixture
async def setup(db_conn, monkeypatch, store, roomy):
    """Alice owns P, which holds notes.txt (V1 as its v1)."""
    _app, as_user = build_versions_app(db_conn, monkeypatch, storage_dir=store)
    alice = await member(db_conn, "pu-alice", caps=("reader",))
    pid = await seed.make_project(db_conn, alice.user_id, "P")
    await _seed_text(db_conn, V1, alice.user_id, "notes.txt")
    x = await seed.place_doc(db_conn, pid, _sha(V1), alice.user_id)
    return as_user, alice, pid, x


async def _up(as_user, who, pid, data, name="notes.txt", **form):
    async with as_user(who) as c:
        return await c.post(f"/v1/projects/{pid}/uploads", files={"file": (name, data, "text/plain")},
                            data={k: str(v) for k, v in form.items()})


async def _rows(conn, sql, *params):
    cur = await conn.execute(sql, params)
    return await cur.fetchall()


async def test_new_file_is_created_held_by_the_project_only_and_indexed(db_conn, setup, store):
    as_user, alice, pid, x = setup
    r = await _up(as_user, alice, pid, FRESH, name="fresh.md")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["result"] == "created" and body["doc_id"] == _sha(FRESH)
    assert body["document"]["name"] == "fresh.md" and body["document"]["version"] == 1
    assert await _rows(db_conn, "SELECT 1 FROM library_entries WHERE doc_id = %s", _sha(FRESH)) == []
    assert await _rows(db_conn, "SELECT state FROM documents WHERE doc_id = %s", _sha(FRESH)) == [("indexed",)]
    assert (store / f"{_sha(FRESH)}.md").exists()


async def test_matching_name_becomes_the_next_version_with_its_note(db_conn, setup):
    as_user, alice, pid, x = setup
    r = await _up(as_user, alice, pid, V2, note="rewrote it")
    assert r.status_code == 201, r.text
    assert r.json()["result"] == "version"
    assert r.json()["document"] == {"id": x, "name": "notes.txt", "version": 2}
    assert await _rows(db_conn, "SELECT note FROM project_documents WHERE project_doc_id = %s AND version = 2",
                       x) == [("rewrote it",)]


async def test_separate_creates_a_second_document_with_the_name(db_conn, setup):
    as_user, alice, pid, x = setup
    r = await _up(as_user, alice, pid, V2, on_match="separate")
    assert r.status_code == 201 and r.json()["result"] == "created"
    assert r.json()["document"]["id"] != x
    assert len(await _rows(db_conn, "SELECT 1 FROM project_documents WHERE project_id = %s AND is_current "
                                    "AND file_name = 'notes.txt'", pid)) == 2


async def test_skip_stores_nothing(db_conn, setup, store):
    as_user, alice, pid, x = setup
    r = await _up(as_user, alice, pid, V2, on_match="skip")
    assert r.status_code == 200 and r.json()["result"] == "skipped"
    assert r.json()["document"]["id"] == x
    assert await _rows(db_conn, "SELECT 1 FROM documents WHERE doc_id = %s", _sha(V2)) == []
    assert [p.name for p in store.iterdir() if p.is_file()] == []
    assert [p.name for p in (store / ".staging").iterdir()] == []


async def test_unchanged_stores_nothing(db_conn, setup):
    as_user, alice, pid, x = setup
    r = await _up(as_user, alice, pid, V1, name="another name.txt")
    assert r.status_code == 200 and r.json()["result"] == "unchanged"
    assert r.json()["document"]["id"] == x and r.json()["state"] == "indexed"
    assert len(await _rows(db_conn, "SELECT 1 FROM project_documents WHERE project_id = %s", pid)) == 1


async def _with_v2(db_conn, alice, x):
    await _seed_text(db_conn, V2, alice.user_id, "notes v2.txt")
    await seed.add_version(db_conn, x, _sha(V2), alice.user_id, file_name="notes v2.txt")


async def test_older_bytes_are_left_alone_by_default(db_conn, setup):
    as_user, alice, pid, x = setup
    await _with_v2(db_conn, alice, x)
    r = await _up(as_user, alice, pid, V1, name="whatever.txt")
    assert r.status_code == 200 and r.json()["result"] == "left_older"
    assert r.json()["older_version"] == 1 and r.json()["document"]["version"] == 2
    assert len(await _rows(db_conn, "SELECT 1 FROM project_documents WHERE project_doc_id = %s", x)) == 2


async def test_restore_makes_the_older_bytes_current_under_the_old_name(db_conn, setup):
    as_user, alice, pid, x = setup
    await _with_v2(db_conn, alice, x)
    r = await _up(as_user, alice, pid, V1, name="whatever.txt", on_older="restore")
    assert r.status_code == 201, r.text
    assert r.json()["result"] == "restored" and r.json()["document"]["version"] == 3
    assert await _rows(db_conn, "SELECT file_name, origin, origin_version, is_current FROM project_documents "
                                "WHERE project_doc_id = %s AND version = 3", x) == [("notes.txt", "restore", 1, True)]


async def test_ambiguous_files_nothing_unless_separate_or_skip(db_conn, setup):
    as_user, alice, pid, x = setup
    await _seed_text(db_conn, V2, alice.user_id, "notes.txt")
    y = await seed.place_doc(db_conn, pid, _sha(V2), alice.user_id)
    r = await _up(as_user, alice, pid, b"a third text")
    assert r.status_code == 200 and r.json()["result"] == "ambiguous"
    assert sorted(c["id"] for c in r.json()["candidates"]) == sorted([x, y])
    assert len(await _rows(db_conn, "SELECT 1 FROM project_documents WHERE project_id = %s", pid)) == 2
    r = await _up(as_user, alice, pid, b"a third text", on_match="skip")
    assert r.status_code == 200 and r.json()["result"] == "skipped"
    r = await _up(as_user, alice, pid, b"a third text", on_match="separate")
    assert r.status_code == 201 and r.json()["result"] == "created"


async def test_document_id_overrides_the_name_match(db_conn, setup):
    as_user, alice, pid, x = setup
    await _seed_text(db_conn, V2, alice.user_id, "other.txt")
    y = await seed.place_doc(db_conn, pid, _sha(V2), alice.user_id)
    r = await _up(as_user, alice, pid, b"a third text", document_id=y)
    assert r.status_code == 201 and r.json()["result"] == "version"
    assert r.json()["document"]["id"] == y and r.json()["document"]["version"] == 2


async def test_document_id_of_another_project_is_not_found(db_conn, setup):
    as_user, alice, pid, x = setup
    q = await seed.make_project(db_conn, alice.user_id, "Q")
    await _seed_text(db_conn, V2, alice.user_id, "q.txt")
    z = await seed.place_doc(db_conn, q, _sha(V2), alice.user_id)
    r = await _up(as_user, alice, pid, b"a third text", document_id=z)
    assert r.status_code == 404 and r.json()["detail"]["error"] == "not_found"
    assert await _rows(db_conn, "SELECT 1 FROM documents WHERE doc_id = %s", _sha(b"a third text")) == []


async def test_the_uploader_is_on_the_job_for_a_new_version(db_conn, monkeypatch, store, roomy):
    _app, as_user = build_versions_app(db_conn, monkeypatch, storage_dir=store, run_queue=False)
    alice = await member(db_conn, "pu-job-alice", caps=("reader",))
    pid = await seed.make_project(db_conn, alice.user_id, "P")
    await _seed_text(db_conn, V1, alice.user_id, "notes.txt")
    await seed.place_doc(db_conn, pid, _sha(V1), alice.user_id)
    r = await _up(as_user, alice, pid, V2)
    assert r.json()["result"] == "version" and r.json()["queued"] is True
    assert await _rows(db_conn, "SELECT requested_by::text FROM doc_index_jobs WHERE doc_id = %s",
                       _sha(V2)) == [(alice.user_id,)]


async def test_activity_entries_are_written(db_conn, setup):
    as_user, alice, pid, x = setup
    await _up(as_user, alice, pid, FRESH, name="fresh.md")
    await _up(as_user, alice, pid, V2)
    kinds = [k for (k,) in await _rows(db_conn, "SELECT kind FROM project_events WHERE project_id = %s "
                                                "ORDER BY id", pid)]
    assert kinds[-2:] == ["document.added", "document.version_added"]


async def test_decides_again_at_send_time(db_conn, setup):
    as_user, alice, pid, x = setup
    head = base64.b64encode(b"%PDF-").decode()
    data = b"%PDF-1.7 the report"
    async with as_user(alice) as c:
        plan = await c.post(f"/v1/projects/{pid}/uploads/plan", json={"files": [
            {"name": "report.pdf", "size": len(data), "sha256": _sha(data), "head": head}]})
    assert plan.json()["files"][0]["action"] == "new"
    bob = await member(db_conn, "pu-bob", caps=("reader",))
    await seed.add_member(db_conn, pid, bob.user_id, "contributor")
    await seed.seed_doc(db_conn, _sha(b"%PDF-1.7 bob's"), bob.user_id, file_name="report.pdf", state="indexed",
                        extracted_by="server")
    await seed.place_doc(db_conn, pid, _sha(b"%PDF-1.7 bob's"), bob.user_id)
    r = await _up(as_user, alice, pid, data, name="report.pdf")
    assert r.status_code == 201 and r.json()["result"] == "version"


async def test_refusals_use_the_upload_codes(setup, monkeypatch):
    as_user, alice, pid, x = setup
    monkeypatch.setenv("TEXT_UPLOAD_MAX_MB", "1")
    r = await _up(as_user, alice, pid, b"a" * (MIB + 1), name="big.txt")
    assert (r.status_code, r.json()["detail"]["error"]) == (413, "too_large")
    r = await _up(as_user, alice, pid, b"hello", name="x.pdf")
    assert (r.status_code, r.json()["detail"]["error"]) == (415, "unsupported_type")
    r = await _up(as_user, alice, pid, b"", name="e.md")
    assert (r.status_code, r.json()["detail"]["error"]) == (422, "empty_file")


async def test_not_enough_disk_after_staging_is_507_and_leaves_nothing(db_conn, setup, store, monkeypatch):
    as_user, alice, pid, x = setup
    monkeypatch.setattr(bulk_upload, "free_mb", lambda: 0)
    r = await _up(as_user, alice, pid, FRESH, name="fresh.md")
    assert r.status_code == 507 and r.json()["detail"]["error"] == "not_enough_space"
    assert await _rows(db_conn, "SELECT 1 FROM documents WHERE doc_id = %s", _sha(FRESH)) == []
    assert [p.name for p in (store / ".staging").iterdir()] == []


async def test_bad_choices_are_422(setup):
    as_user, alice, pid, x = setup
    assert (await _up(as_user, alice, pid, V2, on_match="replace")).status_code == 422
    assert (await _up(as_user, alice, pid, V2, on_older="always")).status_code == 422


async def test_a_reader_is_refused_before_anything_is_stored(db_conn, setup):
    as_user, alice, pid, x = setup
    reader = await member(db_conn, "pu-reader", caps=("reader",))
    await seed.add_member(db_conn, pid, reader.user_id, "reader")
    r = await _up(as_user, reader, pid, FRESH, name="fresh.md")
    assert r.status_code == 403
    assert await _rows(db_conn, "SELECT 1 FROM documents WHERE doc_id = %s", _sha(FRESH)) == []


async def test_plan_and_upload_agree(db_conn, monkeypatch, store, roomy):
    """Every case through the plan, then the upload with its defaults: the plan can't promise what the upload won't
    do (spec: "One decision function for both routes")."""
    _app, as_user = build_versions_app(db_conn, monkeypatch, storage_dir=store)
    alice = await member(db_conn, "pu-agree", caps=("reader",))
    expected = {"new": "created", "version": "version", "ambiguous": "ambiguous", "unchanged": "unchanged",
                "older_version": "left_older"}
    for case, result in expected.items():
        pid = await seed.make_project(db_conn, alice.user_id, case)
        data, other, third = (f"{case} file".encode(), f"{case} other".encode(), f"{case} third".encode())
        if case == "version":
            await _seed_text(db_conn, other, alice.user_id, "a.md")
            await seed.place_doc(db_conn, pid, _sha(other), alice.user_id)
        elif case == "ambiguous":
            for content in (other, third):
                await _seed_text(db_conn, content, alice.user_id, "a.md")
                await seed.place_doc(db_conn, pid, _sha(content), alice.user_id)
        elif case == "unchanged":
            await _seed_text(db_conn, data, alice.user_id, "z.md")
            await seed.place_doc(db_conn, pid, _sha(data), alice.user_id)
        elif case == "older_version":
            await _seed_text(db_conn, data, alice.user_id, "a.md")
            d = await seed.place_doc(db_conn, pid, _sha(data), alice.user_id)
            await _seed_text(db_conn, other, alice.user_id, "a.md")
            await seed.add_version(db_conn, d, _sha(other), alice.user_id)
        async with as_user(alice) as c:
            plan = await c.post(f"/v1/projects/{pid}/uploads/plan", json={"files": [
                {"name": "a.md", "size": len(data), "sha256": _sha(data), "head": None}]})
            sent = await c.post(f"/v1/projects/{pid}/uploads", files={"file": ("a.md", data, "text/markdown")})
        assert plan.json()["files"][0]["action"] == case, case
        assert sent.json()["result"] == result, (case, sent.text)
