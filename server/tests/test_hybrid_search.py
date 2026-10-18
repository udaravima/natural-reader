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


async def _chunk_in(conn, doc_id, ord_, page, text, vec):
    cur = await conn.execute(
        "INSERT INTO doc_chunks (doc_id, ord, page, chunk_type, text, text_hash, embedding, embedding_model) "
        "VALUES (%s, %s, %s, 'page', %s, %s, %s::vector, 'm') RETURNING id",
        (doc_id, ord_, page, text, f"h{ord_}", vec))
    return (await cur.fetchone())[0]


async def _chunk(conn, ord_, page, text, vec):
    return await _chunk_in(conn, DOC, ord_, page, text, vec)


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
    assert [r["page"] for r in rows][:3] == [8, 3, 5]    # 8: meaning 2 + words 2; 3: meaning 4 + words 1; 5: meaning 1 only
    assert all(0 <= r["score"] <= 1 for r in rows)                          # cosine, still for the floor


async def test_without_text_the_search_is_meaning_only_as_before(doc):
    conn, _ = doc
    rows = await doc_search.search_chunks(conn, DOC, QUERY_VEC, 2)
    assert [r["page"] for r in rows] == [5, 8]
    assert all("by_words" not in r for r in rows)          # the meaning-only shape, as before (/search)


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
    assert passages[3]["match"] == "words"                                            # cosine 0.2 < 0.45
    assert passages[8]["match"] == "both"
    assert "match" not in passages[5]                                                # meaning only: the default


def test_the_description_says_it_matches_exact_words_too():
    """Review I1: labels are worth searching for by themselves. v2.4: and the
    model is told why — the exact-word half needs every word of the query."""
    d = sd_tool.TOOL.spec.description
    assert "By exact words" in d and "By meaning" in d
    assert "name, label or number" in d and "every word of the query" in d
    assert "already searched" not in d                      # false whenever prefetch didn't run


async def test_only_the_best_word_matches_skip_the_floor(doc, monkeypatch):
    """Review minor: a common-word query matches many chunks by words; only
    the top word ranks may bypass the floor, or weak passages crowd out
    meaning ones."""
    conn, alice = doc
    for i in range(6):
        await _chunk(conn, 10 + i, 20 + i, f"The method is described here, part {i}.", _vec(0.1, 1))
    shim_pool(monkeypatch, conn, sd_tool)

    async def fake_embed(text):
        return QUERY_VEC

    monkeypatch.setattr(sd_tool, "embed_query", fake_embed)
    ctx = ToolContext(alice.user_id, await doc_search.readable_doc(conn, DOC, alice.user_id))
    run = await run_tool(ToolCall("c1", "search_documents", {"query": "method", "k": 10}), ctx,
                         available_tools(ctx))
    words_only = [p for p in run.result["passages"] if p.get("match") == "words"]
    assert len(words_only) == sd_tool.WORD_BYPASS_RANKS


async def test_migration_015_fills_rows_that_existed_before_it(doc):
    """Review minor: the column must be computed for existing chunks, and the
    migration must be safe to run twice."""
    from pathlib import Path
    conn, _ = doc
    await conn.execute("DROP INDEX doc_chunks_text_search_idx")
    await conn.execute("ALTER TABLE doc_chunks DROP COLUMN text_search")
    sql = (Path(doc_search.__file__).parents[1] / "sql" / "015_chunk_text_search.sql").read_text()
    await conn.execute(sql)
    await conn.execute(sql)
    cur = await conn.execute("SELECT count(*) FROM doc_chunks WHERE doc_id = %s AND text_search IS NULL", (DOC,))
    assert (await cur.fetchone())[0] == 0
    rows = await doc_search.search_chunks(conn, DOC, QUERY_VEC, 3, text="Table 4.2")
    assert 3 in [r["page"] for r in rows]


# ---- C2.1 Task 7: one ranking across a project, balanced, partial word matches ----

DOC_A, DOC_B = "a" * 64, "b" * 64


@pytest.fixture
async def two_docs(db_conn):
    """Two empty indexed documents the tests fill themselves."""
    alice = await member(db_conn, "alice")
    for d in (DOC_A, DOC_B):
        await seed.seed_doc(db_conn, d, alice.user_id, file_name=f"{d[0]}.md", state="indexed")
    return db_conn


async def test_scope_search_keeps_the_word_match_across_documents(two_docs):
    """The old project path ran one search per document and sorted by cosine, which dropped the
    word ranking: B's three closer passages crowded out A's exact "clause 4.2"."""
    conn = two_docs
    await _chunk_in(conn, DOC_A, 0, 7, "The parties agree under clause 4.2 to arbitrate.", _vec(0.2, 1))
    for i in range(3):
        await _chunk_in(conn, DOC_B, i, 1 + i, f"General remarks, part {i}.", _vec(1, 0.1 * (i + 1)))
    by_cosine = sorted(await doc_search.search_scope(conn, [DOC_A, DOC_B], QUERY_VEC, 4), key=lambda r: -r["score"])
    assert by_cosine[-1]["doc_id"] == DOC_A                     # meaning alone puts A's passage last
    rows = await doc_search.search_scope(conn, [DOC_A, DOC_B], QUERY_VEC, 3, text="clause 4.2")
    assert len(rows) == 3
    assert (rows[0]["doc_id"], rows[0]["page"]) == (DOC_A, 7)    # meaning rank 4 + words rank 1 beats meaning only
    assert rows[0]["by_words"] and rows[0]["word_rank"] == 1
    assert all(not r["by_words"] for r in rows[1:])


async def test_balance_caps_each_document_then_fills(two_docs):
    conn = two_docs
    for i in range(5):
        await _chunk_in(conn, DOC_A, i, 1 + i, f"Good passage {i}.", _vec(1, 0.1 * (i + 1)))
    await _chunk_in(conn, DOC_B, 0, 1, "A weaker one.", _vec(1, 3))
    rows = await doc_search.search_scope(conn, [DOC_A, DOC_B], QUERY_VEC, 4, per_doc=2)
    assert [(r["doc_id"], r["page"]) for r in rows] == [(DOC_A, 1), (DOC_A, 2), (DOC_B, 1), (DOC_A, 3)]
    unbalanced = await doc_search.search_scope(conn, [DOC_A, DOC_B], QUERY_VEC, 4)
    assert [r["doc_id"] for r in unbalanced] == [DOC_A] * 4      # without a cap, A takes every slot


async def test_balance_works_with_words_too(two_docs):
    conn = two_docs
    for i in range(4):
        await _chunk_in(conn, DOC_A, i, 1 + i, f"The widget manual, section {i}.", _vec(1, 0.1 * (i + 1)))
    await _chunk_in(conn, DOC_B, 0, 1, "Widget notes from the other project.", _vec(1, 2))
    rows = await doc_search.search_scope(conn, [DOC_A, DOC_B], QUERY_VEC, 3, text="widget", per_doc=2)
    assert [r["doc_id"] for r in rows] == [DOC_A, DOC_A, DOC_B]


async def test_one_document_still_fills_the_list_when_alone(two_docs):
    conn = two_docs
    for i in range(5):
        await _chunk_in(conn, DOC_A, i, 1 + i, f"Passage {i}.", _vec(1, 0.1 * (i + 1)))
    rows = await doc_search.search_scope(conn, [DOC_A], QUERY_VEC, 4, per_doc=2)
    assert [r["page"] for r in rows] == [1, 2, 3, 4]             # the cap only reorders; nothing else to take


async def test_no_documents_is_no_rows(two_docs):
    assert await doc_search.search_scope(two_docs, [], QUERY_VEC, 3) == []
    assert await doc_search.search_scope(two_docs, [], QUERY_VEC, 3, text="anything") == []


async def test_scope_search_reads_only_the_documents_named(two_docs):
    conn = two_docs
    await _chunk_in(conn, DOC_A, 0, 1, "Alpha podman note.", _vec(1, 0.1))
    await _chunk_in(conn, DOC_B, 0, 1, "Beta podman note.", _vec(1, 0.05))
    rows = await doc_search.search_scope(conn, [DOC_A], QUERY_VEC, 5, text="podman")
    assert [r["doc_id"] for r in rows] == [DOC_A]


@pytest.fixture
async def install_doc(two_docs):
    conn = two_docs
    await _chunk_in(conn, DOC_A, 0, 1, "To install podman rootless, enable user namespaces first.", _vec(0.3, 1))
    await _chunk_in(conn, DOC_A, 1, 2, "Only RHEL is mentioned in this paragraph.", _vec(0.2, 1))
    await _chunk_in(conn, DOC_A, 2, 3, "Cats sleep most of the day.", _vec(1, 0.1))
    return conn


async def test_partial_words_find_a_chunk_missing_one_word(install_doc):
    conn = install_doc
    rows = await doc_search.search_scope(conn, [DOC_A], QUERY_VEC, 5, text="install podman rootless rhel")
    by_page = {r["page"]: r for r in rows}
    assert by_page[1]["by_words"] and by_page[1]["word_rank"] == 1      # 3 of 4 words; no chunk has all 4
    rows = await doc_search.search_chunks(conn, DOC_A, QUERY_VEC, 5, text="install podman rootless rhel")
    assert {r["page"]: r["by_words"] for r in rows}[1] is True          # the single-document call too


async def test_partial_words_need_half_the_words(install_doc):
    conn = install_doc
    rows = await doc_search.search_scope(conn, [DOC_A], QUERY_VEC, 5, text="install podman rootless rhel")
    by_page = {r["page"]: r for r in rows}
    assert by_page[2]["by_words"] is False and by_page[2]["word_rank"] is None   # "rhel" alone is 1 of 4
    assert by_page[3]["by_words"] is False


async def test_half_the_words_rounds_up(install_doc):
    """3 words need 2, not 1: "rhel" alone is not enough, but two of the three are."""
    rows = await doc_search.search_scope(install_doc, [DOC_A], QUERY_VEC, 5, text="podman rootless rhel")
    by_page = {r["page"]: r for r in rows}
    assert by_page[1]["by_words"] and not by_page[2]["by_words"]


async def test_stop_words_do_not_count_toward_partial_matching(two_docs):
    """"what is the clause 4.2": unrelated "what is the ..." chunks used to tie with the real one at 3 of 5
    words, and the tool's word-rank floor bypass could show them as word matches."""
    conn = two_docs
    for i in range(6):
        await _chunk_in(conn, DOC_A, i, 10 + i, f"What is the plan for item {i} of the list?", _vec(1, 0.1 * (i + 1)))
    await _chunk_in(conn, DOC_B, 0, 1, "What is the weather like today in the city?", _vec(1, 0.05))
    await _chunk_in(conn, DOC_B, 1, 2, "Clause 4.2 limits the liability of both parties.", _vec(0.1, 1))
    rows = await doc_search.search_scope(conn, [DOC_A, DOC_B], QUERY_VEC, 8, text="what is the clause 4.2")
    assert len(rows) == 8
    word_matched = [(r["doc_id"], r["page"], r["word_match"]) for r in rows if r["by_words"]]
    assert word_matched == [(DOC_B, 2, "partial")]                  # only the chunk with clause AND 4.2
    assert all(r["word_rank"] is None and r["word_match"] is None for r in rows if not r["by_words"])


async def test_a_query_of_only_common_words_has_no_partial_rows(two_docs):
    """Each word is in 3 of 5 chunks, no chunk has all three: nothing is informative, so no word list."""
    conn = two_docs
    for i, text in enumerate(["what the cats", "what dogs", "what is birds", "is the fish", "is the cows"]):
        await _chunk_in(conn, DOC_A, i, 1 + i, f"{text} sleep.", _vec(1, 0.1 * (i + 1)))
    rows = await doc_search.search_scope(conn, [DOC_A], QUERY_VEC, 5, text="what is the")
    assert len(rows) == 5
    assert not any(r["by_words"] for r in rows) and all(r["word_match"] is None for r in rows)


async def test_common_words_still_match_when_one_chunk_has_them_all(two_docs):
    conn = two_docs
    for i in range(3):
        await _chunk_in(conn, DOC_A, i, 1 + i, f"What is the plan {i}?", _vec(1, 0.1 * (i + 1)))
    await _chunk_in(conn, DOC_A, 3, 4, "Unrelated words entirely.", _vec(1, 0.5))
    rows = await doc_search.search_scope(conn, [DOC_A], QUERY_VEC, 4, text="what is the")
    assert {r["page"]: r["word_match"] for r in rows} == {1: "all", 2: "all", 3: "all", 4: None}


async def test_word_match_says_which_word_list_found_a_row(doc, install_doc):
    conn, _ = doc
    rows = await doc_search.search_chunks(conn, DOC, QUERY_VEC, 4, text="Table 4.2")
    assert {r["page"]: r["word_match"] for r in rows} == {8: "all", 3: "all", 5: None, 6: None}
    assert all(r["by_words"] == (r["word_match"] is not None) for r in rows)
    rows = await doc_search.search_scope(conn, [DOC_A], QUERY_VEC, 5, text="install podman rootless rhel")
    assert {r["page"]: (r["by_words"], r["word_match"]) for r in rows} == {
        1: (True, "partial"), 2: (False, None), 3: (False, None)}
    rows = await doc_search.search_scope(conn, [DOC_A], QUERY_VEC, 5)
    assert all("by_words" not in r and "word_match" not in r for r in rows)   # meaning-only keeps its shape


async def test_only_a_match_of_every_word_skips_the_floor_and_a_partial_one_is_labelled(two_docs, monkeypatch):
    """C2.1 Task 8, through the real search (no stand-in): the cosine floor (0.45) may be skipped by the best
    matches of EVERY word of the query, not by a partial match. A partial match that passes the floor says
    "some words"; a weak one is dropped like any other weak row."""
    conn = two_docs
    alice = await member(conn, "alice")
    await _chunk_in(conn, DOC_A, 0, 1, "To install podman rootless, enable user namespaces first.", _vec(1, 0.5))   # cosine 0.89
    await _chunk_in(conn, DOC_A, 1, 2, "Install it with the rhel installer.", _vec(0.1, 1))                       # 0.10
    await _chunk_in(conn, DOC_A, 2, 3, "Only RHEL is mentioned in this paragraph.", _vec(0.2, 1))
    await _chunk_in(conn, DOC_A, 3, 4, "Install podman on a machine of any kind.", _vec(0.1, 1))                  # 0.10
    for i in range(7):
        await _chunk_in(conn, DOC_A, 4 + i, 5 + i, f"Cats sleep most of the day, entry {i}.", _vec(1, 0.1))
    shim_pool(monkeypatch, conn, sd_tool)

    async def fake_embed(text):
        return QUERY_VEC

    monkeypatch.setattr(sd_tool, "embed_query", fake_embed)

    async def search(query):
        ctx = ToolContext(alice.user_id, await doc_search.readable_doc(conn, DOC_A, alice.user_id))
        run = await run_tool(ToolCall("c1", "search_documents", {"query": query, "k": 10}), ctx, available_tools(ctx))
        return {p["page"]: p.get("match") for p in run.result["passages"]}

    # Pages 1 and 4 have both words: the weak one (page 4) keeps its place, labelled "words".
    by_page = await search("install podman")
    assert by_page[1] == "both" and by_page[4] == "words"
    # No chunk has all four words: pages 1 (3 words), 2 and 4 (2 words each) match partly. Page 1 passes the
    # floor; pages 2 and 4 are word ranks 2 and 3, which used to skip it, and are dropped now.
    by_page = await search("install podman rootless rhel")
    assert by_page[1] == "some words"
    assert 2 not in by_page and 4 not in by_page and 3 not in by_page
    assert {page for page, match in by_page.items() if match is None} == {5, 6, 7, 8, 9, 10, 11}   # meaning only


async def _sentence_like_project(conn):
    """Three chunks the meaning ranking likes best (pages 1-3, nothing in them for the query's words), 25 fillers
    in between, and three chunks (pages 10-12, filed FIRST so their ids are the lower ones) that are far in
    meaning but hold two of the query's rare words, so they are partial word matches."""
    for i in range(3):
        await _chunk_in(conn, DOC_A, i, 10 + i, f"Rotating the signing schedule for staff, item {i}.", _vec(0.1, 1))
    for i in range(3):
        await _chunk_in(conn, DOC_B, i, 1 + i, f"Notes on cryptographic hygiene, part {i}.", _vec(1, 0.05 * (i + 1)))
    for i in range(25):
        await _chunk_in(conn, DOC_B, 10 + i, 20 + i, f"What is the plan for item {i} of the list?", _vec(1, 0.3 + 0.02 * i))


SENTENCE = "what is the procedure for rotating signing keys"


async def test_partial_word_matches_weigh_little_against_meaning(two_docs):
    """C2.1 Task 8, from the eval ("next piece of work": the right chunk was 3rd by meaning and fell out of the
    top 5 behind chunks that held only some of the words). A partial match is weak evidence for a sentence-like
    query, so it counts a tenth in the fusion (PARTIAL_WEIGHT): the best by meaning lead and a words-only
    partial match comes after them. At full weight a partial match at word rank 1 tied the meaning-best chunk
    and, being older (lower id), won the top slot."""
    conn = two_docs
    await _sentence_like_project(conn)
    rows = await doc_search.search_scope(conn, [DOC_A, DOC_B], QUERY_VEC, 5, text=SENTENCE)
    assert [r["page"] for r in rows[:3]] == [1, 2, 3]
    assert not any(r["by_words"] for r in rows)
    assert doc_search.PARTIAL_WEIGHT == 0.1


async def test_a_partial_row_also_in_the_meaning_list_does_not_outrank_a_much_better_meaning_row(
        two_docs, monkeypatch):
    """The eval's real failure: chunks with SOME of the words that were also in the meaning list (about 10th)
    took the first places from the chunk that is best by meaning, because their word-list term (0.5/61 about
    0.008) was worth more than the whole gap between meaning ranks 1 and 20. At a tenth, a partial row at word
    rank 1 needs to be within about the 8 best by meaning to lead; the 10th best no longer does."""
    conn = two_docs
    await _sentence_like_project(conn)
    # Meaning rank 10 (between fillers 6 and 7, y = 0.42); three of the rare words, so word rank 1.
    await _chunk_in(conn, DOC_A, 20, 30, "Rotating the signing keys is covered in the key custody chapter.", _vec(1, 0.42))
    rows = await doc_search.search_scope(conn, [DOC_A, DOC_B], QUERY_VEC, 5, text=SENTENCE)
    assert rows[0]["page"] == 1 and not rows[0]["by_words"]                   # the meaning-best chunk
    assert 30 in [r["page"] for r in rows]                                     # the partial row is not lost: 3rd or so
    monkeypatch.setattr(doc_search, "PARTIAL_WEIGHT", 0.5)
    at_half = await doc_search.search_scope(conn, [DOC_A, DOC_B], QUERY_VEC, 5, text=SENTENCE)
    assert at_half[0]["page"] == 30                                            # what the half weight did


async def test_a_partial_match_that_is_also_close_in_meaning_still_leads(two_docs):
    """Found by both lists is strong evidence: close in meaning (3rd) AND a partial match leads even at a tenth."""
    conn = two_docs
    await _sentence_like_project(conn)
    await _chunk_in(conn, DOC_A, 20, 30, "Rotating the signing keys is covered in the key custody chapter.", _vec(1, 0.12))
    rows = await doc_search.search_scope(conn, [DOC_A, DOC_B], QUERY_VEC, 3, text=SENTENCE)
    assert (rows[0]["page"], rows[0]["word_match"]) == (30, "partial")      # meaning rank 3 and word rank 1


async def test_only_the_rarest_words_count_and_a_long_query_still_works(two_docs):
    """14 informative words: the 12 rarest (a01..a12, equally rare, so by name) count, and half of 12 is 6."""
    conn = two_docs
    names = [f"a{i:02d}" for i in range(1, 15)]
    await _chunk_in(conn, DOC_A, 0, 1, "alpha " + " ".join(names[:6]), _vec(1, 0.1))        # 6 of the 12: found
    await _chunk_in(conn, DOC_A, 1, 2, "beta " + " ".join(names[8:14]), _vec(1, 0.2))        # only 4 of the 12
    await _chunk_in(conn, DOC_A, 2, 3, "gamma a07 a08", _vec(1, 0.3))
    await _chunk_in(conn, DOC_A, 3, 4, "zeta eta theta", _vec(1, 0.4))
    rows = await doc_search.search_scope(conn, [DOC_A], QUERY_VEC, 4, text=" ".join(names))   # without the cap: 6 of 14
    assert {r["page"]: r["word_match"] for r in rows} == {1: "partial", 2: None, 3: None, 4: None}
    long_query = " ".join(f"w{i:02d}" for i in range(60)) + " alpha beta"
    rows = await doc_search.search_scope(conn, [DOC_A], QUERY_VEC, 4, text=long_query)
    assert len(rows) == 4
    assert {r["page"]: r["word_match"] for r in rows} == {1: "partial", 2: "partial", 3: None, 4: None}
    # the 60 words nobody has did not take the 12 places: alpha and beta (one chunk each) still counted


async def test_partial_words_with_equal_counts_are_ordered_by_text_rank(two_docs):
    """Three of four words in both chunks; ts_rank_cd gives more to the one that says them more often, even
    though the other was inserted first (so id order would put it first)."""
    conn = two_docs
    await _chunk_in(conn, DOC_A, 0, 1, "alpha beta gamma", _vec(1, 0.1))
    await _chunk_in(conn, DOC_A, 1, 2, "alpha beta gamma alpha beta gamma alpha beta gamma", _vec(1, 0.2))
    for i in range(4):
        await _chunk_in(conn, DOC_A, 2 + i, 3 + i, f"unrelated filler {i}", _vec(1, 0.3 + i))
    rows = await doc_search.search_scope(conn, [DOC_A], QUERY_VEC, 6, text="alpha beta gamma delta")
    assert {r["page"]: r["word_rank"] for r in rows if r["by_words"]} == {2: 1, 1: 2}


async def test_partial_word_ties_do_not_favour_the_oldest_documents(two_docs):
    """C2.1 Task 8: 100 identical chunks in each of two documents all match 3 of 4 words equally, and the word
    list takes 60 of them. Taking ties in id order gave every place to the document indexed first (its ids are
    the lower ones); a hash of the id mixes them. At a tenth of the weight a words-only partial row sits
    behind about 550 meaning rows, so the chunks here are also close in meaning: the 60 best by meaning (30
    from each document, their vectors interleaved) are returned, and which of THEM the word list picked is
    what shows (by_words)."""
    conn = two_docs
    for n, doc_id in enumerate((DOC_A, DOC_B)):
        for i in range(100):
            await _chunk_in(conn, doc_id, i, 1 + i, "alpha beta gamma", _vec(1, 0.002 * i + 0.001 * n))
    for i in range(220):
        await _chunk_in(conn, DOC_A, 1000 + i, 1 + i % 50, f"unrelated filler {i}", _vec(0.1, 1))
    rows = await doc_search.search_scope(conn, [DOC_A, DOC_B], QUERY_VEC, 60, text="alpha beta gamma delta")
    assert {r["doc_id"] for r in rows} == {DOC_A, DOC_B}
    by_words = [r for r in rows if r["by_words"]]
    assert len(by_words) >= 8 and all(r["word_match"] == "partial" for r in by_words)
    assert {r["doc_id"] for r in by_words} == {DOC_A, DOC_B}


async def test_a_query_counts_at_most_64_distinct_words(two_docs):
    """C2.1 Task 8: every distinct word costs a frequency lookup (200 words took over 300 ms), so only the first
    64 count. 63 words nobody has, then alpha (64th: counts) and beta (65th: cut)."""
    conn = two_docs
    await _chunk_in(conn, DOC_A, 0, 1, "alpha only here", _vec(1, 0.1))
    await _chunk_in(conn, DOC_A, 1, 2, "beta only here", _vec(1, 0.2))
    for i in range(4):
        await _chunk_in(conn, DOC_A, 2 + i, 3 + i, f"plain filler {i}", _vec(1, 0.3 + i))
    nobody = " ".join(f"zz{i:02d}" for i in range(63))
    rows = await doc_search.search_scope(conn, [DOC_A], QUERY_VEC, 6, text=f"{nobody} alpha beta")
    assert {r["page"]: r["word_match"] for r in rows}[1] == "partial"      # alpha was the 64th word
    assert {r["page"]: r["word_match"] for r in rows}[2] is None            # beta was the 65th: never counted
    # one word earlier and neither counts: 64 words nobody has, then alpha
    nobody = " ".join(f"zz{i:02d}" for i in range(64))
    rows = await doc_search.search_scope(conn, [DOC_A], QUERY_VEC, 6, text=f"{nobody} alpha beta")
    assert not any(r["by_words"] for r in rows)


async def test_common_words_are_spotted_the_same_way_in_a_large_scope(two_docs):
    """Over about 2000 chunks the check for words in over half of them starts from a sample; the answer is the
    same: "common" (all chunks) and "often" (55%, a hair above half, so only the exact count can tell) are out,
    and only "raretoken" (one chunk) counts."""
    conn = two_docs
    await conn.execute(
        "INSERT INTO doc_chunks (doc_id, ord, page, chunk_type, text, text_hash, embedding, embedding_model) "
        "SELECT %s, i, 1 + i %% 50, 'page', 'common filler ' || CASE WHEN i %% 20 < 11 THEN 'often ' ELSE '' END "
        "|| CASE WHEN i = 7 THEN 'raretoken' ELSE '' END, 'g' || i, %s::vector, 'm' FROM generate_series(1, 2100) AS i",
        (DOC_A, _vec(1, 0.5)))
    rows = await doc_search.search_scope(conn, [DOC_A], QUERY_VEC, 8, text="common often raretoken nosuchword")
    word_matched = [r for r in rows if r["by_words"]]
    assert len(word_matched) == 1 and "raretoken" in word_matched[0]["text"] and word_matched[0]["word_match"] == "partial"


async def test_a_word_nobody_has_is_ignored_not_counted(install_doc):
    """"zzqx" is in no chunk: it can't tell chunks apart, so it neither raises the bar (3 words would need 2)
    nor takes one of the 12 places."""
    rows = await doc_search.search_scope(install_doc, [DOC_A], QUERY_VEC, 5, text="install zzqx qqzz")
    assert {r["page"]: r["word_match"] for r in rows} == {1: "partial", 2: None, 3: None}


async def test_partial_words_rank_by_matched_count_then_text_rank(install_doc):
    conn = install_doc
    await _chunk_in(conn, DOC_A, 3, 4, "Install it with the rhel installer.", _vec(0.1, 1))   # 2 of 4: install, rhel
    rows = await doc_search.search_scope(conn, [DOC_A], QUERY_VEC, 5, text="install podman rootless rhel")
    ranks = {r["page"]: r["word_rank"] for r in rows if r["by_words"]}
    assert ranks == {1: 1, 4: 2}                                       # 3 matched words, then 2


async def test_partial_words_are_not_used_when_a_chunk_has_every_word(install_doc):
    conn = install_doc
    await _chunk_in(conn, DOC_A, 3, 4, "Install podman rootless on RHEL 9.", _vec(0.1, 1))
    rows = await doc_search.search_scope(conn, [DOC_A], QUERY_VEC, 5, text="install podman rootless rhel")
    assert {r["page"] for r in rows if r["by_words"]} == {4}           # the all-words list only


async def test_partial_words_look_across_the_whole_scope(two_docs):
    """No single chunk has every word, though the two documents together do: partial matching."""
    conn = two_docs
    await _chunk_in(conn, DOC_A, 0, 1, "Install podman with the package manager.", _vec(0.2, 1))
    await _chunk_in(conn, DOC_B, 0, 1, "Rootless RHEL hosts need subuid ranges.", _vec(0.3, 1))
    rows = await doc_search.search_scope(conn, [DOC_A, DOC_B], QUERY_VEC, 5, text="install podman rootless rhel")
    assert {(r["doc_id"], r["by_words"]) for r in rows} == {(DOC_A, True), (DOC_B, True)}


async def test_a_single_word_query_has_no_partial_form(install_doc):
    rows = await doc_search.search_scope(install_doc, [DOC_A], QUERY_VEC, 5, text="kubernetes")
    assert all(not r["by_words"] for r in rows) and rows


async def test_unspaced_script_query_does_not_crash(two_docs):
    conn = two_docs
    await _chunk_in(conn, DOC_A, 0, 1, "A plain English paragraph about nothing in particular.", _vec(1, 0.1))
    rows = await doc_search.search_scope(conn, [DOC_A], QUERY_VEC, 3, text="日本語の文書")
    assert len(rows) == 1                                                # meaning still answers
    rows = await doc_search.search_chunks(conn, DOC_A, QUERY_VEC, 3, text="日本語の文書 を 検索")
    assert len(rows) == 1


async def test_a_lexeme_with_quotes_and_operators_does_not_break_the_query(install_doc):
    conn = install_doc
    rows = await doc_search.search_scope(conn, [DOC_A], QUERY_VEC, 3, text="don't \\ & | ! ( ) :* 'rhel' x:y")
    assert rows


async def test_single_document_results_unchanged(doc):
    """search_chunks keeps its row shape and order: no doc_id (the /search response is these rows),
    by_words/word_rank only with text."""
    conn, _ = doc
    rows = await doc_search.search_chunks(conn, DOC, QUERY_VEC, 4, text="Table 4.2")
    assert [(r["page"], r["by_words"], r["word_rank"]) for r in rows] == [
        (8, True, 2), (3, True, 1), (5, False, None), (6, False, None)]
    assert set(rows[0]) == {"id", "page", "chunk_type", "text", "score", "by_words", "word_rank",
                            "word_match"}                                  # word_match is new; the rest is as before
    assert [r["word_match"] for r in rows] == ["all", "all", None, None]
    plain = await doc_search.search_chunks(conn, DOC, QUERY_VEC, 4)
    assert [r["page"] for r in plain] == [5, 8, 6, 3]
    assert set(plain[0]) == {"id", "page", "chunk_type", "text", "score"}


def test_the_balance_walk_alone():
    rows = [{"id": i, "doc_id": d} for i, d in enumerate("aaabab")]
    assert [r["id"] for r in doc_search._balance(rows, k=4, per_doc=2)] == [0, 1, 3, 5]
    assert [r["id"] for r in doc_search._balance(rows, k=3, per_doc=1)] == [0, 3, 1]
    assert [r["id"] for r in doc_search._balance(rows, k=9, per_doc=1)] == [0, 3, 1, 2, 4, 5]
    assert [r["id"] for r in doc_search._balance(rows, k=2, per_doc=None)] == [0, 1]


def test_the_project_search_knobs():
    from server.chat.config import ChatConfig, load_chat_config
    assert (ChatConfig().project_fusion_depth, ChatConfig().search_per_doc) == (50, 2)
    cfg = load_chat_config({"CHAT_PROJECT_FUSION_DEPTH": "120", "CHAT_SEARCH_PER_DOC": "3"})
    assert (cfg.project_fusion_depth, cfg.search_per_doc) == (120, 3)
    for bad in ("0", "501", "x"):
        assert load_chat_config({"CHAT_PROJECT_FUSION_DEPTH": bad}).project_fusion_depth == 50
    for bad in ("0", "11", "x"):
        assert load_chat_config({"CHAT_SEARCH_PER_DOC": bad}).search_per_doc == 2
    assert load_chat_config({"CHAT_PROJECT_FUSION_DEPTH": "500", "CHAT_SEARCH_PER_DOC": "10"}).search_per_doc == 10


def _bench_script():
    """scripts/bench_project_search.py as a module (it is not on the import path)."""
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "scripts" / "bench_project_search.py"
    spec = importlib.util.spec_from_file_location("bench_project_search_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_benchmark_refuses_the_real_database_and_a_missing_one(capsys):
    script = _bench_script()
    for url in ("postgresql://u:p@localhost:5433/natural_reader", "postgresql://u:p@localhost:5433/", None, ""):
        with pytest.raises(SystemExit) as e:
            script.check_database_url(url)
        assert e.value.code == 2
    assert "p@localhost" not in capsys.readouterr().err                       # the password never reaches the screen
    script.check_database_url("postgresql://u:p@localhost:5433/natural_reader_bench")


def test_the_benchmark_percentile_is_nearest_rank_and_its_vocabulary_is_seeded():
    import random
    script = _bench_script()
    ms = [float(i) for i in range(1, 101)]
    assert (script.percentile(ms, 50), script.percentile(ms, 95), script.percentile(ms, 100)) == (50.0, 95.0, 100.0)
    assert script.percentile([7.0], 95) == 7.0
    first = script.make_vocabulary(random.Random(1), 200)
    assert first == script.make_vocabulary(random.Random(1), 200) and len(set(first)) == 200
