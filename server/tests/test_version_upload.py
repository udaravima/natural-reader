"""POST /v1/projects/{pid}/documents/{id}/versions — a revised file's bytes
become the new version (V1 spec §4.4): A1's upload pipeline with a version
row as the holding, the document locked first, no library entry."""
import hashlib

import pytest

from server.routers import docs as docs_router
from server.tests import seed
from server.tests.chat_harness import member
from server.tests.docs_harness import fake_embed
from server.tests.versions_harness import build_versions_app

pytestmark = pytest.mark.asyncio

V1 = b"Version one. It has two sentences."
V2 = b"Version two. It has different words in it."


def _sha(b):
    return hashlib.sha256(b).hexdigest()


class _CountingEmbed:
    def __init__(self):
        self.calls = 0

    async def __call__(self, texts):
        self.calls += 1
        return await fake_embed(texts)


@pytest.fixture
def store(tmp_path):
    d = tmp_path / "store"
    d.mkdir()
    return d


@pytest.fixture
def embed():
    return _CountingEmbed()


@pytest.fixture
async def setup(db_conn, monkeypatch, store, embed):
    _app, as_user = build_versions_app(db_conn, monkeypatch, storage_dir=store, embed=embed)
    alice = await member(db_conn, "up-alice", caps=("reader",))
    pid = await seed.make_project(db_conn, alice.user_id, "P")
    await seed.seed_doc(db_conn, _sha(V1), alice.user_id, file_name="notes.txt", file_type="text",
                        state="indexed", extracted_by="server")
    x = await seed.place_doc(db_conn, pid, _sha(V1), alice.user_id)
    return as_user, alice, pid, x


def _upload(c, pid, x, data, name="notes.txt", **form):
    return c.post(f"/v1/projects/{pid}/documents/{x}/versions",
                  files={"file": (name, data, "text/plain")}, data=form)


async def test_new_bytes_become_the_indexed_current_version_without_a_library_entry(db_conn, setup):
    as_user, alice, pid, x = setup
    async with as_user(alice) as c:
        r = await _upload(c, pid, x, V2, name="notes v2.txt", note="rewrote it")
    assert r.status_code == 201, r.text
    assert r.json()["version"] == 2 and r.json()["doc_id"] == _sha(V2)
    cur = await db_conn.execute("SELECT state FROM documents WHERE doc_id = %s", (_sha(V2),))
    assert (await cur.fetchone())[0] == "indexed"  # the background job ran
    cur = await db_conn.execute("SELECT 1 FROM library_entries WHERE doc_id = %s", (_sha(V2),))
    assert await cur.fetchone() is None
    cur = await db_conn.execute("SELECT file_name, note, is_current FROM project_documents "
                                "WHERE project_doc_id = %s AND version = 2", (x,))
    assert await cur.fetchone() == ("notes v2.txt", "rewrote it", True)


async def test_known_bytes_are_not_reprocessed(db_conn, setup, embed):
    as_user, alice, pid, x = setup
    await seed.seed_doc(db_conn, _sha(V2), alice.user_id, file_name="v2.txt", file_type="text",
                        state="indexed", extracted_by="server")
    async with as_user(alice) as c:
        r = await _upload(c, pid, x, V2)
    assert r.status_code == 201 and embed.calls == 0


async def test_same_bytes_rules(db_conn, setup):
    as_user, alice, pid, x = setup
    async with as_user(alice) as c:
        r = await _upload(c, pid, x, V1)
        assert r.status_code == 409 and r.json()["detail"]["error"] == "already_current"
        assert (await _upload(c, pid, x, V2)).status_code == 201
        r = await _upload(c, pid, x, V1, name="renamed.txt")
        assert r.status_code == 201 and r.json()["restored_from"] == 1


async def test_row_upload_ignores_another_documents_name(db_conn, setup):
    """Review Focus 2: the row action is explicit, so no name prompt."""
    as_user, alice, pid, x = setup
    other = b"Another document. Different words."
    await seed.seed_doc(db_conn, _sha(other), alice.user_id, file_name="taken.txt",
                        file_type="text", state="indexed", extracted_by="server")
    await seed.place_doc(db_conn, pid, _sha(other), alice.user_id)
    async with as_user(alice) as c:
        r = await _upload(c, pid, x, V2, name="taken.txt")
    assert r.status_code == 201


async def test_note_rules_apply_before_bytes_are_stored(db_conn, setup, store):
    """Review Focus 4."""
    as_user, alice, pid, x = setup
    async with as_user(alice) as c:
        r = await _upload(c, pid, x, V2, note="n" * 501)
        assert r.status_code == 422
        assert list(store.iterdir()) == []  # nothing staged or placed
        r = await _upload(c, pid, x, V2, note="   ")
    assert r.status_code == 201
    cur = await db_conn.execute("SELECT note FROM project_documents "
                                "WHERE project_doc_id = %s AND version = 2", (x,))
    assert (await cur.fetchone())[0] is None


async def test_role_and_visibility_before_any_bytes(db_conn, setup, store):
    as_user, _alice, pid, x = setup
    reader = await member(db_conn, "up-reader", caps=("reader",))
    stranger = await member(db_conn, "up-stranger", caps=("reader",))
    await seed.add_member(db_conn, pid, reader.user_id, "reader")
    async with as_user(reader) as c:
        assert (await _upload(c, pid, x, V2)).status_code == 403
    async with as_user(stranger) as c:
        assert (await _upload(c, pid, x, V2)).status_code == 404
    assert list(store.iterdir()) == []


async def test_a_lost_race_with_gc_still_retries_once_for_a_version(db_conn, setup, monkeypatch):
    """Spec §9: A1's retry holds when the holding is a version, not an entry."""
    as_user, alice, pid, x = setup
    bob = await member(db_conn, "up-bob", caps=("reader",))
    await seed.seed_doc(db_conn, _sha(V2), bob.user_id, file_name="v2.txt", file_type="text",
                        state="indexed", extracted_by="server")
    real = docs_router._lock_existing
    calls = []

    async def gc_wins_once(conn, did):
        calls.append(did)
        if len(calls) == 1:
            # A GC committed between our conflicting INSERT and our lock.
            await conn.execute("DELETE FROM documents WHERE doc_id = %s", (did,))
            return None
        return await real(conn, did)

    monkeypatch.setattr(docs_router, "_lock_existing", gc_wins_once)
    async with as_user(alice) as c:
        r = await _upload(c, pid, x, V2)
    assert r.status_code == 201 and r.json()["version"] == 2
    assert calls == [_sha(V2)]  # the retry's INSERT created the row; no second lock


async def test_the_document_is_locked_before_the_content_row(db_conn, setup, monkeypatch):
    """Spec §4.4's lock order: prelock runs before the content INSERT, so
    new content doesn't exist yet when the document is locked."""
    as_user, alice, pid, x = setup
    seen = {}
    original = docs_router._store_and_hold

    async def spy(staged, name, hold, *, prelock=None):
        async def checking(conn):
            cur = await conn.execute("SELECT 1 FROM documents WHERE doc_id = %s", (staged.sha256,))
            seen["content_existed"] = await cur.fetchone() is not None
            await prelock(conn)
        return await original(staged, name, hold, prelock=checking)

    monkeypatch.setattr(docs_router, "_store_and_hold", spy)
    async with as_user(alice) as c:
        assert (await _upload(c, pid, x, V2)).status_code == 201
    assert seen == {"content_existed": False}
