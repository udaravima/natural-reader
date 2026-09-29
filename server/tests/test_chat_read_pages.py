"""v2.3 Task C: read_document_pages — the exact text of up to 3 whole pages
of the open document (journeys J3, J4, J9 in the plan)."""
from __future__ import annotations

import json

import pytest

from server.chat import tools as chat_tools
from server.chat.config import ChatConfig, load_chat_config
from server.chat.tools import ToolContext, available_tools, run_tool
from server.chat.tools import read_document_pages as rp_tool
from server.llm.types import ToolCall
from server.services import doc_search
from server.tests import seed
from server.tests.chat_harness import member, shim_pool

DOC = "d" * 64


async def _chunk(conn, ord_, page, text, chunk_type="page"):
    cur = await conn.execute(
        "INSERT INTO doc_chunks (doc_id, ord, page, chunk_type, text, text_hash) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id", (DOC, ord_, page, chunk_type, text, f"h{ord_}"))
    return (await cur.fetchone())[0]


@pytest.fixture
async def book(db_conn, monkeypatch):
    """Five pages; page 2 is two chunks (a markdown page has several blocks)."""
    shim_pool(monkeypatch, db_conn, rp_tool)
    alice = await member(db_conn, "alice")
    await seed.seed_doc(db_conn, DOC, alice.user_id, file_name="Thesis.pdf", state="indexed")
    await db_conn.execute("UPDATE documents SET page_count = 5 WHERE doc_id = %s", (DOC,))
    ids = {}
    ids[1] = await _chunk(db_conn, 0, 1, "Page one.")
    ids[2] = await _chunk(db_conn, 1, 2, "Table 4.2 shows the results.")
    ids[3] = await _chunk(db_conn, 2, 2, "Accuracy rose to 91%.")
    ids[4] = await _chunk(db_conn, 3, 3, "Page three.")
    ids[5] = await _chunk(db_conn, 4, 4, "Page four.")
    ids[6] = await _chunk(db_conn, 5, 5, "Page five.")
    doc = await doc_search.readable_doc(db_conn, DOC, alice.user_id)
    return ToolContext(alice.user_id, doc), ids


async def _read(ctx, args, call_id="c1"):
    return await run_tool(ToolCall(call_id, "read_document_pages", args), ctx, available_tools(ctx))


async def test_offered_with_the_document_search_and_only_for_an_indexed_document(book):
    ctx, _ = book
    assert [t.name for t in available_tools(ctx)] == ["search_documents", "read_document_pages", "web_search"]
    assert "read_document_pages" not in [t.name for t in available_tools(ToolContext("u", None))]
    extracting = doc_search.ReadableDoc(DOC, "x", "extracting", 5)
    assert "read_document_pages" not in [t.name for t in available_tools(ToolContext("u", extracting))]


async def test_one_page_is_its_chunks_joined_in_order(book):
    ctx, ids = book
    run = await _read(ctx, {"first_page": 2})
    assert run.ok
    assert run.result["documents"] == [{"ref": 1, "name": "Thesis.pdf"}]
    assert run.result["pages"] == [{"ref": 1, "page": 2,
                                    "text": "Table 4.2 shows the results.\n\nAccuracy rose to 91%."}]
    # Its chunks now count as shown: a later search won't send them again.
    assert {ids[2], ids[3]} <= ctx.shown
    assert run.summary["result_summary"] == {
        "ok": True, "chunk_count": None, "query": None, "summary_text": "Read page 2.",
        "docId": DOC, "docName": "Thesis.pdf", "pages": [2]}


async def test_a_range_reads_up_to_three_pages(book):
    ctx, _ = book
    run = await _read(ctx, {"first_page": 3, "last_page": 5})
    assert [p["page"] for p in run.result["pages"]] == [3, 4, 5]
    assert run.summary["result_summary"]["summary_text"] == "Read pages 3-5."


async def test_more_than_three_pages_reads_the_first_three_and_says_where_to_continue(book):
    ctx, _ = book
    run = await _read(ctx, {"first_page": 1, "last_page": 5})
    assert [p["page"] for p in run.result["pages"]] == [1, 2, 3]
    assert run.result["message"] == "Not read: pages 4-5. Read from page 4 next."


@pytest.mark.parametrize("args,error", [
    ({"first_page": 0}, "This document has pages 1-5."),
    ({"first_page": 6}, "This document has pages 1-5."),
    ({"first_page": 4, "last_page": 9}, "This document has pages 1-5."),
    ({"first_page": 3, "last_page": 2}, "last_page must not be before first_page."),
    ({}, "first_page is required: a page number."),
    ({"first_page": "two"}, "first_page is required: a page number."),
    ({"first_page": True}, "first_page is required: a page number."),
])
async def test_out_of_range_or_malformed_pages_say_what_is_valid(book, args, error):
    ctx, _ = book
    run = await _read(ctx, args)
    assert not run.ok and run.result == {"error": error}


async def test_a_page_number_sent_as_a_string_is_accepted(book):
    """Small models often send "2" for an integer argument."""
    ctx, _ = book
    run = await _read(ctx, {"first_page": "2", "last_page": 2.0})
    assert [p["page"] for p in run.result["pages"]] == [2]


async def test_the_text_is_capped_with_a_marker(book):
    ctx, ids = book
    small = ToolContext(ctx.user_id, ctx.doc, cfg=ChatConfig(read_pages_max_chars=20))
    run = await _read(small, {"first_page": 2, "last_page": 3})
    text = "".join(p["text"] for p in run.result["pages"])
    assert len(text.replace(rp_tool.CUT_MARKER, "")) == 20
    assert run.result["pages"][0]["text"].endswith(rp_tool.CUT_MARKER)
    assert [p["page"] for p in run.result["pages"]] == [2]     # nothing left for page 3
    assert run.result["message"] == ("Page 2 is longer than 20 characters: only its start is shown. "
                                     "Not read: page 3. Read from page 3 next.")
    assert not ({ids[2], ids[3]} & small.shown)                # a cut page isn't "shown"


def test_overlapping_chunks_are_joined_without_the_repeat():
    """Task E splits long pages with an overlap and marks each later part a
    continuation; reading a page must not show the overlapped text twice."""
    a = "The first part ends with this shared sentence."
    b = "this shared sentence. And the second part goes on."
    assert rp_tool.join_chunks([(a, False), (b, True)]) == (
        "The first part ends with this shared sentence. And the second part goes on.")
    assert rp_tool.join_chunks([("One.", False), ("Two.", False)]) == "One.\n\nTwo."
    assert rp_tool.join_chunks([]) == ""


def test_blocks_that_merely_repeat_text_are_never_merged():
    """Review minor: a refrain ending one stanza and starting the next is real
    text. Only a continuation part's overlap is removed."""
    a = "Stanza one ends with the refrain line here."
    b = "with the refrain line here. Stanza two goes on."
    assert rp_tool.join_chunks([(a, False), (b, False)]) == a + "\n\n" + b


async def test_a_converted_document_reads_its_markdown_pages(db_conn, monkeypatch):
    shim_pool(monkeypatch, db_conn, rp_tool)
    alice = await member(db_conn, "alice")
    await seed.seed_doc(db_conn, DOC, alice.user_id, file_name="Scan.pdf", state="indexed")
    await _chunk(db_conn, 0, 1, "# Title\n\n| a | b |\n|---|---|\n| 1 | 2 |", "page-md")
    ctx = ToolContext(alice.user_id, await doc_search.readable_doc(db_conn, DOC, alice.user_id))
    run = await _read(ctx, {"first_page": 1})
    assert run.result["pages"][0]["text"] == "# Title\n\n| a | b |\n|---|---|\n| 1 | 2 |"
    # page_count is unknown for a converted document: its last chunk's page bounds it.
    bad = await _read(ctx, {"first_page": 2}, "c2")
    assert bad.result == {"error": "This document has pages 1-1."}


async def test_a_cut_later_page_says_to_read_it_on_its_own(book):
    """Review I1: the continuation hint comes from what was returned."""
    ctx, _ = book
    small = ToolContext(ctx.user_id, ctx.doc, cfg=ChatConfig(read_pages_max_chars=30))
    run = await _read(small, {"first_page": 1, "last_page": 5})
    assert [p["page"] for p in run.result["pages"]] == [1, 2]   # page 1 whole, page 2 cut
    assert run.result["message"] == ("Page 2 was cut at the size limit: read it on its own for the rest. "
                                     "Not read: pages 2-5. Read from page 2 next.")


async def test_a_page_ending_exactly_at_the_limit_says_what_was_not_read(book):
    ctx, _ = book
    exact = ToolContext(ctx.user_id, ctx.doc, cfg=ChatConfig(read_pages_max_chars=len("Page one.")))
    run = await _read(exact, {"first_page": 1, "last_page": 3})
    assert [p["page"] for p in run.result["pages"]] == [1]
    assert run.result["message"] == "Not read: pages 2-3. Read from page 2 next."


async def test_refs_come_from_the_scope(book):
    ctx, _ = book
    run = await _read(ctx, {"first_page": 1})
    assert run.result["pages"][0]["ref"] == run.result["documents"][0]["ref"] == 1


async def test_a_converted_document_counts_trailing_pages_with_no_text(db_conn, monkeypatch):
    """Review minor: chunks_from_pages skips empty pages, so max(page) of the
    chunks can miss the last pages; doc_pages knows them."""
    shim_pool(monkeypatch, db_conn, rp_tool)
    alice = await member(db_conn, "alice")
    await seed.seed_doc(db_conn, DOC, alice.user_id, file_name="Scan.pdf", state="indexed")
    await _chunk(db_conn, 0, 1, "text", "page-md")
    for page, md in ((1, "text"), (2, "")):
        await db_conn.execute("INSERT INTO doc_pages (doc_id, page, markdown) VALUES (%s,%s,%s)",
                              (DOC, page, md))
    ctx = ToolContext(alice.user_id, await doc_search.readable_doc(db_conn, DOC, alice.user_id))
    run = await _read(ctx, {"first_page": 2})
    assert run.result["message"] == "Page 2 has no text (it may be an image)."


async def test_a_page_with_no_text_says_so(book, db_conn):
    ctx, ids = book
    await db_conn.execute("DELETE FROM doc_chunks WHERE id = %s", (ids[4],))
    run = await _read(ctx, {"first_page": 3})
    assert run.result["pages"] == [{"ref": 1, "page": 3, "text": ""}]
    assert run.result["message"] == "Page 3 has no text (it may be an image)."


def test_the_description_and_guidance_name_no_other_tool():
    text = json.dumps({"d": rp_tool.TOOL.spec.description, "p": rp_tool.TOOL.spec.parameters,
                       "g": rp_tool.TOOL.guidance(None)})
    for other in chat_tools.REGISTRY:
        if other is not rp_tool.TOOL:
            assert other.name not in text
    assert "3 pages" in rp_tool.TOOL.spec.description


def test_chat_read_pages_max_chars_is_read_from_the_environment():
    assert load_chat_config({}).read_pages_max_chars == 12000
    assert load_chat_config({"CHAT_READ_PAGES_MAX_CHARS": "4000"}).read_pages_max_chars == 4000
    assert load_chat_config({"CHAT_READ_PAGES_MAX_CHARS": "10"}).read_pages_max_chars == 12000
