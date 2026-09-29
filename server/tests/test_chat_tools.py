import json
import logging

import pytest

from server.chat import tools as chat_tools
from server.chat.config import ChatConfig
from server.chat.tools import ToolContext, available_tools, run_tool
from server.chat.tools import search_documents as sd_tool
from server.chat.tools import web_search as ws_tool
from server.llm.types import ToolCall
from server.services import doc_search
from server.services.embeddings import EMBEDDING_DIM
from server.tests import seed
from server.tests.chat_harness import member, shim_pool

DOC = "d" * 64


def _vec(*head):
    return "[" + ",".join(str(x) for x in list(head) + [0] * (EMBEDDING_DIM - len(head))) + "]"


async def _chunk(conn, ord_, page, text, vec):
    await conn.execute(
        "INSERT INTO doc_chunks (doc_id, ord, page, chunk_type, text, text_hash, embedding, embedding_model) "
        "VALUES (%s, %s, %s, 'page', %s, %s, %s::vector, 'm')", (DOC, ord_, page, text, f"h{ord_}", vec))


@pytest.fixture
async def indexed(db_conn, monkeypatch):
    shim_pool(monkeypatch, db_conn, sd_tool)

    async def fake_embed(text):
        return [1.0] + [0.0] * (EMBEDDING_DIM - 1)

    monkeypatch.setattr(sd_tool, "embed_query", fake_embed)
    alice = await member(db_conn, "alice")
    await seed.seed_doc(db_conn, DOC, alice.user_id, file_name="Thesis.pdf", state="indexed")
    await _chunk(db_conn, 0, 3, "exact match " + "x" * 2000, _vec(1))
    await _chunk(db_conn, 1, 7, "half match", _vec(1, 1))
    await _chunk(db_conn, 2, 9, "no match", _vec(0, 1))
    return db_conn, alice


async def test_readable_doc_uses_the_readers_name_and_hides_others_docs(indexed):
    conn, alice = indexed
    await conn.execute("UPDATE library_entries SET file_name = 'My thesis.pdf' WHERE user_id = %s", (alice.user_id,))
    doc = await doc_search.readable_doc(conn, DOC, alice.user_id)
    assert (doc.doc_id, doc.name, doc.state) == (DOC, "My thesis.pdf", "indexed")
    bob = await member(conn, "bob")
    assert await doc_search.readable_doc(conn, DOC, bob.user_id) is None


async def test_search_documents_is_offered_only_for_an_indexed_readable_doc(indexed):
    conn, alice = indexed
    doc = await doc_search.readable_doc(conn, DOC, alice.user_id)
    assert [t.name for t in available_tools(ToolContext(alice.user_id, doc))] == ["search_documents", "read_document_pages", "web_search"]
    assert [t.name for t in available_tools(ToolContext(alice.user_id, None))] == ["web_search"]
    extracting = doc_search.ReadableDoc(DOC, "x", "extracting")
    assert "search_documents" not in [t.name for t in available_tools(ToolContext(alice.user_id, extracting))]


async def _search(ctx, args, call_id="c1"):
    return await run_tool(ToolCall(call_id, "search_documents", args), ctx, available_tools(ctx))


async def _ctx(indexed, **kw):
    conn, alice = indexed
    return ToolContext(alice.user_id, await doc_search.readable_doc(conn, DOC, alice.user_id), **kw)


async def test_passages_name_their_document_page_and_relevance_not_a_raw_score(indexed):
    ctx = await _ctx(indexed)
    run = await _search(ctx, {"query": "match", "k": 2})
    assert run.ok
    assert run.result["query"] == "match"
    assert run.result["documents"] == [{"ref": 1, "name": "Thesis.pdf"}]
    first, second = run.result["passages"]
    assert (first["ref"], first["page"], first["relevance"]) == (1, 3, "strong")
    assert (second["page"], second["relevance"]) == (7, "strong")        # 0.7071
    assert "score" not in first and "docId" not in run.result
    assert first["text"].endswith(" [truncated]") and len(first["text"]) == 1500 + len(" [truncated]")
    # The saved summary keeps the raw scores and names the document, so a
    # reply's "(page N)" can open it later (Task 6 citations).
    assert run.summary == {"name": "search_documents", "arguments": {"query": "match", "k": 2},
                           "result_summary": {"ok": True, "chunk_count": 2, "query": "match",
                                              "summary_text": None, "docId": DOC, "docName": "Thesis.pdf",
                                              "passages": [{"page": 3, "score": 1.0},
                                                           {"page": 7, "score": 0.7071}]}}


@pytest.mark.parametrize("score,bucket", [(0.95, "strong"), (0.7, "strong"), (0.62, "moderate"),
                                          (0.55, "moderate"), (0.5, "weak"), (0.45, "weak")])
def test_relevance_buckets(score, bucket):
    assert sd_tool.relevance(score) == bucket


async def test_passages_below_the_floor_are_dropped_and_none_left_says_so(indexed):
    ctx = await _ctx(indexed)
    run = await _search(ctx, {"query": "m", "k": 10})
    assert [p["page"] for p in run.result["passages"]] == [3, 7]           # page 9 scored 0.0
    strict = await _ctx(indexed, cfg=ChatConfig(search_min_score=1.01))
    none = await _search(strict, {"query": "m"})
    assert none.ok and none.result["passages"] == []
    assert none.result["message"] == "No passages about this in the document."
    assert none.summary["result_summary"]["chunk_count"] == 0


async def test_a_passage_already_shown_this_turn_comes_back_without_its_text(indexed):
    """Repeated searches surface new material instead of the same text."""
    ctx = await _ctx(indexed)
    first = await _search(ctx, {"query": "m", "k": 1})
    assert [p["page"] for p in first.result["passages"]] == [3]
    second = await _search(ctx, {"query": "m", "k": 1}, "c2")
    assert second.result["passages"] == [{"ref": 1, "page": 7, "relevance": "strong", "text": "half match"}]
    assert second.result["already_shown"] == [{"ref": 1, "pages": [3]}]
    assert second.summary["result_summary"]["chunk_count"] == 1
    third = await _search(ctx, {"query": "m", "k": 5}, "c3")
    assert third.result["passages"] == [] and third.result["already_shown"] == [{"ref": 1, "pages": [3, 7]}]
    assert third.result["message"] == ("Nothing new: every passage found was already shown above. "
                                       "Search with different words, or answer from what you have.")


async def test_the_prefetched_passages_count_as_shown(indexed):
    conn, _ = indexed
    cur = await conn.execute("SELECT id FROM doc_chunks WHERE doc_id = %s AND page = 3", (DOC,))
    ctx = await _ctx(indexed)
    ctx.shown.add((await cur.fetchone())[0])
    run = await _search(ctx, {"query": "m", "k": 1})
    assert run.result["already_shown"] == [{"ref": 1, "pages": [3]}]
    assert [p["page"] for p in run.result["passages"]] == [7]


async def test_search_documents_clamps_k_and_needs_a_query(indexed):
    ctx = await _ctx(indexed)
    run = await _search(ctx, {"query": "m", "k": 99})
    assert run.summary["result_summary"]["chunk_count"] == 2   # clamped to 10; 2 pass the floor
    empty = await _search(ctx, {}, "c2")
    assert not empty.ok and empty.result == {"error": "query is required and must be non-empty."}
    assert empty.summary["result_summary"] == {"error": "query is required and must be non-empty."}


async def test_a_tool_not_offered_is_unknown(indexed):
    _, alice = indexed
    ctx = ToolContext(alice.user_id, None)
    run = await _search(ctx, {"query": "x"})
    assert run.result == {"error": "Unknown tool: search_documents"}


def test_no_tool_description_names_another_tool():
    """Review focus 1: a description is sent whenever its tool is offered, and
    the other tool may not be. Routing between tools lives in the rules."""
    for tool in chat_tools.REGISTRY:
        text = json.dumps({"d": tool.spec.description, "p": tool.spec.parameters})
        for other in chat_tools.REGISTRY:
            if other is not tool:
                assert other.name not in text, (tool.name, other.name)


def test_the_search_description_says_what_it_searches_and_how():
    d = sd_tool.TOOL.spec.description
    for words in ("open document", "by meaning", "page", "relevance", "already searched", "different words"):
        assert words in d, words
    assert len(d) < 700                                       # short enough for a 3B model


async def test_a_crashing_tool_becomes_an_error_result(monkeypatch):
    async def boom(query, count):
        raise RuntimeError("searxng down")
    monkeypatch.setattr(ws_tool, "web_search", boom)
    ctx = ToolContext("u", None)
    run = await run_tool(ToolCall("c1", "web_search", {"query": "x"}), ctx, available_tools(ctx))
    assert run.result == {"error": "web_search failed: RuntimeError"}


async def test_a_tool_failure_logs_only_the_exception_type_at_warning(monkeypatch, caplog):
    """Final review M8: an exception's message can quote the user's query;
    no user text at WARNING+ (spec §10). The full exception is DEBUG only."""
    async def boom(query, count):
        raise RuntimeError(f"no results for {query!r}")
    monkeypatch.setattr(ws_tool, "web_search", boom)
    ctx = ToolContext("u", None)
    with caplog.at_level(logging.DEBUG, logger="server.chat.tools"):
        await run_tool(ToolCall("c1", "web_search", {"query": "my private question"}), ctx, available_tools(ctx))
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings and all("my private question" not in r.getMessage() and r.exc_info is None
                            for r in warnings)
    assert "RuntimeError" in warnings[0].getMessage()
    assert any("my private question" in (r.getMessage() + str(r.exc_info)) for r in caplog.records
               if r.levelno == logging.DEBUG)


async def test_web_search_validates_count_and_summarizes(monkeypatch):
    async def fake(query, count):
        return {"query": query, "results": [{"title": "t", "url": "https://example.com", "summary": "s"}] * count}
    monkeypatch.setattr(ws_tool, "web_search", fake)
    ctx = ToolContext("u", None)
    bad = await run_tool(ToolCall("c1", "web_search", {"query": "x", "count": 50}), ctx, available_tools(ctx))
    assert bad.result == {"error": "count must be between 1 and 10."}
    ok = await run_tool(ToolCall("c2", "web_search", {"query": "news", "count": 2}), ctx, available_tools(ctx))
    assert ok.summary["result_summary"] == {"ok": True, "chunk_count": None, "query": "news",
                                            "summary_text": 'Web search for "news" returned 2 result(s).'}


def test_registry_order_is_stable():
    assert [t.name for t in chat_tools.REGISTRY] == ["search_documents", "read_document_pages", "web_search"]


@pytest.mark.parametrize("shown,want", [(0, 5), (15, 20), (60, 65), (200, 100)])
async def test_a_search_asks_for_enough_rows_to_skip_everything_already_shown(monkeypatch, shown, want):
    """Review I1: over three rounds a turn can have shown 20+ passages. The
    search must look past all of them (up to MAX_ROWS, a cost bound),
    or it reports "nothing new" while new evidence ranks lower."""
    asked = {}

    async def fake_search_chunks(conn, doc_id, qvec, k, text=None):
        asked["k"] = k
        return []

    async def fake_embed(text):
        return [0.0]

    class _Conn:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *a):
            return False

    class _Pool:
        def connection(self):
            return _Conn()

    monkeypatch.setattr(sd_tool, "search_chunks", fake_search_chunks)
    monkeypatch.setattr(sd_tool, "embed_query", fake_embed)
    monkeypatch.setattr(sd_tool, "get_pool", lambda: _Pool())
    ctx = ToolContext("u", doc_search.ReadableDoc(DOC, "T.pdf", "indexed"), shown=set(range(shown)))
    await sd_tool.TOOL.execute({"query": "q", "k": 5}, ctx)
    assert asked["k"] == want


async def test_a_search_finds_the_documents_own_passages_when_other_documents_are_closer(indexed):
    """Task B re-review: the HNSW index covers every document's chunks and the
    doc_id filter ran AFTER the index scan, which keeps only ef_search (40)
    candidates library-wide. With 300 closer chunks in another document, the
    open document's passages never came back. The search must be exact over
    the one document (a few hundred chunks: cheap)."""
    conn, alice = indexed
    other = "e" * 64
    await seed.seed_doc(conn, other, alice.user_id, file_name="Other.pdf", state="indexed")
    for i in range(300):
        await conn.execute(
            "INSERT INTO doc_chunks (doc_id, ord, page, chunk_type, text, text_hash, embedding, embedding_model) "
            "VALUES (%s, %s, 1, 'page', 'other', %s, %s::vector, 'm')", (other, i, f"o{i}", _vec(1, 0.001 * i)))
    # The plan a large library can get: walk the HNSW index in distance order.
    await conn.execute("SET LOCAL enable_seqscan = off")
    await conn.execute("SET LOCAL enable_sort = off")
    rows = await doc_search.search_chunks(conn, DOC, [1.0] + [0.0] * (EMBEDDING_DIM - 1), 3)
    assert [r["page"] for r in rows] == [3, 7, 9]
