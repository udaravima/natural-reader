import json
import logging

import pytest

from server.chat import tools as chat_tools
from server.chat.config import ChatConfig
from server.chat.project_scope import ScopeDoc, load_project_scope
from server.chat.tools import ToolContext, available_tools, run_tool
from server.chat.tools import scope
from server.chat.tools import search_documents as sd_tool
from server.chat.tools import web_search as ws_tool
from server.llm.types import ToolCall
from server.services import doc_search
from server.services.embeddings import EMBEDDING_DIM, current_profile
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
    assert [t.name for t in available_tools(ToolContext(alice.user_id, doc))] == ["search_documents", "read_document_pages", "extract_from_documents", "web_search", "read_web_pages"]
    assert [t.name for t in available_tools(ToolContext(alice.user_id, None))] == ["web_search", "read_web_pages"]
    extracting = doc_search.ReadableDoc(DOC, "x", "extracting")
    assert "search_documents" not in [t.name for t in available_tools(ToolContext(alice.user_id, extracting))]


async def _search(ctx, args, call_id="c1"):
    return await run_tool(ToolCall(call_id, "search_documents", args), ctx, available_tools(ctx))


async def _ctx(indexed, **kw):
    conn, alice = indexed
    return ToolContext(alice.user_id, await doc_search.readable_doc(conn, DOC, alice.user_id), **kw)


async def test_passages_name_their_document_and_page_and_no_score_or_label(indexed):
    ctx = await _ctx(indexed)
    run = await _search(ctx, {"query": "match", "k": 2})
    assert run.ok
    assert run.result["query"] == "match"
    assert run.result["documents"] == [{"ref": 1, "name": "Thesis.pdf"}]
    first, second = run.result["passages"]
    assert (first["ref"], first["page"], second["page"]) == (1, 3, 7)    # nearest first
    # No score and no relevance label: closeness doesn't mean the passage
    # answers (measured 2026-10-05; search_documents.py).
    assert "score" not in first and "relevance" not in first and "docId" not in run.result
    assert first["text"].endswith(" [truncated]") and len(first["text"]) == 1500 + len(" [truncated]")
    # The saved summary keeps the raw scores and names the document, so a
    # reply's "(page N)" can open it later (Task 6 citations).
    assert run.summary == {"name": "search_documents", "arguments": {"query": "match", "k": 2},
                           "result_summary": {"ok": True, "chunk_count": 2, "query": "match",
                                              "summary_text": None, "docId": DOC, "docName": "Thesis.pdf",
                                              "passages": [{"page": 3, "score": 1.0},
                                                           {"page": 7, "score": 0.7071}]}}


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
    assert second.result["passages"] == [{"ref": 1, "page": 7, "text": "half match"}]
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
    for words in ("open document", "By meaning", "vector search", "By exact words", "every word of the query",
                  "page", "read them before relying on them"):
        assert words in d, words
    # v2.4: longer than v2.3's 700 on purpose — it is the one place the model
    # learns that this isn't a web search engine (the user asked for it, and
    # the exact-word half needs every query word). Still under ~200 tokens.
    assert len(d) < 850


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
        return {"query": query, "results": [{"title": "t", "url": f"https://example.com/{i}", "site": "example.com",
                                             "snippet": "s"} for i in range(count)]}
    monkeypatch.setattr(ws_tool, "web_search", fake)
    ctx = ToolContext("u", None)
    bad = await run_tool(ToolCall("c1", "web_search", {"query": "x", "count": 50}), ctx, available_tools(ctx))
    assert bad.result == {"error": "count must be between 1 and 10."}
    ok = await run_tool(ToolCall("c2", "web_search", {"query": "news", "count": 2}), ctx, available_tools(ctx))
    assert ok.summary["result_summary"] == {"ok": True, "chunk_count": None, "query": "news",
                                            "summary_text": 'Web search for "news" returned 2 result(s).'}


def test_registry_order_is_stable():
    assert [t.name for t in chat_tools.REGISTRY] == ["search_documents", "read_document_pages",
                                                     "extract_from_documents", "document_outline", "web_search",
                                                     "read_web_pages"]


class _NullPool:
    class _Conn:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *a):
            return False

    def connection(self):
        return self._Conn()


@pytest.mark.parametrize("shown", [0, 15, 60, 200])
async def test_a_search_hands_what_is_shown_to_the_search_and_does_not_widen_it(monkeypatch, shown):
    """Review I1: over three rounds a turn can have shown 20+ passages. The search is told to look past all of
    them (`exclude`, set aside before the per-document balance) and still asks for k: widening k by the number
    shown let the shown ones use up a document's allowance (fix round 1)."""
    asked = {}

    async def fake_search_scope(conn, doc_ids, qvec, k, text=None, *, per_doc=None, depth=0, exclude=()):
        asked.update(k=k, per_doc=per_doc, doc_ids=doc_ids, exclude=exclude)
        return []

    async def fake_embed(text):
        return [0.0]

    monkeypatch.setattr(sd_tool, "search_scope", fake_search_scope)
    monkeypatch.setattr(sd_tool, "embed_query", fake_embed)
    monkeypatch.setattr(sd_tool, "get_pool", lambda: _NullPool())
    ctx = ToolContext("u", doc_search.ReadableDoc(DOC, "T.pdf", "indexed"), shown=set(range(shown)))
    await sd_tool.TOOL.execute({"query": "q", "k": 5}, ctx)
    assert asked["k"] == 5 and set(asked["exclude"]) == set(range(shown))
    assert asked["doc_ids"] == [DOC] and asked["per_doc"] is None     # one document: nothing to balance


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


async def test_an_old_version_carries_a_version_note_into_the_listing(indexed):
    conn, alice = indexed
    pid = await seed.make_project(conn, alice.user_id, "Thesis group")
    newer = "e" * 64
    await seed.seed_doc(conn, newer, alice.user_id, file_name="Thesis v2.pdf", state="indexed")
    pdoc = await seed.place_doc(conn, pid, DOC, alice.user_id)
    await seed.add_version(conn, pdoc, newer, alice.user_id)
    doc = await doc_search.readable_doc(conn, DOC, alice.user_id)
    assert doc.version_note["project"]["name"] == "Thesis group"
    [item] = scope.documents_listing(scope.scope_docs(ToolContext(alice.user_id, doc)))
    assert item["version_note"] == ('version 1 in project "Thesis group"; '
                                    "not the current version, which is version 2")
    current = await doc_search.readable_doc(conn, newer, alice.user_id)
    assert current.version_note is None
    assert "version_note" not in scope.documents_listing(scope.scope_docs(ToolContext(alice.user_id, current)))[0]


async def test_a_project_only_reader_sees_the_name_as_filed_not_the_canonical_one(indexed):
    conn, alice = indexed
    bob = await member(conn, "bob-chat")
    pid = await seed.make_project(conn, alice.user_id, "P", members=[(bob.user_id, "reader")])
    await seed.place_doc(conn, pid, DOC, alice.user_id, file_name="Group thesis.pdf")
    doc = await doc_search.readable_doc(conn, DOC, bob.user_id)
    assert doc.name == "Group thesis.pdf"


# ---- C2.1 Task 8: the search tool over a project ----

PA, PB, PC = "a" * 64, "b" * 64, "c" * 64


@pytest.fixture
async def project(db_conn, monkeypatch):
    """Alice's project: Alpha.md (ref 1), Beta.md (2), Gamma.md (3), all indexed. Every query embeds to (1, 0, ...)."""
    shim_pool(monkeypatch, db_conn, sd_tool)

    async def fake_embed(text):
        return [1.0] + [0.0] * (EMBEDDING_DIM - 1)

    monkeypatch.setattr(sd_tool, "embed_query", fake_embed)
    alice = await member(db_conn, "alice")
    pid = await seed.make_project(db_conn, alice.user_id, "Handbook")
    for doc_id, name in ((PA, "Alpha.md"), (PB, "Beta.md"), (PC, "Gamma.md")):
        await seed.seed_doc(db_conn, doc_id, alice.user_id, file_name=name, state="indexed")
        await db_conn.execute("UPDATE documents SET page_count = 9, embedding_profile = %s WHERE doc_id = %s",
                              (current_profile(), doc_id))
        await seed.place_doc(db_conn, pid, doc_id, alice.user_id, file_name=name)
    scope_ = await load_project_scope(db_conn, alice.user_id, pid, open_doc_id=None)
    assert [d.name for d in scope_.searchable] == ["Alpha.md", "Beta.md", "Gamma.md"]
    return db_conn, alice, scope_


async def _pchunk(conn, doc_id, ord_, page, text, *vec):
    await conn.execute(
        "INSERT INTO doc_chunks (doc_id, ord, page, chunk_type, text, text_hash, embedding, embedding_model) "
        "VALUES (%s, %s, %s, 'page', %s, %s, %s::vector, 'm')", (doc_id, ord_, page, text, f"h{doc_id[:2]}{ord_}", _vec(*vec)))


def _project_ctx(project, **kw):
    _, alice, scope_ = project
    return ToolContext(alice.user_id, None, project=scope_, question="q", **kw)


async def test_search_documents_filters_to_named_documents(project):
    conn, _, _ = project
    await _pchunk(conn, PA, 0, 1, "Alpha overview.", 1, 0.1)
    await _pchunk(conn, PB, 0, 2, "Beta overview.", 1, 0.2)
    await _pchunk(conn, PC, 0, 3, "Gamma overview.", 1, 0.3)
    ctx = _project_ctx(project)
    run = await _search(ctx, {"query": "overview", "k": 10, "documents": [2, "gamma.md"]})
    assert run.ok
    assert sorted((p["ref"], p["page"]) for p in run.result["passages"]) == [(2, 2), (3, 3)]
    assert {e[0] for e in ctx.evidence} == {PB, PC}
    assert [d["ref"] for d in run.result["documents"]] == [2, 3]
    # a lone value (small models do that) names one document; an empty list names none: the whole project
    one = await _search(_project_ctx(project), {"query": "overview", "documents": "1"}, "c2")
    assert [(p["ref"], p["page"]) for p in one.result["passages"]] == [(1, 1)]
    everything = await _search(_project_ctx(project), {"query": "overview", "k": 10, "documents": []}, "c3")
    assert sorted(p["ref"] for p in everything.result["passages"]) == [1, 2, 3]


async def test_search_documents_unknown_document_lists_valid_ones(project):
    ctx = _project_ctx(project)
    run = await _search(ctx, {"query": "overview", "documents": [1, "Nope.md"]})
    assert not run.ok
    assert run.result["error"].startswith("Unknown document: Nope.md. Use a number or name from the list: ")
    assert run.result["error"].endswith("1 Alpha.md, 2 Beta.md, 3 Gamma.md.")
    assert ctx.shown == set() and ctx.evidence == []                     # nothing was searched
    two = await _search(ctx, {"query": "overview", "documents": [7, "x\ny"]}, "c2")
    assert two.result["error"].startswith("Unknown document: 7, x y. ")


async def test_search_documents_balances_in_project_scope(project):
    """One ranking, but a document that dominates it gives only CHAT_SEARCH_PER_DOC (2) passages until k are
    chosen; the unbalanced list would have been Alpha four times."""
    conn, _, _ = project
    for i in range(5):
        await _pchunk(conn, PA, i, 1 + i, f"Alpha overview {i}.", 1, 0.1 * (i + 1))
    await _pchunk(conn, PB, 0, 1, "Beta overview.", 1, 1)
    await _pchunk(conn, PC, 0, 1, "Gamma overview.", 1, 1.2)
    ctx = _project_ctx(project)
    run = await _search(ctx, {"query": "overview", "k": 4})
    assert [(p["ref"], p["page"]) for p in run.result["passages"]] == [(1, 1), (1, 2), (2, 1), (3, 1)]
    assert [d["ref"] for d in run.result["documents"]] == [1, 2, 3]
    assert [p["docId"] for p in run.summary["result_summary"]["passages"]] == [PA, PA, PB, PC]
    # with the cap off, the same search fills from Alpha: the cap is what moved Beta and Gamma up
    wide = _project_ctx(project, cfg=ChatConfig(search_per_doc=10))
    unbalanced = await _search(wide, {"query": "overview", "k": 4}, "c2")
    assert [p["ref"] for p in unbalanced.result["passages"]] == [1, 1, 1, 1]


async def test_project_search_hands_the_fusion_depth_and_the_cap_to_the_search(monkeypatch):
    asked = {}

    async def fake_search_scope(conn, doc_ids, qvec, k, text=None, *, per_doc=None, depth=0, exclude=()):
        asked.update(per_doc=per_doc, depth=depth, text=text, doc_ids=doc_ids)
        return []

    async def fake_embed(text):
        return [0.0]

    monkeypatch.setattr(sd_tool, "search_scope", fake_search_scope)
    monkeypatch.setattr(sd_tool, "embed_query", fake_embed)
    monkeypatch.setattr(sd_tool, "get_pool", lambda: _NullPool())
    docs = tuple(ScopeDoc(i, doc_search.ReadableDoc(str(i) * 64, f"D{i}.md", "indexed"), f"D{i}.md", False)
                 for i in (1, 2))
    from server.chat.project_scope import ProjectScope
    ctx = ToolContext("u", None, project=ProjectScope("p", "P", docs), cfg=ChatConfig(search_per_doc=3, project_fusion_depth=77))
    await sd_tool.TOOL.execute({"query": "q words", "k": 5}, ctx)
    assert (asked["per_doc"], asked["depth"], asked["text"]) == (3, 77, "q words")
    assert asked["doc_ids"] == ["1" * 64, "2" * 64]


async def test_a_result_lists_only_the_documents_it_refers_to(project):
    """The map (a later task) names every document; a result names the ones its passages, or its "already
    shown" notes, are about."""
    conn, _, _ = project
    await _pchunk(conn, PA, 0, 1, "Alpha overview.", 1, 0.1)
    await _pchunk(conn, PB, 0, 2, "Beta overview.", 1, 0.5)
    await _pchunk(conn, PC, 0, 3, "Gamma overview.", 1, 0.9)
    ctx = _project_ctx(project, cfg=ChatConfig(search_min_score=0.0))
    first = await _search(ctx, {"query": "overview", "k": 1})
    assert [d["ref"] for d in first.result["documents"]] == [1] and first.result["passages"][0]["ref"] == 1
    second = await _search(ctx, {"query": "overview", "k": 1}, "c2")
    assert [p["ref"] for p in second.result["passages"]] == [2]
    assert second.result["already_shown"] == [{"ref": 1, "pages": [1]}]
    assert [d["ref"] for d in second.result["documents"]] == [1, 2]       # Alpha because of the note
    # all of it already shown: no passages, but the notes still name their documents
    done = await _search(_project_ctx(project, cfg=ChatConfig(search_min_score=0.0), shown=set(ctx.shown)),
                         {"query": "overview", "documents": [1, 2], "k": 5}, "c3")
    assert done.result["passages"] == [] and [d["ref"] for d in done.result["documents"]] == [1, 2]
    assert done.result["message"].startswith("Nothing new")
    # nothing found at all: no document is named
    none = await _search(_project_ctx(project, cfg=ChatConfig(search_min_score=1.01)), {"query": "qqqq"}, "c4")
    assert none.result["passages"] == [] and none.result["documents"] == []


def test_resolve_documents_returns_found_and_unknown_in_the_order_given():
    docs = [ScopeDoc(i, doc_search.ReadableDoc(str(i) * 64, n, "indexed"), n, False)
            for i, n in ((1, "Alpha.md"), (2, "Beta.md"), (3, "2024"))]
    found, unknown = scope.resolve_documents(docs, [2, "alpha.MD", "2", 2.0, "2024", 9, "x\ny", True])
    assert [d.ref for d in found] == [2, 1, 3]            # "2" and 2.0 name Beta again: once; "2024" is a name
    assert unknown == ["9", "x y", "True"]
    assert scope.resolve_documents(docs, []) == ([], [])


async def test_search_documents_ignores_documents_in_document_scope(indexed):
    """One open document: a near-name a small model fills in must not cost it a round."""
    ctx = await _ctx(indexed)
    run = await _search(ctx, {"query": "m", "documents": ["the thesis", 7]})
    assert run.ok and [p["page"] for p in run.result["passages"]] == [3, 7]


def test_the_search_tool_takes_an_optional_documents_list():
    spec = sd_tool.TOOL.spec.parameters
    assert spec["properties"]["documents"]["type"] == "array"
    assert spec["properties"]["documents"]["items"] == {"type": ["integer", "string"]}
    assert spec["required"] == ["query"]


async def test_a_follow_up_search_reaches_a_documents_deeper_passages(db_conn, monkeypatch):
    """Fix round 1: passages already shown must not use up their document's allowance (2 per document in a
    project search). Alpha's two best, Beta's best and Gamma's best were shown; widening k by that count and
    balancing BEFORE dropping them left Alpha's 3rd and 4th out of reach (the follow-up gave Delta 1, Beta 2,
    Gamma 2, Delta 2, Epsilon 1) unless the model narrowed with `documents`."""
    shim_pool(monkeypatch, db_conn, sd_tool)

    async def fake_embed(text):
        return [1.0] + [0.0] * (EMBEDDING_DIM - 1)

    monkeypatch.setattr(sd_tool, "embed_query", fake_embed)
    alice = await member(db_conn, "alice")
    pid = await seed.make_project(db_conn, alice.user_id, "Handbook")
    ids = {name: chr(97 + i) * 64 for i, name in enumerate(("Alpha", "Beta", "Gamma", "Delta", "Epsilon"))}
    for name, doc_id in ids.items():
        await seed.seed_doc(db_conn, doc_id, alice.user_id, file_name=f"{name}.md", state="indexed")
        await db_conn.execute("UPDATE documents SET page_count = 9, embedding_profile = %s WHERE doc_id = %s",
                              (current_profile(), doc_id))
        await seed.place_doc(db_conn, pid, doc_id, alice.user_id, file_name=f"{name}.md")
    # Meaning order, best first (the query vector is (1, 0, ...), the 2nd number is the distance): the page is
    # the document's own rank, so "Alpha 3" is Alpha's third best.
    order = [("Alpha", 1), ("Alpha", 2), ("Beta", 1), ("Gamma", 1), ("Alpha", 3), ("Alpha", 4), ("Delta", 1),
             ("Beta", 2), ("Gamma", 2), ("Delta", 2), ("Epsilon", 1), ("Epsilon", 2), ("Beta", 3), ("Gamma", 3)]
    for rank, (name, page) in enumerate(order):
        await _pchunk(db_conn, ids[name], page, page, f"{name} overview {page}.", 1, 0.02 * (rank + 1))
    scope_ = await load_project_scope(db_conn, alice.user_id, pid, open_doc_id=None)
    cur = await db_conn.execute(
        "SELECT id FROM doc_chunks WHERE (doc_id, page) IN ((%s, 1), (%s, 2), (%s, 1), (%s, 1))",
        (ids["Alpha"], ids["Alpha"], ids["Beta"], ids["Gamma"]))
    shown = {r[0] for r in await cur.fetchall()}
    assert len(shown) == 4
    ctx = ToolContext(alice.user_id, None, project=scope_, question="q", shown=set(shown))
    run = await _search(ctx, {"query": "zzzz", "k": 5})
    assert run.ok
    got = [(p["ref"], p["page"]) for p in run.result["passages"]]
    # refs follow the names: Alpha 1, Beta 2, Delta 3, Epsilon 4, Gamma 5
    assert got == [(1, 3), (1, 4), (3, 1), (2, 2), (5, 2)]       # Alpha 3 and 4 are reachable; five NEW passages
    assert ctx.shown >= shown and len(ctx.shown) == 9
    # The notes about what the model already has still come, for the documents they are from.
    assert run.result["already_shown"] == [{"ref": 1, "pages": [1, 2]}, {"ref": 2, "pages": [1]}, {"ref": 5, "pages": [1]}]
    assert [d["ref"] for d in run.result["documents"]] == [1, 2, 3, 5]
