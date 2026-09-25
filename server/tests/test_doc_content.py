from server.services import doc_content
from server.tests.seed import place_doc, seed_doc, share_doc

D = "d" * 64


async def _user(conn, sub):
    from server.auth.users import resolve_or_provision_user
    return (await resolve_or_provision_user(conn, iss="i", sub=sub, email=f"{sub}@x.io"))["id"]


async def _project(conn, owner):
    cur = await conn.execute(
        "INSERT INTO projects (owner_user_id, name) VALUES (%s,'P') RETURNING id", (owner,))
    return str((await cur.fetchone())[0])


async def _via(conn, user, doc=D):
    cur = await conn.execute(
        "SELECT added_via FROM library_entries WHERE user_id=%s AND doc_id=%s", (user, doc))
    row = await cur.fetchone()
    return row[0] if row else None


async def test_add_entry_created_exists_upgraded(db_conn):
    a, b = await _user(db_conn, "a"), await _user(db_conn, "b")
    await seed_doc(db_conn, D, a)
    assert await doc_content.add_entry(db_conn, a, D, via="upload") == "exists"
    assert await doc_content.add_entry(db_conn, b, D, via="shared", shared_by=a) == "created"
    assert await doc_content.add_entry(db_conn, b, D, via="upload") == "upgraded"
    assert await _via(db_conn, b) == "upload"


async def test_share_never_downgrades_an_upload_entry(db_conn):
    a, b = await _user(db_conn, "a"), await _user(db_conn, "b")
    await seed_doc(db_conn, D, a)
    await doc_content.add_entry(db_conn, b, D, via="upload")
    assert await doc_content.add_entry(db_conn, b, D, via="shared", shared_by=a) == "exists"
    assert await _via(db_conn, b) == "upload"


async def test_revoke_only_removes_my_share(db_conn):
    a, b, c = [await _user(db_conn, s) for s in "abc"]
    await seed_doc(db_conn, D, a)
    await doc_content.add_entry(db_conn, b, D, via="upload")   # b uploaded it too
    await share_doc(db_conn, D, b, c)                          # b shared with c
    assert await doc_content.revoke_share(db_conn, a, c, D) is False  # not a's share
    assert await doc_content.revoke_share(db_conn, a, b, D) is False  # b's upload entry
    assert await doc_content.revoke_share(db_conn, b, c, D) is True


async def test_content_ops_refusal(db_conn):
    a, b = await _user(db_conn, "a"), await _user(db_conn, "b")
    await seed_doc(db_conn, D, a)
    assert await doc_content.content_ops_refusal(db_conn, D, a, is_admin=False) is None
    p = await _project(db_conn, a)
    await place_doc(db_conn, p, D, a)
    assert await doc_content.content_ops_refusal(db_conn, D, a, is_admin=False) == "in_project"
    await share_doc(db_conn, D, a, b)
    assert await doc_content.content_ops_refusal(db_conn, D, a, is_admin=False) == "other_holders"
    assert await doc_content.content_ops_refusal(db_conn, D, b, is_admin=False) == "other_holders"
    assert await doc_content.content_ops_refusal(db_conn, D, b, is_admin=True) is None


async def test_gc_removes_only_orphans_and_their_bytes(db_conn, tmp_path):
    a = await _user(db_conn, "a")
    f = tmp_path / f"{D}.pdf"
    f.write_bytes(b"%PDF-1.4")
    await seed_doc(db_conn, D, a, bytes_path=f)
    await db_conn.execute("INSERT INTO doc_chunks (doc_id, ord, text, text_hash) VALUES (%s,0,'t','h')", (D,))
    assert await doc_content.gc_content_if_orphaned(db_conn, D, trigger="test") is False
    await doc_content.remove_entry(db_conn, a, D)
    assert await doc_content.gc_content_if_orphaned(db_conn, D, trigger="test") is True
    cur = await db_conn.execute("SELECT count(*) FROM doc_chunks WHERE doc_id=%s", (D,))
    assert (await cur.fetchone())[0] == 0 and not f.exists()


async def test_gc_keeps_content_placed_in_a_project(db_conn):
    a = await _user(db_conn, "a")
    await seed_doc(db_conn, D, None, project_ids=[await _project(db_conn, a)])
    assert await doc_content.gc_content_if_orphaned(db_conn, D, trigger="test") is False


async def test_docs_referenced_by_user_includes_owned_project_placements(db_conn):
    a, b = await _user(db_conn, "a"), await _user(db_conn, "b")
    await seed_doc(db_conn, D, a)
    p = await _project(db_conn, a)
    await seed_doc(db_conn, "e" * 64, b, project_ids=[p])
    assert set(await doc_content.docs_referenced_by_user(db_conn, a)) == {D, "e" * 64}
