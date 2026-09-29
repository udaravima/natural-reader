"""GET /v1/docs/{id}/file: the stored bytes, for anyone who can read the
document (the same `can_read` predicate as GET /v1/docs/{id}) — the owner,
a share recipient, a project owner or member. Anyone else, including a
holder whose pre-A1 entry was never verified, gets 404. The download is named
with the caller's own name for the document, never a stranger's."""
from __future__ import annotations

from urllib.parse import unquote

import pytest

from server.auth import deps
from server.auth.users import resolve_or_provision_user, set_status
from server.tests import seed
from server.tests.docs_harness import build_docs_app

pytestmark = pytest.mark.asyncio

DOC = "c" * 64
PDF = b"%PDF-1.4\n% the stored bytes\n"


@pytest.fixture
def as_user(db_conn, monkeypatch, tmp_path):
    _app, as_user = build_docs_app(db_conn, monkeypatch, storage_dir=tmp_path)
    return as_user


@pytest.fixture
def stored(tmp_path):
    path = tmp_path / f"{DOC}.pdf"
    path.write_bytes(PDF)
    return path


async def _member(db_conn, sub):
    u = await resolve_or_provision_user(db_conn, iss="i", sub=sub, email=f"{sub}@x.io")
    await set_status(db_conn, u["id"], "active")
    return deps.Principal(user_id=u["id"], email=u["email"], role="member",
                          capabilities=frozenset({"reader"}))


def _disposition(r):
    """(type, file name) from Content-Disposition — plain `filename="…"`, or
    RFC 6266 `filename*=utf-8''…` for names that need encoding."""
    kind, _, param = r.headers["content-disposition"].partition("; ")
    if param.startswith("filename*=utf-8''"):
        return kind, unquote(param[len("filename*=utf-8''"):])
    assert param.startswith('filename="') and param.endswith('"')
    return kind, param[len('filename="'):-1]


async def _get(as_user, who, doc_id=DOC):
    async with as_user(who) as c:
        return await c.get(f"/v1/docs/{doc_id}/file")


async def _project(db_conn, owner, *members):
    cur = await db_conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P') RETURNING id", (owner,))
    pid = str((await cur.fetchone())[0])
    for m in members:
        await db_conn.execute(
            "INSERT INTO project_members (project_id, user_id) VALUES (%s,%s)", (pid, m))
    return pid


async def test_owner_gets_the_bytes_inline_with_the_stored_type(db_conn, as_user, stored):
    owner = await _member(db_conn, "owner")
    await seed.seed_doc(db_conn, DOC, owner.user_id, file_name="paper.pdf", bytes_path=stored)
    r = await _get(as_user, owner)
    assert r.status_code == 200
    assert r.content == PDF
    assert r.headers["content-type"] == "application/pdf"
    assert _disposition(r) == ("inline", "paper.pdf")


async def test_share_recipient_gets_the_bytes_under_the_name_the_sharer_gave(
        db_conn, as_user, stored):
    owner, recipient = await _member(db_conn, "owner"), await _member(db_conn, "recipient")
    await seed.seed_doc(db_conn, DOC, owner.user_id, file_name="canonical.pdf",
                        bytes_path=stored)
    await seed.share_doc(db_conn, DOC, owner.user_id, recipient.user_id)
    await db_conn.execute(
        "UPDATE library_entries SET file_name = 'from the sharer.pdf' "
        "WHERE user_id = %s AND doc_id = %s", (recipient.user_id, DOC))
    r = await _get(as_user, recipient)
    assert r.status_code == 200
    assert r.content == PDF
    assert _disposition(r) == ("inline", "from the sharer.pdf")


async def test_the_name_is_the_callers_own_entry_name(db_conn, as_user, stored):
    first, second = await _member(db_conn, "first"), await _member(db_conn, "second")
    await seed.seed_doc(db_conn, DOC, first.user_id, file_name="first's name.pdf",
                        bytes_path=stored)
    await db_conn.execute(
        "INSERT INTO library_entries (user_id, doc_id, added_via, verified, file_name) "
        "VALUES (%s,%s,'upload',true,'my copy.pdf')", (second.user_id, DOC))
    r = await _get(as_user, second)
    assert r.status_code == 200
    assert _disposition(r) == ("inline", "my copy.pdf")


async def test_project_member_and_owner_get_the_bytes(db_conn, as_user, stored):
    uploader = await _member(db_conn, "uploader")
    p_owner, p_member = await _member(db_conn, "p_owner"), await _member(db_conn, "p_member")
    pid = await _project(db_conn, p_owner.user_id, p_member.user_id)
    await seed.seed_doc(db_conn, DOC, uploader.user_id, file_name="filed.pdf",
                        bytes_path=stored, project_ids=[pid])
    for who in (p_owner, p_member):
        r = await _get(as_user, who)
        assert r.status_code == 200
        assert r.content == PDF
        assert _disposition(r) == ("inline", "filed.pdf")


async def test_stranger_gets_404(db_conn, as_user, stored):
    owner, stranger = await _member(db_conn, "owner"), await _member(db_conn, "stranger")
    await seed.seed_doc(db_conn, DOC, owner.user_id, bytes_path=stored)
    r = await _get(as_user, stranger)
    assert r.status_code == 404
    assert PDF not in r.content


async def test_unknown_doc_is_the_same_404(db_conn, as_user):
    someone = await _member(db_conn, "someone")
    assert (await _get(as_user, someone, "d" * 64)).status_code == 404


async def test_unverified_pre_a1_holder_gets_404(db_conn, as_user, stored):
    squatter = await _member(db_conn, "squatter")
    await seed.seed_doc(db_conn, DOC, squatter.user_id, bytes_path=stored, verified=False)
    r = await _get(as_user, squatter)
    assert r.status_code == 404
    assert PDF not in r.content


async def test_missing_bytes_is_409_bytes_missing(db_conn, as_user, tmp_path):
    owner = await _member(db_conn, "owner")
    # A legacy row with no stored bytes, and one whose file has gone from disk.
    await seed.seed_doc(db_conn, DOC, owner.user_id)
    gone = "b" * 64
    await seed.seed_doc(db_conn, gone, owner.user_id, bytes_path=tmp_path / f"{gone}.pdf")
    for doc_id in (DOC, gone):
        r = await _get(as_user, owner, doc_id)
        assert r.status_code == 409
        assert r.json()["detail"]["error"] == "bytes_missing"
        assert r.json()["detail"]["message"] == "Upload the file again first."


@pytest.mark.parametrize("file_type,name,body,mime", [
    ("markdown", "notes.md", b"# Notes\n", "text/markdown; charset=utf-8"),
    ("text", "plain.txt", b"plain words\n", "text/plain; charset=utf-8"),
])
async def test_text_types_carry_their_mime(db_conn, as_user, tmp_path, file_type, name, body, mime):
    owner = await _member(db_conn, "owner")
    path = tmp_path / f"{DOC}.bin"
    path.write_bytes(body)
    await seed.seed_doc(db_conn, DOC, owner.user_id, file_name=name, file_type=file_type,
                        bytes_path=path)
    r = await _get(as_user, owner)
    assert r.status_code == 200
    assert r.content == body
    assert r.headers["content-type"] == mime


async def test_a_name_with_quotes_or_non_ascii_cannot_break_the_header(db_conn, as_user, stored):
    owner = await _member(db_conn, "owner")
    await seed.seed_doc(db_conn, DOC, owner.user_id, file_name='bad"; x=1 ünï.pdf',
                        bytes_path=stored)
    r = await _get(as_user, owner)
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("inline; filename*=utf-8''")
    assert '"' not in r.headers["content-disposition"]
    assert _disposition(r) == ("inline", 'bad"; x=1 ünï.pdf')


async def test_requires_the_reader_capability(db_conn, as_user, stored):
    owner = await _member(db_conn, "owner")
    await seed.seed_doc(db_conn, DOC, owner.user_id, bytes_path=stored)
    no_reader = deps.Principal(user_id=owner.user_id, email=owner.email, role="member",
                               capabilities=frozenset())
    assert (await _get(as_user, no_reader)).status_code == 403
