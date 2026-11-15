"""POST /v1/projects/{pid}/uploads/plan — what each file would become in the project, nothing stored
(bulk upload spec, phase 2: "The two routes")."""
import base64
import dataclasses
import hashlib

import pytest

from server.auth import scopes
from server.services import bulk_upload
from server.tests import seed
from server.tests.chat_harness import member
from server.tests.versions_harness import build_versions_app

pytestmark = pytest.mark.asyncio

MIB = 1024 * 1024
V1 = b"Version one. It has two sentences."
V2 = b"Version two. It has different words in it."
PDF = b"%PDF-1.7 a pdf"


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def _head(b):
    return base64.b64encode(b[:5]).decode()


def _file(name, data=None, *, size=None, sha=True, head=True):
    data = data if data is not None else name.encode()
    return {"name": name, "size": len(data) if size is None else size,
            "sha256": _sha(data) if sha else None, "head": _head(data) if head else None}


@pytest.fixture
def store(tmp_path):
    d = tmp_path / "store"
    d.mkdir()
    return d


@pytest.fixture
async def setup(db_conn, monkeypatch, store):
    """Alice owns P, which holds notes.txt (V1 as its v1)."""
    _app, as_user = build_versions_app(db_conn, monkeypatch, storage_dir=store)
    monkeypatch.setattr(bulk_upload, "free_mb", lambda: 10_000_000)
    alice = await member(db_conn, "plan-alice", caps=("reader",))
    pid = await seed.make_project(db_conn, alice.user_id, "P")
    await seed.seed_doc(db_conn, _sha(V1), alice.user_id, file_name="notes.txt", file_type="text",
                        state="indexed", extracted_by="server")
    x = await seed.place_doc(db_conn, pid, _sha(V1), alice.user_id)
    return as_user, alice, pid, x


async def _plan(as_user, who, pid, files):
    async with as_user(who) as c:
        return await c.post(f"/v1/projects/{pid}/uploads/plan", json={"files": files})


async def test_each_action(db_conn, setup):
    as_user, alice, pid, x = setup
    await seed.seed_doc(db_conn, _sha(V2), alice.user_id, file_name="notes.txt", file_type="text",
                        state="indexed", extracted_by="server")
    await seed.add_version(db_conn, x, _sha(V2), alice.user_id, file_name="notes.txt")  # V2 is v2, V1 is old
    r = await _plan(as_user, alice, pid, [
        _file("new.md", b"fresh words"), _file("notes.txt", b"a third text"),
        _file("copy.txt", V2), _file("old.txt", V1)])
    assert r.status_code == 200, r.text
    got = [(f["name"], f["action"], (f["document"] or {}).get("id"), f["version"]) for f in r.json()["files"]]
    assert got == [("new.md", "new", None, None), ("notes.txt", "version", x, 3),
                   ("copy.txt", "unchanged", x, 2), ("old.txt", "older_version", x, 1)]


async def test_two_documents_with_the_name_are_ambiguous_with_every_candidate(db_conn, setup):
    as_user, alice, pid, x = setup
    await seed.seed_doc(db_conn, _sha(V2), alice.user_id, file_name="notes.txt", file_type="text",
                        state="indexed", extracted_by="server")
    y = await seed.place_doc(db_conn, pid, _sha(V2), alice.user_id)
    r = await _plan(as_user, alice, pid, [_file("notes.txt", b"a third text")])
    f = r.json()["files"][0]
    assert f["action"] == "ambiguous" and f["document"] is None
    assert sorted(c["id"] for c in f["candidates"]) == sorted([x, y])
    for c in f["candidates"]:
        assert c["name"] == "notes.txt" and c["version"] == 1
        assert c["added_by"]["id"] == alice.user_id and c["added_at"]


async def test_names_compare_case_and_spaces_ignored(setup):
    as_user, alice, pid, x = setup
    r = await _plan(as_user, alice, pid, [_file(" NOTES.TXT ", b"other")])
    assert r.json()["files"][0]["action"] == "version"


async def test_a_path_in_the_name_is_cut_to_its_last_part(setup):
    as_user, alice, pid, x = setup
    r = await _plan(as_user, alice, pid, [_file("a/b/notes.txt", b"other"), _file("a\\notes.txt", b"more")])
    assert [(f["name"], f["action"]) for f in r.json()["files"]] == [("notes.txt", "version"),
                                                                       ("notes.txt", "version")]


async def test_docx_and_text_named_pdf_are_refused_from_their_first_bytes(setup):
    as_user, alice, pid, x = setup
    r = await _plan(as_user, alice, pid, [_file("notes.docx", b"PK\x03\x04\x14rest"),
                                          _file("x.pdf", b"hello there"), _file("ok.pdf", PDF)])
    got = [(f["action"], f["code"]) for f in r.json()["files"]]
    assert got == [("refused", "unsupported_type"), ("refused", "unsupported_type"), ("new", None)]
    assert r.json()["files"][0]["message"] == "Not a PDF, Markdown or text file."


async def test_utf16_text_is_refused(setup):
    as_user, alice, pid, x = setup
    r = await _plan(as_user, alice, pid, [_file("a.txt", "hello".encode("utf-16-le"))])
    assert r.json()["files"][0]["code"] == "unsupported_type"


async def test_the_text_cap_is_10_mb(setup):
    as_user, alice, pid, x = setup
    r = await _plan(as_user, alice, pid, [_file("a.md", b"# hi", size=10 * MIB + 1),
                                          _file("b.md", b"# hi", size=10 * MIB)])
    a, b = r.json()["files"]
    assert a["code"] == "too_large" and "10 MB" in a["message"]
    assert b["action"] == "new"


async def test_the_pdf_cap_is_50_mb(setup):
    as_user, alice, pid, x = setup
    r = await _plan(as_user, alice, pid, [_file("a.pdf", PDF, size=50 * MIB + 1)])
    assert r.json()["files"][0]["message"] == "Over the 50 MB limit for a PDF."


async def test_empty_file_is_refused(setup):
    as_user, alice, pid, x = setup
    r = await _plan(as_user, alice, pid, [_file("a.md", b"", head=False)])
    assert r.json()["files"][0]["code"] == "empty_file"


async def test_without_a_hash_only_names_match(setup):
    as_user, alice, pid, x = setup
    r = await _plan(as_user, alice, pid, [_file("other.txt", V1, sha=False), _file("notes.txt", V1, sha=False)])
    assert [f["action"] for f in r.json()["files"]] == ["new", "version"]


async def test_without_a_head_the_type_is_not_judged(setup):
    as_user, alice, pid, x = setup
    r = await _plan(as_user, alice, pid, [_file("notes.docx", b"PK\x03\x04", head=False)])
    assert r.json()["files"][0]["action"] == "new"


async def test_the_match_never_reaches_outside_the_project(db_conn, setup):
    as_user, alice, pid, x = setup
    bob = await member(db_conn, "plan-bob", caps=("reader",))
    q = await seed.make_project(db_conn, bob.user_id, "Q")
    await seed.seed_doc(db_conn, _sha(V2), bob.user_id, file_name="q.txt", file_type="text",
                        state="indexed", extracted_by="server")
    await seed.place_doc(db_conn, q, _sha(V2), bob.user_id)
    r = await _plan(as_user, alice, pid, [_file("q.txt", V2)])
    assert r.json()["files"][0]["action"] == "new"
    await seed.add_member(db_conn, q, alice.user_id, "contributor")  # a member of Q too: still only P counts
    r = await _plan(as_user, alice, pid, [_file("q.txt", V2)])
    assert r.json()["files"][0]["action"] == "new"


async def test_not_enough_disk_refuses_the_whole_plan(setup, monkeypatch):
    as_user, alice, pid, x = setup
    monkeypatch.setattr(bulk_upload, "free_mb", lambda: 100)
    monkeypatch.setenv("UPLOAD_MIN_FREE_MB", "1024")
    r = await _plan(as_user, alice, pid, [_file("a.pdf", PDF, size=3 * MIB)])
    assert r.status_code == 507, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "not_enough_space"
    assert detail["message"] == ("This upload needs 3 MB, and the server has 100 MB free, "
                                 "of which it keeps 1024 MB in reserve.")


async def test_unchanged_and_refused_files_need_no_disk(setup, monkeypatch):
    as_user, alice, pid, x = setup
    monkeypatch.setattr(bulk_upload, "free_mb", lambda: 0)
    r = await _plan(as_user, alice, pid, [_file("notes.txt", V1), _file("e.md", b"", head=False)])
    assert r.status_code == 200, r.text


async def test_over_the_limit_is_refused_whole(setup, monkeypatch):
    as_user, alice, pid, x = setup
    monkeypatch.setenv("BULK_UPLOAD_MAX_FILES", "2")
    r = await _plan(as_user, alice, pid, [_file("a.md"), _file("b.md"), _file("c.md")])
    assert r.status_code == 413
    assert r.json()["detail"]["error"] == "too_many_files"
    assert "2" in r.json()["detail"]["message"]


async def test_reader_and_non_member_are_refused(db_conn, setup):
    as_user, alice, pid, x = setup
    reader = await member(db_conn, "plan-reader", caps=("reader",))
    await seed.add_member(db_conn, pid, reader.user_id, "reader")
    outsider = await member(db_conn, "plan-outsider", caps=("reader",))
    r = await _plan(as_user, reader, pid, [_file("a.md")])
    assert r.status_code == 403 and r.json()["detail"]["error"] == "insufficient_role"
    r = await _plan(as_user, outsider, pid, [_file("a.md")])
    assert r.status_code == 404


async def test_documents_token_works_and_chat_token_does_not(setup):
    as_user, alice, pid, x = setup
    account = frozenset({"reader", "chat"})
    docs_token = dataclasses.replace(alice, capabilities=scopes.narrow(account, frozenset({"documents"})),
                                     scopes=frozenset({"documents"}), account_capabilities=account)
    chat_token = dataclasses.replace(alice, capabilities=scopes.narrow(account, frozenset({"chat"})),
                                     scopes=frozenset({"chat"}), account_capabilities=account)
    assert (await _plan(as_user, docs_token, pid, [_file("a.md")])).status_code == 200
    r = await _plan(as_user, chat_token, pid, [_file("a.md")])
    assert r.status_code == 403 and r.json()["detail"]["error"] == "missing_capability"


async def test_nothing_is_stored(db_conn, setup, store):
    as_user, alice, pid, x = setup

    async def counts():
        cur = await db_conn.execute("SELECT (SELECT count(*) FROM documents), (SELECT count(*) FROM project_documents),"
                                    " (SELECT count(*) FROM project_events)")
        return await cur.fetchone()
    before = await counts()
    r = await _plan(as_user, alice, pid, [_file("new.md", b"fresh"), _file("notes.txt", b"v2 words")])
    assert r.status_code == 200
    assert await counts() == before
    assert [p.name for p in store.iterdir() if p.name != ".staging"] == []


async def test_clean_name():
    assert bulk_upload.clean_name("  a/b/c.pdf ") == "c.pdf"
    assert bulk_upload.clean_name("C:\\docs\\x.md") == "x.md"
    assert bulk_upload.clean_name("dir/") == "document"
    assert bulk_upload.clean_name(None) == "document"
    assert bulk_upload.clean_name("n" * 300 + ".pdf") == "n" * 255


async def test_bad_settings_fall_back_to_the_defaults(monkeypatch):
    monkeypatch.setenv("BULK_UPLOAD_MAX_FILES", "lots")
    monkeypatch.setenv("UPLOAD_MIN_FREE_MB", "-5")
    assert bulk_upload.max_files() == 200
    assert bulk_upload.reserve_mb() == 1024
