"""C2.1 Task 11: extract_from_documents, the chat tool that hands pages to the reader sub-agent and returns
only verified facts (spec §5.4.1), and read_document_pages showing code fenced.

The reader's router is faked (it returns scripted JSON per batch); documents and chunks are real rows in the
test database, as for the other chat tool tests."""
from __future__ import annotations

import json
import logging

import pytest

from server.chat import reader
from server.chat.config import ChatConfig, load_chat_config
from server.chat.project_scope import load_project_scope
from server.chat.sources import answer_sources
from server.chat.tools import ToolContext, available_tools, run_tool
from server.chat.tools import extract_from_documents as ex_tool
from server.chat.tools import read_document_pages as rp_tool
from server.llm.types import CallSettings, ToolCall
from server.services import doc_search
from server.services.embeddings import EMBEDDING_DIM, current_profile
from server.services.inference_limits import InferenceLimits
from server.tests import seed
from server.tests.chat_harness import member, shim_pool

PA, PB, PC = "a" * 64, "b" * 64, "c" * 64
FALLBACK = "The pages were taken in order: finding the best pages failed."


def _vec(*head):
    return "[" + ",".join(str(x) for x in list(head) + [0] * (EMBEDDING_DIM - len(head))) + "]"


# ---- a scripted reader ------------------------------------------------------------------------------------

def facts_json(*facts):
    """(label, fact, quote) triples as the reader would reply."""
    return json.dumps({"found": bool(facts), "facts": [{"fact": f, "quote": q, "from": label}
                                                        for label, f, q in facts]})


class ScriptedRouter:
    """Stands in for llm.router.Router.complete. `script` gets the reader's user message (the labelled
    passages) and returns the reply text, or raises; every call is recorded. Like the real one it fills the
    caller's `received` with the reply and `usage`, the provider's own count (None: it sent none)."""

    def __init__(self, script, usage=None):
        self.script = script
        self.usage = usage
        self.calls: list[tuple[str, list, CallSettings]] = []

    async def complete(self, model_id, messages, settings=CallSettings(), *, received=None):
        self.calls.append((model_id, messages, settings))
        text = self.script(messages[1].content)
        if received is not None:
            received.text, received.usage = text, self.usage
        return text

    def seen(self) -> list[str]:
        """Every page label the reader was given, in the order given, over all calls."""
        labels = []
        for _, messages, _ in self.calls:
            labels += [line for line in messages[1].content.splitlines() if line.startswith("[") and line.endswith("]")]
        return labels


def by_label(table):
    """A script: for each label in `table` that the batch holds, one fact (fact, quote)."""
    def script(user):
        return facts_json(*[(label, *table[label]) for label in table if label in user])
    return script


NOTHING = lambda user: facts_json()    # noqa: E731


class Env:
    """Alice's project and the plumbing around it."""

    def __init__(self, conn, alice, project_id):
        self.conn, self.alice, self.project_id = conn, alice, project_id

    async def add_doc(self, doc_id, name, pages, *, state="indexed", page_count=None, dist=None):
        """A project document with one 'page' chunk per entry of `pages` ({page: text}); `dist` is the embedding
        distance of a page from the query vector (1, 0, ...), 0.5 where not given, None for no embedding."""
        await seed.seed_doc(self.conn, doc_id, self.alice.user_id, file_name=name, state=state)
        await self.conn.execute("UPDATE documents SET page_count = %s, embedding_profile = %s WHERE doc_id = %s",
                                (page_count if page_count is not None else max(pages, default=0),
                                 current_profile(), doc_id))
        await seed.place_doc(self.conn, self.project_id, doc_id, self.alice.user_id, file_name=name)
        for ord_, (page, text) in enumerate(pages.items()):
            await self.add_chunk(doc_id, ord_, page, text, d=(dist or {}).get(page, 0.5))

    async def add_chunk(self, doc_id, ord_, page, text, chunk_type="page", d=0.5):
        """`d` None = no embedding (the search never finds it; reading still does)."""
        await self.conn.execute(
            "INSERT INTO doc_chunks (doc_id, ord, page, chunk_type, text, text_hash, embedding, embedding_model) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s::vector, 'm')",
            (doc_id, ord_, page, chunk_type, text, f"h{doc_id[:2]}{ord_}", None if d is None else _vec(1, d)))

    async def ctx(self, open_doc_id=None, **kw):
        scope = await load_project_scope(self.conn, self.alice.user_id, self.project_id, open_doc_id=open_doc_id)
        return ToolContext(self.alice.user_id, None, project=scope, question="How do I install it?", **kw)

    async def doc_ctx(self, doc_id, **kw):
        doc = await doc_search.readable_doc(self.conn, doc_id, self.alice.user_id)
        return ToolContext(self.alice.user_id, doc, question="How do I install it?", **kw)


@pytest.fixture
async def env(db_conn, monkeypatch):
    shim_pool(monkeypatch, db_conn, ex_tool, rp_tool)
    monkeypatch.setattr(reader, "get_limits", lambda: InferenceLimits(32768, 1800))
    for key in ("READER_MODEL", "SUMMARIZE_MODEL", "WEB_SEARCH_SUMMARY_MODEL", "READER_INPUT_CHARS",
                "READER_OUTPUT_CHARS", "READER_MAX_CONCURRENCY", "READER_TIMEOUT_S",
                "WEB_SEARCH_SUMMARY_TIMEOUT_S"):
        monkeypatch.delenv(key, raising=False)

    async def fake_embed(text):
        return [1.0] + [0.0] * (EMBEDDING_DIM - 1)

    monkeypatch.setattr(ex_tool, "embed_query", fake_embed)
    alice = await member(db_conn, "alice")
    pid = await seed.make_project(db_conn, alice.user_id, "Handbook")
    return Env(db_conn, alice, pid)


@pytest.fixture
def use_router(monkeypatch):
    def install(script):
        router = ScriptedRouter(script)
        monkeypatch.setattr(reader, "get_router", lambda: router)
        return router
    return install


async def _extract(ctx, args, call_id="c1"):
    return await run_tool(ToolCall(call_id, "extract_from_documents", args), ctx, available_tools(ctx))


INSTALL = "dnf install python3.13"
START = "uvicorn app:main --port 8080"
ALPHA = {1: "Alpha handbook. Contents follow.", 2: f"To install, run {INSTALL} as root.",
         3: f"Start the server with {START} and open the browser.", 4: "Appendix: licence text."}


async def _alpha_and_gamma(env):
    await env.add_doc(PA, "Alpha.md", ALPHA)
    await env.add_doc(PC, "Gamma.md", {1: "Gamma is still extracting."}, state="extracting")


# ---- the seven tests the brief names ---------------------------------------------------------------------

async def test_extract_returns_verified_facts_with_ref_and_page(env, use_router):
    await _alpha_and_gamma(env)
    router = use_router(by_label({"[1, page 2]": ("Install with dnf", INSTALL),
                                  "[1, page 3]": ("Start with uvicorn", START)}))
    ctx = await env.ctx()
    run = await _extract(ctx, {"documents": [1], "need": "the install and start commands"})
    assert run.ok
    assert run.result == {
        "need": "the install and start commands",
        "documents": [{"ref": 1, "name": "Alpha.md"}],
        "facts": [{"ref": 1, "page": 2, "fact": "Install with dnf", "quote": INSTALL},
                  {"ref": 1, "page": 3, "fact": "Start with uvicorn", "quote": START}],
        "not_read": [], "failed": []}
    # All four pages went to the reader, labelled by ref and page only, in page order, with the question and goal.
    assert router.seen() == [f"[1, page {n}]" for n in (1, 2, 3, 4)]
    user = router.calls[0][1][1].content
    assert user.startswith("Question: How do I install it?\nGoal: the install and start commands\n\n[1, page 1]\n")


async def test_extract_drops_unverified_quotes(env, use_router):
    await _alpha_and_gamma(env)
    use_router(lambda user: facts_json(("[1, page 2]", "Install with dnf", INSTALL),
                                       ("[1, page 2]", "Needs Python 3.12", "requires python 3.12"),
                                       ("[1, page 3]", "Start on port 9090", "uvicorn app:main --port 9090")))
    ctx = await env.ctx()
    run = await _extract(ctx, {"documents": ["Alpha.md"], "need": "versions and ports"})
    assert [(f["ref"], f["page"], f["quote"]) for f in run.result["facts"]] == [(1, 2, INSTALL)]
    assert ctx.evidence == [(PA, 2, INSTALL)]                       # a dropped fact is not evidence
    assert "9090" not in json.dumps(run.result) and not any("9090" in t for t in ctx.seen_text)


async def test_extract_over_the_cap_reads_best_pages_and_lists_the_rest(env, use_router):
    """A 30-page document, cap 12: the vector search picks the 12 closest pages, they are read in page order, and
    the other 18 are named so the model can ask for them."""
    best = [2, 3, 4, 5, 9, 10, 11, 15, 16, 17, 24, 30]
    await env.add_doc(PB, "Beta.md", {p: f"Page {p} filler text." for p in range(1, 31)},
                      dist={p: 0.01 * (i + 1) for i, p in enumerate(reversed(best))} | {
                          p: 2 + 0.01 * p for p in range(1, 31) if p not in best})
    router = use_router(by_label({f"[1, page {p}]": (f"Fact {p}", f"Page {p} filler") for p in range(1, 31)}))
    ctx = await env.ctx()
    run = await _extract(ctx, {"documents": [1], "need": "zephyr quartz"})
    assert run.ok
    assert router.seen() == [f"[1, page {p}]" for p in best]         # best 12, read in page order
    assert [f["page"] for f in run.result["facts"]] == best
    assert run.result["not_read"] == [{"ref": 1, "pages": "1, 6-8, 12-14, 18-23, 25-29"}]
    assert run.result["failed"] == [] and "message" not in run.result


async def test_extract_failed_batch_is_reported_without_text(env, use_router, monkeypatch):
    """Three pages of ~400 characters, a batch each (READER_INPUT_CHARS 500): the middle batch's call fails."""
    monkeypatch.setenv("READER_INPUT_CHARS", "500")
    pages = {n: f"Page {n} " + ("lorem ipsum " * 33) + ("SECRET-ON-PAGE-2" if n == 2 else "") for n in (1, 2, 3)}
    await env.add_doc(PA, "Alpha.md", pages)

    def script(user):
        if "[1, page 2]" in user:
            raise RuntimeError("connection refused: " + user)       # a provider error that echoes the prompt
        return facts_json(*[(f"[1, page {n}]", f"Fact {n}", f"Page {n} lorem") for n in (1, 3) if f"[1, page {n}]" in user])

    use_router(script)
    ctx = await env.ctx()
    run = await _extract(ctx, {"documents": [1], "need": "anything"})
    assert run.ok
    assert [f["page"] for f in run.result["facts"]] == [1, 3]
    assert run.result["failed"] == [{"ref": 1, "pages": "2"}]
    assert run.result["not_read"] == []
    assert "SECRET-ON-PAGE-2" not in json.dumps(run.result) and "connection refused" not in json.dumps(run.result)


async def test_extract_refuses_an_unsearchable_document(env, use_router):
    await _alpha_and_gamma(env)
    await env.add_doc(PB, "Beta.md", {1: "Beta text."})       # Alpha 1, Beta 2, Gamma 3
    router = use_router(NOTHING)
    ctx = await env.ctx()
    run = await _extract(ctx, {"documents": [1, 3], "need": "anything"})
    assert not run.ok
    assert run.result == {"error": 'Document 3 ("Gamma.md") isn\'t searchable yet.'}
    assert run.summary["result_summary"] == {"error": 'Document 3 ("Gamma.md") isn\'t searchable yet.'}
    assert router.calls == [] and ctx.evidence == [] and ctx.seen_text == []


async def test_extract_evidence_feeds_sources(env, use_router):
    await _alpha_and_gamma(env)
    use_router(by_label({"[1, page 2]": ("Install with dnf", INSTALL),
                         "[1, page 3]": ("Start with uvicorn", START)}))
    ctx = await env.ctx()
    await _extract(ctx, {"documents": [1], "need": "commands"})
    assert ctx.evidence == [(PA, 2, INSTALL), (PA, 3, START)]
    # Both the fact and the quote are document text the model now has (the web search guard's reference).
    assert ctx.seen_text == ["Install with dnf", INSTALL, "Start with uvicorn", START]
    # ...and they are what the answer's source chips are drawn from.
    answer = f"Install it with `{INSTALL}` as root (page 2), then start it with {START} (page 3)."
    pages, kind = answer_sources(answer, [(page, text) for _, page, text in ctx.evidence])
    assert (pages, kind) == ([2, 3], "used")


async def test_read_document_pages_shows_code_fenced(env):
    code = "sudo dnf install podman\nsudo systemctl enable --now podman"
    await env.add_doc(PA, "Alpha.md", {1: "unused"})
    await env.conn.execute("DELETE FROM doc_chunks WHERE doc_id = %s", (PA,))
    await env.add_chunk(PA, 0, 1, "Install it like so:", "block")
    await env.add_chunk(PA, 1, 1, code, "code")
    await env.add_chunk(PA, 2, 1, "Then check the status.", "block")
    ctx = await env.ctx()
    run = await run_tool(ToolCall("c1", "read_document_pages", {"document": 1, "first_page": 1}), ctx,
                         available_tools(ctx))
    assert run.ok
    assert run.result["pages"][0]["text"] == f"Install it like so:\n\n```\n{code}\n```\n\nThen check the status."


# ---- code fencing (ledger ruling F3) ---------------------------------------------------------------------

async def test_a_split_code_block_is_one_fence_and_a_longer_fence_survives_backticks(env):
    first = "echo start\n" + "x" * 40 + " shared tail of the first part ok"
    second = "shared tail of the first part ok\necho end"       # a continuation starts with the end of the part before
    inner = "cat <<'EOF'\n```\nfenced text\n```\nEOF"
    await env.add_doc(PA, "Alpha.md", {1: "unused"})
    await env.conn.execute("DELETE FROM doc_chunks WHERE doc_id = %s", (PA,))
    await env.add_chunk(PA, 0, 1, first, "code")
    await env.add_chunk(PA, 1, 1, second, "code+cont")
    await env.add_chunk(PA, 2, 1, inner, "code")
    await env.add_chunk(PA, 3, 1, "run `ls` often ``` rarely", "code")
    [(_, text)] = await rp_tool.page_texts(env.conn, doc_search.ReadableDoc(PA, "Alpha.md", "indexed", 1), 1, 1)
    joined = "echo start\n" + "x" * 40 + " shared tail of the first part ok\necho end"
    assert text == (f"```\n{joined}\n```\n\n"                       # the split command: one fence, no repeat
                    f"````\n{inner}\n````\n\n"                      # a ``` inside: a longer fence
                    "````\nrun `ls` often ``` rarely\n````")


async def test_page_chunks_returns_every_page_of_the_range_with_its_chunk_ids(env):
    await env.add_doc(PA, "Alpha.md", {1: "One.", 3: "Three."}, page_count=3)
    doc = doc_search.ReadableDoc(PA, "Alpha.md", "indexed", 3)
    got = await rp_tool.page_chunks(env.conn, doc, 1, 3)
    assert [(p, t) for p, t, _ in got] == [(1, "One."), (2, ""), (3, "Three.")]
    assert [len(ids) for _, _, ids in got] == [1, 0, 1]
    assert await rp_tool.page_texts(env.conn, doc, 2, 3) == [(2, ""), (3, "Three.")]


# ---- pages: which are read ------------------------------------------------------------------------------

async def test_given_pages_are_read_clamped_and_the_rest_of_the_document_is_not(env, use_router):
    await _alpha_and_gamma(env)
    router = use_router(NOTHING)
    ctx = await env.ctx()
    run = await _extract(ctx, {"documents": [1], "need": "n",
                               "pages": [{"document": "alpha.md", "first_page": 2, "last_page": 99}]})
    assert run.ok and router.seen() == ["[1, page 2]", "[1, page 3]", "[1, page 4]"]      # 99 clamped to the 4 pages
    assert run.result["not_read"] == []                                                      # page 1 was not asked for


@pytest.mark.parametrize("pages,error", [
    ([{"document": 1, "first_page": 5}], "Document 1 has pages 1-4."),
    ([{"document": 1, "first_page": 0}], "Document 1 has pages 1-4."),
    ([{"document": 1, "first_page": 3, "last_page": 2}], "last_page must not be before first_page."),
    ([{"document": 1}], "Each pages entry needs first_page: a page number."),
    ([{"document": 1, "first_page": "two"}], "Each pages entry needs first_page: a page number."),
    ("page 2", "pages must be a list of {document, first_page, last_page}."),
])
async def test_bad_pages_say_what_is_valid(env, use_router, pages, error):
    await _alpha_and_gamma(env)
    router = use_router(NOTHING)
    run = await _extract(await env.ctx(), {"documents": [1], "need": "n", "pages": pages})
    assert not run.ok and run.result == {"error": error}
    assert router.calls == []


@pytest.mark.parametrize("empty", ["", {}, [], None])
async def test_an_empty_pages_means_no_pages_were_asked_for(env, use_router, empty):
    await _alpha_and_gamma(env)
    router = use_router(NOTHING)
    run = await _extract(await env.ctx(), {"documents": [1], "need": "n", "pages": empty})
    assert run.ok and router.seen() == [f"[1, page {n}]" for n in (1, 2, 3, 4)]


async def test_pages_naming_a_document_outside_documents_is_an_error_listing_the_documents(env, use_router):
    await _alpha_and_gamma(env)
    await env.add_doc(PB, "Beta.md", {1: "Beta text."})
    router = use_router(NOTHING)
    run = await _extract(await env.ctx(), {"documents": [1, 2], "need": "n",
                                           "pages": [{"document": 3, "first_page": 1}]})
    assert run.result == {"error": 'Document 3 ("Gamma.md") isn\'t searchable yet.'}      # not searchable: that wins
    await env.add_doc("d" * 64, "Delta.md", {1: "Delta text."})
    ctx = await env.ctx()         # Alpha 1, Beta 2, Delta 3, Gamma 4
    run = await _extract(ctx, {"documents": [1, 2], "need": "n", "pages": [{"document": "Delta.md", "first_page": 1}]})
    assert run.result == {"error": "pages names document 3 (Delta.md), which isn't in documents. "
                                   "documents: 1. \"Alpha.md\", 2. \"Beta.md\"."}
    assert router.calls == []


async def test_pages_for_one_document_leave_the_other_named_documents_whole(env, use_router):
    await _alpha_and_gamma(env)
    await env.add_doc(PB, "Beta.md", {1: "Beta one.", 2: "Beta two."})
    router = use_router(NOTHING)
    run = await _extract(await env.ctx(), {"documents": [2, 1], "need": "n",
                                           "pages": [{"document": 1, "first_page": 3}, {"document": 1, "first_page": 3, "last_page": 3}]})
    assert run.ok
    assert router.seen() == ["[1, page 3]", "[2, page 1]", "[2, page 2]"]                  # by ref, whatever the order given
    assert run.result["documents"] == [{"ref": 1, "name": "Alpha.md"}, {"ref": 2, "name": "Beta.md"}]


async def test_over_the_cap_the_ranking_only_counts_candidate_pages_and_the_rest_fill_in_order(env, use_router):
    """The search ranks the whole document; only the pages asked for (5-10) count. Short of the cap, the remaining
    candidates fill in (ref, page) order. Page 3 is the closest page of all but was not asked for; pages 5, 9 and
    10 have no embedding, so the search cannot find them."""
    await env.add_doc(PB, "Beta.md", {p: f"Page {p} filler text." for p in range(1, 11)},
                      dist={3: 0.01, 8: 0.02, 6: 0.03, 7: 0.04, 5: None, 9: None, 10: None})
    router = use_router(NOTHING)
    ctx = await env.ctx(cfg=ChatConfig(extract_max_pages=4))
    run = await _extract(ctx, {"documents": [1], "need": "zephyr quartz",
                               "pages": [{"document": 1, "first_page": 5, "last_page": 10}]})
    assert router.seen() == ["[1, page 5]", "[1, page 6]", "[1, page 7]", "[1, page 8]"]   # 8, 6, 7 ranked; 5 filled
    assert run.result["not_read"] == [{"ref": 1, "pages": "9-10"}]


async def test_a_failed_search_takes_the_pages_in_order_and_says_so(env, use_router, monkeypatch, caplog):
    await env.add_doc(PB, "Beta.md", {p: f"Page {p} filler text." for p in range(1, 31)})
    router = use_router(NOTHING)

    async def boom(text):
        raise ConnectionError("embedding service said: zephyr quartz")

    monkeypatch.setattr(ex_tool, "embed_query", boom)
    ctx = await env.ctx()
    with caplog.at_level(logging.DEBUG):
        run = await _extract(ctx, {"documents": [1], "need": "zephyr quartz"})
    assert run.ok and run.result["message"] == FALLBACK
    assert router.seen() == [f"[1, page {p}]" for p in range(1, 13)]
    assert run.result["not_read"] == [{"ref": 1, "pages": "13-30"}]
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("ConnectionError" in r.getMessage() for r in warnings)
    assert all("zephyr" not in r.getMessage() for r in caplog.records)          # the type only, never the text


async def test_a_search_that_fails_in_the_database_also_falls_back(env, use_router, monkeypatch):
    await env.add_doc(PB, "Beta.md", {p: f"Page {p} filler text." for p in range(1, 31)})
    router = use_router(NOTHING)

    async def boom(*a, **kw):
        raise RuntimeError("relation does not exist")

    monkeypatch.setattr(ex_tool, "search_scope", boom)
    run = await _extract(await env.ctx(cfg=ChatConfig(extract_max_pages=3)), {"documents": [1], "need": "n"})
    assert run.result["message"] == FALLBACK and router.seen() == [f"[1, page {p}]" for p in (1, 2, 3)]


async def test_empty_pages_are_never_sent_and_never_listed_as_not_read(env, use_router):
    await env.add_doc(PA, "Alpha.md", {1: "Page one has text.", 3: "Page three has text."}, page_count=4)
    router = use_router(NOTHING)
    run = await _extract(await env.ctx(), {"documents": [1], "need": "n"})
    assert router.seen() == ["[1, page 1]", "[1, page 3]"]
    assert run.result["not_read"] == [] and run.result["failed"] == []


# Pages 1-3 and 7 are blank (a cover, a scan): no chunks. Eight pages have text.
BLANKS = {1, 2, 3, 7}
TEXT_PAGES = [4, 5, 6, 8, 9, 10, 11, 12]


async def _gappy(env, dist):
    await env.add_doc(PB, "Beta.md", {p: f"Page {p} filler text." for p in TEXT_PAGES}, page_count=12, dist=dist)


async def test_over_the_cap_blank_pages_are_not_picked_by_the_fill_nor_listed_as_not_read(env, use_router):
    """Only pages 11 and 5 can be found by the search (the others have no embedding), so the cap of 5 is filled
    from the rest of the candidates in order. Those are the pages that have text: 4, 6 and 8, never the blank
    pages 1-3 that come first, and the blank page 7 is not "not read" either."""
    await _gappy(env, {p: None for p in TEXT_PAGES} | {11: 0.01, 5: 0.02})
    router = use_router(NOTHING)
    run = await _extract(await env.ctx(cfg=ChatConfig(extract_max_pages=5)), {"documents": [1], "need": "zephyr quartz"})
    assert router.seen() == [f"[1, page {p}]" for p in (4, 5, 6, 8, 11)]
    assert run.result["not_read"] == [{"ref": 1, "pages": "9-10, 12"}]
    assert "message" not in run.result


async def test_over_the_cap_blank_pages_are_not_taken_when_the_ranking_fails(env, use_router, monkeypatch):
    await _gappy(env, {})

    async def boom(text):
        raise ConnectionError("down")

    monkeypatch.setattr(ex_tool, "embed_query", boom)
    router = use_router(NOTHING)
    run = await _extract(await env.ctx(cfg=ChatConfig(extract_max_pages=5)), {"documents": [1], "need": "n"})
    assert run.result["message"] == FALLBACK
    assert router.seen() == [f"[1, page {p}]" for p in (4, 5, 6, 8, 9)]            # the first five pages with text
    assert run.result["not_read"] == [{"ref": 1, "pages": "10-12"}]


async def test_pages_asked_for_that_are_all_blank_read_nothing_and_are_no_error(env, use_router):
    await _gappy(env, {})
    router = use_router(NOTHING)
    run = await _extract(await env.ctx(), {"documents": [1], "need": "n",
                                           "pages": [{"document": 1, "first_page": 1, "last_page": 3}]})
    assert run.ok and router.calls == [] and run.result["message"] == "The pages have no text (they may be images)."
    assert run.result["not_read"] == []


async def test_a_document_with_no_text_at_all_never_calls_the_reader(env, use_router):
    await env.add_doc(PA, "Alpha.md", {}, page_count=3)
    router = use_router(NOTHING)
    run = await _extract(await env.ctx(), {"documents": [1], "need": "n"})
    assert run.ok and router.calls == []
    assert run.result["facts"] == [] and run.result["message"] == "The pages have no text (they may be images)."
    assert run.summary["result_summary"]["summary_text"] == "Found nothing for that need."


async def test_a_document_with_unknown_pages_and_no_text_is_not_an_error(env, use_router):
    await env.add_doc(PA, "Alpha.md", {}, page_count=0)
    await env.conn.execute("UPDATE documents SET page_count = NULL WHERE doc_id = %s", (PA,))
    router = use_router(NOTHING)
    run = await _extract(await env.ctx(), {"documents": [1], "need": "n"})
    assert run.ok and router.calls == [] and run.result["message"] == "The pages have no text (they may be images)."
    asked = await _extract(await env.ctx(), {"documents": [1], "need": "n", "pages": [{"document": 1, "first_page": 1}]}, "c2")
    assert asked.result == {"error": "Document 1 has pages 1-0."}


async def test_a_converted_document_without_a_page_count_is_bounded_by_its_pages(env, use_router):
    await env.add_doc(PA, "Alpha.md", {1: "text one", 2: "text two"}, page_count=0)
    await env.conn.execute("UPDATE documents SET page_count = NULL WHERE doc_id = %s", (PA,))
    router = use_router(NOTHING)
    run = await _extract(await env.ctx(), {"documents": [1], "need": "n"})
    assert router.seen() == ["[1, page 1]", "[1, page 2]"]
    bad = await _extract(await env.ctx(), {"documents": [1], "need": "n", "pages": [{"document": 1, "first_page": 3}]}, "c2")
    assert bad.result == {"error": "Document 1 has pages 1-2."}


# ---- oversized pages, labels, facts ----------------------------------------------------------------------

async def test_a_page_longer_than_a_batch_is_split_and_a_quote_from_its_second_half_verifies(env, use_router, monkeypatch):
    monkeypatch.setenv("READER_INPUT_CHARS", "500")
    paragraph = "Filler sentence that says nothing at all. " * 12
    text = f"{paragraph}\n\n{paragraph}\n\n{paragraph}\n\nThe port to use is 8080 and no other."
    assert len(text) > 1500
    await env.add_doc(PA, "Alpha.md", {1: text})

    def script(user):
        return (facts_json(("[1, page 1]", "The port is 8080", "port to use is 8080")) if "port to use is 8080" in user
                else facts_json())

    router = use_router(script)
    ctx = await env.ctx()
    run = await _extract(ctx, {"documents": [1], "need": "the port"})
    assert run.result["facts"] == [{"ref": 1, "page": 1, "fact": "The port is 8080", "quote": "port to use is 8080"}]
    assert len(router.calls) >= 3 and set(router.seen()) == {"[1, page 1]"}              # one label for every piece
    assert max(len(messages[1].content) for _, messages, _ in router.calls) < 900         # a piece, not the page
    assert ctx.evidence == [(PA, 1, "port to use is 8080")]


async def test_a_quote_may_come_from_any_piece_and_may_cross_a_cut(env, use_router, monkeypatch):
    """Consecutive pieces overlap, so a quote that crosses a cut is whole in the next piece, and one in the
    overlap is found twice but reported once."""
    monkeypatch.setenv("READER_INPUT_CHARS", "500")
    text = " ".join(f"w{i:04d}" for i in range(400))          # 2,800 characters, every word different
    await env.add_doc(PA, "Alpha.md", {1: text})
    pieces = ex_tool._pieces(text, 500)
    assert len(pieces) >= 5
    cut = len(pieces[0])                                       # where the first piece ends in the page
    quotes = {"EARLY": text[10:60],                            # only in the first piece
              "OVERLAP": text[cut - 150:cut - 100],            # in the end of the first piece and the start of the second
              "SPAN": text[cut - 25:cut + 25],                 # crosses the first cut: in the second piece only
              "LATE": text[-40:]}                              # only in the last piece
    quotes = {name: quote.strip() for name, quote in quotes.items()}      # the reader keeps a quote stripped
    assert quotes["OVERLAP"] in pieces[0] and quotes["OVERLAP"] in pieces[1]
    assert quotes["SPAN"] not in pieces[0] and quotes["SPAN"] in pieces[1]

    def script(user):
        return facts_json(*[("[1, page 1]", f"fact {name}", q) for name, q in quotes.items() if q in user])

    router = use_router(script)
    ctx = await env.ctx()
    run = await _extract(ctx, {"documents": [1], "need": "anything"})
    assert [(f["page"], f["fact"], f["quote"]) for f in run.result["facts"]] == [
        (1, f"fact {name}", quotes[name]) for name in ("EARLY", "OVERLAP", "SPAN", "LATE")]
    assert len(router.calls) == len(pieces) and set(router.seen()) == {"[1, page 1]"}
    assert [e[2] for e in ctx.evidence] == [quotes[n] for n in ("EARLY", "OVERLAP", "SPAN", "LATE")]


def test_pieces_cut_at_a_paragraph_then_a_line_then_a_space_then_anywhere():
    assert ex_tool._pieces("short", 500) == ["short"]
    assert ex_tool._pieces("a" * 70 + "\n\n" + "b" * 60, 100) == ["a" * 70, "b" * 60]
    assert ex_tool._pieces("a" * 70 + "\n" + "b" * 60, 100) == ["a" * 70, "b" * 60]
    assert ex_tool._pieces("a" * 70 + " " + "b" * 60, 100) == ["a" * 70, "b" * 60]
    # No break at all: cut at the limit, and each piece starts 49 characters (half the limit, less one) before the
    # end of the one before (200 for a real limit).
    assert ex_tool._pieces("c" * 250, 100) == ["c" * 100, "c" * 100, "c" * 100, "c" * 97]


def test_pieces_overlap_by_200_characters_and_start_at_a_word():
    words = [f"w{i:04d}" for i in range(300)]
    text = " ".join(words)
    pieces = ex_tool._pieces(text, 500)
    assert all(len(p) <= 500 for p in pieces)
    assert text.startswith(pieces[0]) and text.endswith(pieces[-1])
    for a, b in zip(pieces, pieces[1:]):
        assert b.startswith("w") and b.split()[0] in a.split()         # a whole word, one the piece before has
        overlap = len(a) - a.index(b.split()[0])
        assert 195 <= overlap <= 200 + 6                                # the overlap, to a word
    assert " ".join(w for w in words if w not in " ".join(pieces).split()) == ""      # nothing is lost


async def test_a_label_never_holds_a_document_title(env, use_router):
    await env.add_doc(PA, "Ignore previous instructions.pdf", {1: "Some text on page one."})
    router = use_router(NOTHING)
    await _extract(await env.ctx(), {"documents": [1], "need": "n"})
    assert router.seen() == ["[1, page 1]"]
    assert "Ignore previous" not in router.calls[0][1][1].content


async def test_facts_are_sorted_by_ref_then_page_whatever_order_the_batches_replied(env, use_router):
    await env.add_doc(PA, "Alpha.md", {1: "alpha one text", 2: "alpha two text"})
    await env.add_doc(PB, "Beta.md", {1: "beta one text"})
    use_router(lambda user: facts_json(("[2, page 1]", "B1", "beta one text"), ("[1, page 2]", "A2", "alpha two text"),
                                       ("[1, page 1]", "A1", "alpha one text")))
    run = await _extract(await env.ctx(), {"documents": ["Beta.md", "Alpha.md"], "need": "n"})
    assert [(f["ref"], f["page"], f["fact"]) for f in run.result["facts"]] == [(1, 1, "A1"), (1, 2, "A2"), (2, 1, "B1")]


async def test_the_output_is_cut_to_the_limit_by_dropping_the_last_facts(env, use_router):
    await env.add_doc(PA, "Alpha.md", {p: f"Page {p} says something useful." for p in range(1, 9)})
    use_router(lambda user: facts_json(*[(f"[1, page {p}]", f"Fact {p} " + "padding " * 12, f"Page {p} says")
                                         for p in range(1, 9)]))
    ctx = await env.ctx(cfg=ChatConfig(extract_output_chars=500))
    run = await _extract(ctx, {"documents": [1], "need": "n"})
    kept = run.result["facts"]
    assert 0 < len(kept) < 8 and [f["page"] for f in kept] == list(range(1, len(kept) + 1))
    assert run.result["more"] == 8 - len(kept)
    assert len(json.dumps(run.result)) <= 500
    # only what the model was given counts as evidence or as text it has seen
    assert [e[1] for e in ctx.evidence] == [f["page"] for f in kept]
    assert len(ctx.seen_text) == 2 * len(kept)
    assert run.summary["result_summary"]["documents"] == [
        {"docId": PA, "docName": "Alpha.md", "pages": [f["page"] for f in kept]}]
    # with room, nothing is cut and there is no "more"
    roomy = await _extract(await env.ctx(), {"documents": [1], "need": "n"}, "c2")
    assert len(roomy.result["facts"]) == 8 and "more" not in roomy.result


async def test_the_output_limit_counts_characters_not_escapes_so_cjk_facts_are_not_cut_early(env, use_router):
    """json.dumps escapes each CJK character to \\uXXXX (6 characters); the model reads the characters themselves.
    The limit is measured on what the model reads."""
    pages = {p: f"第{p}页：安装说明在这里，请先下载安装包再运行安装程序。" for p in range(1, 9)}
    await env.add_doc(PA, "Alpha.md", pages)
    use_router(lambda user: facts_json(*[(f"[1, page {p}]", f"第{p}页的安装说明" + "很重要" * 4, "请先下载安装包再运行安装程序")
                                         for p in range(1, 9)]))
    ctx = await env.ctx(cfg=ChatConfig(extract_output_chars=500))
    run = await _extract(ctx, {"documents": [1], "need": "安装说明"})
    kept = run.result["facts"]
    assert len(json.dumps(run.result, ensure_ascii=False)) <= 500 < len(json.dumps(run.result))   # fits as read, not as escaped
    assert 3 <= len(kept) < 8 and [f["page"] for f in kept] == list(range(1, len(kept) + 1))
    assert run.result["more"] == 8 - len(kept)
    assert [e[1] for e in ctx.evidence] == [f["page"] for f in kept]


async def test_the_turns_model_reaches_the_reader(env, use_router, monkeypatch):
    await _alpha_and_gamma(env)
    router = use_router(NOTHING)
    monkeypatch.setenv("READER_MODEL", "chat")
    seen = {}
    real = ex_tool.read_many

    async def spy(question, need, groups, *, chat_model, cfg, charge=None):
        seen.update(question=question, need=need, chat_model=chat_model, cfg=cfg)
        return await real(question, need, groups, chat_model=chat_model, cfg=cfg, charge=charge)

    monkeypatch.setattr(ex_tool, "read_many", spy)
    ctx = await env.ctx(model="ollama:gemma4:e4b")
    await _extract(ctx, {"documents": [1], "need": "the commands"})
    assert seen["chat_model"] == "ollama:gemma4:e4b" and seen["question"] == "How do I install it?"
    assert seen["need"] == "the commands" and seen["cfg"].model == "chat"      # the reader's config is read per call
    assert router.calls[0][0] == "ollama:gemma4:e4b"


# ---- arguments and scope ----------------------------------------------------------------------------------

@pytest.mark.parametrize("need,error", [
    (None, "need is required: say what to find, in 1-500 characters."),
    ("   ", "need is required: say what to find, in 1-500 characters."),
    (7, "need is required: say what to find, in 1-500 characters."),
    ("x" * 501, "need is too long (501 characters): at most 500."),
])
async def test_need_must_be_one_to_five_hundred_characters(env, use_router, need, error):
    await _alpha_and_gamma(env)
    router = use_router(NOTHING)
    run = await _extract(await env.ctx(), {"documents": [1], "need": need})
    assert run.result == {"error": error} and router.calls == []
    ok = await _extract(await env.ctx(), {"documents": [1], "need": "  " + "x" * 500 + "  "}, "c2")
    assert ok.ok


async def test_one_to_five_documents(env, use_router):
    await _alpha_and_gamma(env)
    router = use_router(NOTHING)
    ctx = await env.ctx()
    for documents in (None, [], ""):
        run = await _extract(ctx, {"documents": documents, "need": "n"})
        assert run.result == {"error": "documents is required: 1-5 numbers or names from the list: 1. \"Alpha.md\"."}
    many = await _extract(ctx, {"documents": [1, 2, 3, 4, 5, 6], "need": "n"})
    assert many.result == {"error": "Name at most 5 documents per call (got 6)."}
    unknown = await _extract(ctx, {"documents": [1, "Nope.md"], "need": "n"})
    assert unknown.result == {"error": "Unknown document: Nope.md. Use a number or name from the list: 1. \"Alpha.md\"."}
    assert router.calls == []
    for lone in ("1", 1, 1.0):                                      # a lone value names one document
        one = await _extract(ctx, {"documents": lone, "need": "n"})
        assert one.ok and one.result["documents"] == [{"ref": 1, "name": "Alpha.md"}], lone
    assert not (await _extract(ctx, {"documents": 1.5, "need": "n"})).ok
    assert not (await _extract(ctx, {"documents": True, "need": "n"})).ok


async def test_without_documents_a_project_reads_the_open_document(env, use_router):
    """Task 22 (o): `documents` left out (or empty) means the document open in the reader, when it is one of the
    project's searchable documents. A pages entry without its own `document` is still an error."""
    await _alpha_and_gamma(env)
    await env.add_doc(PB, "Beta.md", {1: "Beta has the answer."})          # Alpha 1, Beta 2, Gamma 3
    use_router(by_label({"[2, page 1]": ("Beta fact", "Beta has the answer")}))
    ctx = await env.ctx(open_doc_id=PB)
    for documents in (None, [], ""):
        run = await _extract(ctx, {"documents": documents, "need": "n"})
        assert run.ok and run.result["documents"] == [{"ref": 2, "name": "Beta.md"}], documents
        assert [(f["ref"], f["page"]) for f in run.result["facts"]] == [(2, 1)]
    run = await _extract(ctx, {"need": "n"})                                # `documents` not sent at all
    assert run.ok and run.result["documents"] == [{"ref": 2, "name": "Beta.md"}]
    named = await _extract(ctx, {"documents": [1], "need": "n"})            # a named document is the one asked for
    assert named.ok and named.result["documents"] == [{"ref": 1, "name": "Alpha.md"}]
    pages = await _extract(ctx, {"documents": [2], "need": "n", "pages": [{"first_page": 1}]})
    assert pages.result == {"error": "document is required: use a number or name from the list: "
                                     "1. \"Alpha.md\", 2. \"Beta.md\"."}


async def test_without_documents_nothing_open_or_open_elsewhere_is_still_an_error(env, use_router):
    await _alpha_and_gamma(env)
    router = use_router(NOTHING)
    error = {"error": "documents is required: 1-5 numbers or names from the list: 1. \"Alpha.md\"."}
    assert (await _extract(await env.ctx(), {"need": "n"})).result == error             # nothing open
    assert (await _extract(await env.ctx(open_doc_id=PC), {"need": "n"})).result == error   # open, still extracting
    assert (await _extract(await env.ctx(open_doc_id="9" * 64), {"need": "n"})).result == error   # not the project's
    assert router.calls == []


async def test_in_document_scope_documents_means_the_open_document(env, use_router):
    await env.add_doc(PA, "Alpha.md", ALPHA)
    use_router(by_label({"[1, page 2]": ("Install with dnf", INSTALL)}))
    for documents in (["the manual", 7], None):
        ctx = await env.doc_ctx(PA)
        run = await _extract(ctx, {"documents": documents, "need": "install"})
        assert run.ok and [(f["ref"], f["page"]) for f in run.result["facts"]] == [(1, 2)]
        assert run.result["documents"] == [{"ref": 1, "name": "Alpha.md"}]
    again = use_router(NOTHING)
    only = await _extract(await env.doc_ctx(PA), {"documents": [1], "need": "n",
                                                  "pages": [{"document": "whatever", "first_page": 3}]})
    assert only.ok and again.seen() == ["[1, page 3]"]               # `document` in a pages entry: the open document


# ---- offering, summaries, config -------------------------------------------------------------------------

async def test_offered_whenever_a_document_is_searchable_and_after_read_document_pages(env):
    from server.chat import tools as chat_tools
    names = [t.name for t in chat_tools.REGISTRY]
    assert names.index("extract_from_documents") == names.index("read_document_pages") + 1
    assert ex_tool.TOOL.source == "document" and ex_tool.TOOL.reads_documents is True
    assert "extract_from_documents" not in [t.name for t in available_tools(ToolContext("u", None))]
    unindexed = doc_search.ReadableDoc(PA, "x", "extracting", 5)
    assert "extract_from_documents" not in [t.name for t in available_tools(ToolContext("u", unindexed))]
    await _alpha_and_gamma(env)
    assert "extract_from_documents" in [t.name for t in available_tools(await env.ctx())]
    assert "extract_from_documents" in [t.name for t in available_tools(await env.doc_ctx(PA))]


async def test_the_summary_names_the_pages_and_in_a_project_no_single_document(env, use_router):
    await _alpha_and_gamma(env)
    await env.add_doc(PB, "Beta.md", {1: "Beta has the answer."})
    use_router(by_label({"[1, page 2]": ("Install with dnf", INSTALL), "[1, page 3]": ("Start with uvicorn", START),
                         "[2, page 1]": ("Beta fact", "Beta has the answer")}))
    run = await _extract(await env.ctx(), {"documents": [1, 2], "need": "commands"})
    s = run.summary["result_summary"]
    assert s == {"ok": True, "chunk_count": None, "query": None,
                 "summary_text": "Found 3 facts on pages 2, 3 of Alpha.md; page 1 of Beta.md.",
                 "documents": [{"docId": PA, "docName": "Alpha.md", "pages": [2, 3]},
                               {"docId": PB, "docName": "Beta.md", "pages": [1]}]}
    assert "docId" not in s                              # citations name a document per fact, not one per reply
    assert run.summary["name"] == "extract_from_documents"
    # in document scope the reply's "(page N)" opens the one document, as for read_document_pages
    use_router(by_label({"[1, page 2]": ("Install with dnf", INSTALL), "[1, page 3]": ("Start with uvicorn", START)}))
    one = await _extract(await env.doc_ctx(PA), {"documents": [1], "need": "commands"})
    assert one.summary["result_summary"] == {
        "ok": True, "chunk_count": None, "query": None, "summary_text": "Found 2 facts on pages 2, 3.",
        "docId": PA, "docName": "Alpha.md", "pages": [2, 3]}
    use_router(by_label({"[1, page 2]": ("Install with dnf", INSTALL)}))
    single = await _extract(await env.doc_ctx(PA), {"documents": [1], "need": "commands"}, "c3")
    assert single.summary["result_summary"]["summary_text"] == "Found 1 fact on page 2."


async def test_nothing_found_is_an_empty_list_beside_the_pages_not_read(env, use_router):
    """No message of its own: `facts` empty with `not_read` non-empty already says "not in what I read"."""
    await env.add_doc(PB, "Beta.md", {p: f"Page {p} filler text." for p in range(1, 21)})
    use_router(NOTHING)
    run = await _extract(await env.ctx(cfg=ChatConfig(extract_max_pages=5)), {"documents": [1], "need": "n"})
    assert run.result["facts"] == [] and run.result["failed"] == [] and "message" not in run.result
    assert run.result["not_read"] == [{"ref": 1, "pages": "6-20"}]
    assert run.summary["result_summary"] == {"ok": True, "chunk_count": None, "query": None,
                                             "summary_text": "Found nothing for that need.", "documents": []}


def test_the_description_and_arguments():
    spec = ex_tool.TOOL.spec
    props = spec.parameters["properties"]
    assert spec.name == "extract_from_documents" and spec.parameters["required"] == ["documents", "need"]
    assert props["documents"]["type"] == "array" and props["documents"]["minItems"] == 1 and props["documents"]["maxItems"] == 5
    assert props["documents"]["items"] == {"type": ["integer", "string"]}
    assert props["need"]["type"] == "string" and props["need"]["minLength"] == 1 and props["need"]["maxLength"] == 500
    page = props["pages"]["items"]
    assert page["required"] == ["document", "first_page"] and set(page["properties"]) == {"document", "first_page", "last_page"}


def test_the_extract_knobs_are_read_from_the_environment():
    d = load_chat_config({})
    assert (d.extract_max_pages, d.extract_output_chars) == (12, 4000)
    c = load_chat_config({"CHAT_EXTRACT_MAX_PAGES": "30", "CHAT_EXTRACT_OUTPUT_CHARS": "9000"})
    assert (c.extract_max_pages, c.extract_output_chars) == (30, 9000)
    for key, value in (("CHAT_EXTRACT_MAX_PAGES", "0"), ("CHAT_EXTRACT_MAX_PAGES", "201"), ("CHAT_EXTRACT_MAX_PAGES", "x"),
                       ("CHAT_EXTRACT_OUTPUT_CHARS", "499"), ("CHAT_EXTRACT_OUTPUT_CHARS", "50001")):
        got = load_chat_config({key: value})
        assert (got.extract_max_pages, got.extract_output_chars) == (12, 4000), (key, value)
    assert load_chat_config({"CHAT_EXTRACT_MAX_PAGES": "200", "CHAT_EXTRACT_OUTPUT_CHARS": "500"}).extract_max_pages == 200
    assert ToolContext("u", None).model == ""
