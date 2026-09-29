import logging

import pytest

from server.chat import tools as chat_tools
from server.chat.tools import ToolContext, available_tools, run_tool
from server.chat.tools import search_document as sd_tool
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

    monkeypatch.setattr(sd_tool, "embed_one", fake_embed)
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


async def test_search_document_is_offered_only_for_an_indexed_readable_doc(indexed):
    conn, alice = indexed
    doc = await doc_search.readable_doc(conn, DOC, alice.user_id)
    assert [t.name for t in available_tools(ToolContext(alice.user_id, doc))] == ["search_document", "web_search"]
    assert [t.name for t in available_tools(ToolContext(alice.user_id, None))] == ["web_search"]
    extracting = doc_search.ReadableDoc(DOC, "x", "extracting")
    assert "search_document" not in [t.name for t in available_tools(ToolContext(alice.user_id, extracting))]


async def test_search_document_returns_ranked_capped_passages(indexed):
    conn, alice = indexed
    ctx = ToolContext(alice.user_id, await doc_search.readable_doc(conn, DOC, alice.user_id))
    run = await run_tool(ToolCall("c1", "search_document", {"query": "match", "k": 2}), ctx, available_tools(ctx))
    assert run.ok
    assert run.result["query"] == "match" and run.result["chunk_count"] == 2
    first, second = run.result["results"]
    assert (first["index"], first["page"], first["score"]) == (1, 3, 1.0)
    assert first["text"].endswith(" [truncated]") and len(first["text"]) == 1500 + len(" [truncated]")
    assert (second["page"], second["score"]) == (7, 0.7071)
    # The saved summary names the document, so a reply's "(page N)" can open it
    # later (Task 6 citations) — the model's result doesn't need it.
    assert run.summary == {"name": "search_document", "arguments": {"query": "match", "k": 2},
                           "result_summary": {"ok": True, "chunk_count": 2, "query": "match", "summary_text": None,
                                              "docId": DOC, "docName": ctx.doc.name}}
    assert "docId" not in run.result


async def test_search_document_clamps_k_and_needs_a_query(indexed):
    conn, alice = indexed
    ctx = ToolContext(alice.user_id, await doc_search.readable_doc(conn, DOC, alice.user_id))
    run = await run_tool(ToolCall("c1", "search_document", {"query": "m", "k": 99}), ctx, available_tools(ctx))
    assert run.result["chunk_count"] == 3                     # clamped to 10; only 3 chunks exist
    empty = await run_tool(ToolCall("c2", "search_document", {}), ctx, available_tools(ctx))
    assert not empty.ok and empty.result == {"error": "query is required and must be non-empty."}
    assert empty.summary["result_summary"] == {"error": "query is required and must be non-empty."}


async def test_a_tool_not_offered_is_unknown(indexed):
    _, alice = indexed
    ctx = ToolContext(alice.user_id, None)
    run = await run_tool(ToolCall("c1", "search_document", {"query": "x"}), ctx, available_tools(ctx))
    assert run.result == {"error": "Unknown tool: search_document"}


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
    assert [t.name for t in chat_tools.REGISTRY] == ["search_document", "web_search"]
