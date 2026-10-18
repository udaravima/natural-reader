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
    assert set(rows[0]) == {"id", "page", "chunk_type", "text", "score", "by_words", "word_rank"}
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
