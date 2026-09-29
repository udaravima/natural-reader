"""v2.3 Task F: exact-word matching alongside meaning. Meaning search is weak
on labels ("Table 4.2", "MIMIC-IV", "§3.1"); a 'simple' full-text index finds
them in any language, and reciprocal-rank fusion merges the two lists."""
from __future__ import annotations

import pytest

from server.chat.tools import ToolContext, available_tools, run_tool
from server.chat.tools import search_documents as sd_tool
from server.llm.types import ToolCall
from server.services import doc_search
from server.services.embeddings import EMBEDDING_DIM
from server.tests import seed
from server.tests.chat_harness import member, shim_pool

DOC = "d" * 64
QUERY_VEC = [1.0] + [0.0] * (EMBEDDING_DIM - 1)


def _vec(*head):
    return "[" + ",".join(str(x) for x in list(head) + [0] * (EMBEDDING_DIM - len(head))) + "]"


async def _chunk(conn, ord_, page, text, vec):
    cur = await conn.execute(
        "INSERT INTO doc_chunks (doc_id, ord, page, chunk_type, text, text_hash, embedding, embedding_model) "
        "VALUES (%s, %s, %s, 'page', %s, %s, %s::vector, 'm') RETURNING id", (DOC, ord_, page, text, f"h{ord_}", vec))
    return (await cur.fetchone())[0]


@pytest.fixture
async def doc(db_conn, monkeypatch):
    alice = await member(db_conn, "alice")
    await seed.seed_doc(db_conn, DOC, alice.user_id, file_name="Thesis.pdf", state="indexed")
    # Close in meaning to the query, but no label.
    await _chunk(db_conn, 0, 5, "Results were discussed at length in the chapter.", _vec(1, 0.1))
    await _chunk(db_conn, 1, 6, "Accuracy rose on every dataset we tried.", _vec(1, 0.3))
    # Far in meaning (cosine 0.2), but it has the label.
    await _chunk(db_conn, 2, 3, "Table 4.2 lists accuracy by dataset.", _vec(0.2, 1))
    # Close in meaning AND has the label: first after fusion.
    await _chunk(db_conn, 3, 8, "As Table 4.2 shows, results improved.", _vec(1, 0.2))
    return db_conn, alice


async def test_the_full_text_column_is_generated_for_every_chunk(doc):
    conn, _ = doc
    cur = await conn.execute("SELECT text_search::text FROM doc_chunks WHERE doc_id = %s AND page = 3", (DOC,))
    assert "'4.2'" in (await cur.fetchone())[0]
    cur = await conn.execute("SELECT indexdef FROM pg_indexes WHERE tablename = 'doc_chunks' "
                             "AND indexdef ILIKE '%%gin%%text_search%%'")
    assert await cur.fetchone() is not None


async def test_a_label_is_found_by_words_when_meaning_ranks_it_low(doc):
    conn, _ = doc
    vector_only = await doc_search.search_chunks(conn, DOC, QUERY_VEC, 3)
    assert 3 not in [r["page"] for r in vector_only]                       # meaning alone misses it
    rows = await doc_search.search_chunks(conn, DOC, QUERY_VEC, 3, text="Table 4.2")
    by_page = {r["page"]: r for r in rows}
    assert 3 in by_page and by_page[3]["by_words"] and by_page[3]["score"] < 0.45
    assert by_page[8]["by_words"] and not by_page[5]["by_words"]


async def test_fusion_puts_what_both_searches_found_first(doc):
    conn, _ = doc
    rows = await doc_search.search_chunks(conn, DOC, QUERY_VEC, 4, text="Table 4.2")
    assert rows[0]["page"] == 8                                            # rank 2 by meaning + rank 1-2 by words
    assert [r["page"] for r in rows][1:3] == [3, 5] or [r["page"] for r in rows][1:3] == [5, 3]
    assert all(0 <= r["score"] <= 1 for r in rows)                          # cosine, still for the floor


async def test_without_text_the_search_is_meaning_only_as_before(doc):
    conn, _ = doc
    rows = await doc_search.search_chunks(conn, DOC, QUERY_VEC, 2)
    assert [r["page"] for r in rows] == [5, 8] and not any(r["by_words"] for r in rows)


async def test_a_query_with_no_words_is_meaning_only(doc):
    conn, _ = doc
    rows = await doc_search.search_chunks(conn, DOC, QUERY_VEC, 2, text="?!")
    assert [r["page"] for r in rows] == [5, 8]


async def test_search_documents_keeps_a_words_match_below_the_floor_and_says_how_it_matched(doc, monkeypatch):
    conn, alice = doc
    shim_pool(monkeypatch, conn, sd_tool)

    async def fake_embed(text):
        return QUERY_VEC

    monkeypatch.setattr(sd_tool, "embed_query", fake_embed)
    ctx = ToolContext(alice.user_id, await doc_search.readable_doc(conn, DOC, alice.user_id))
    run = await run_tool(ToolCall("c1", "search_documents", {"query": "Table 4.2", "k": 4}), ctx,
                         available_tools(ctx))
    passages = {p["page"]: p for p in run.result["passages"]}
    assert passages[3]["match"] == "words" and passages[3]["relevance"] == "weak"   # cosine 0.2 < 0.45
    assert passages[8]["match"] == "both"
    assert "match" not in passages[5]                                                # meaning only: the default


def test_the_description_says_it_matches_exact_words_too():
    d = sd_tool.TOOL.spec.description
    assert "exact words" in d and "by meaning" in d
