"""C2.1 Task 6: the stored outline of a document (doc_outlines, migration 021):
save, replace, load."""
from __future__ import annotations

from server.services.doc_outlines import Outline, load_outlines, save_outline
from server.services.extract import OutlineEntry
from server.tests import seed
from server.tests.test_doc_pipeline import _member

A, B = "a" * 64, "b" * 64


async def _docs(db_conn):
    owner = await _member(db_conn, "outline-owner")
    for doc_id in (A, B):
        await seed.seed_doc(db_conn, doc_id, owner.user_id, file_type="markdown", state="indexed")


async def test_an_outline_round_trips(db_conn):
    await _docs(db_conn)
    outline = Outline((OutlineEntry(1, "Guide", 1), OutlineEntry(2, "Install", 2)), "Guide to the thing.",
                      "headings")
    await save_outline(db_conn, A, outline)
    assert await load_outlines(db_conn, [A]) == {A: outline}


async def test_a_second_save_replaces_the_first(db_conn):
    await _docs(db_conn)
    await save_outline(db_conn, A, Outline((OutlineEntry(1, "Old", 1),), "old opening", "headings"))
    newer = Outline((), "Just prose.", "none")
    await save_outline(db_conn, A, newer)
    assert await load_outlines(db_conn, [A]) == {A: newer}
    cur = await db_conn.execute("SELECT count(*) FROM doc_outlines WHERE doc_id = %s", (A,))
    assert (await cur.fetchone())[0] == 1


async def test_unknown_ids_are_absent(db_conn):
    await _docs(db_conn)
    await save_outline(db_conn, A, Outline((), "x", "none"))
    loaded = await load_outlines(db_conn, [A, B, "c" * 64])
    assert list(loaded) == [A]
    assert await load_outlines(db_conn, []) == {}
