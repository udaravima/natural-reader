"""v2.3 Task D: several tool rounds per answer, with guard-rails: a round cap
whose last results say so, a per-turn result budget, repeated calls not run
twice, and no document text sent to the web (plan Review focus 3 and 4)."""
from __future__ import annotations

import json

import pytest

from server.chat import context as ctx_mod
from server.chat.config import ChatConfig, load_chat_config
from server.chat.context import Prefetch
from server.chat import orchestrator
from server.chat.tools import ToolContext, TurnTools, available_tools
from server.chat.tools import search_documents as sd_tool
from server.chat.tools import web_search as ws_tool
from server.llm.types import Finish, ToolCall, ToolCallReady, Usage
from server.services.doc_search import ReadableDoc
from server.tests.chat_harness import FakeRouter, member, reply
from server.tests.test_chat_orchestrator import _msg, _run, conn, open_doc  # noqa: F401 — fixtures

DOC = ReadableDoc("d" * 64, "Thesis.pdf", "indexed", 40)
CORPUS = {   # query -> the passages a search for it finds
    "chapter 3 method": [{"id": 21, "page": 18, "chunk_type": "page", "score": 0.8,
                          "text": "The method in chapter 3 uses the MIMIC-IV dataset."}],
    "MIMIC-IV collected by": [{"id": 22, "page": 22, "chunk_type": "page", "score": 0.75,
                               "text": "MIMIC-IV was collected at Beth Israel Deaconess Medical Center."}],
}


def _call(cid, query, name="search_documents"):
    return [ToolCallReady(ToolCall(cid, name, {"query": query})), Usage(5, 5), Finish("tool_calls")]


@pytest.fixture
def corpus(open_doc, monkeypatch):
    """search_documents answers from CORPUS; counts the searches run."""
    runs = []

    async def fake_embed_one(text):
        return [text]

    async def fake_search_chunks(conn, doc_id, qvec, k, text=None):
        runs.append(qvec[0])
        return [dict(r) for r in CORPUS.get(qvec[0], [])]

    monkeypatch.setattr(sd_tool, "embed_query", fake_embed_one)
    monkeypatch.setattr(sd_tool, "search_chunks", fake_search_chunks)
    monkeypatch.setattr(ws_tool, "web_search", _fake_web())
    return runs


def _fake_web(sent=None):
    """A stand-in SearXNG that records every query it is sent."""
    async def fake(query, count):
        if sent is not None:
            sent.append(query)
        return {"query": query, "results": [{"title": "t", "url": "https://example.com", "summary": "s"}]}
    return fake


def _tool_results(router_call):
    return [json.loads(m.content) for m in router_call["messages"] if m.role == "tool"]


def test_three_rounds_by_default_and_the_budget_is_configurable():
    assert ChatConfig().max_tool_rounds == 3 == load_chat_config({}).max_tool_rounds
    assert load_chat_config({}).tool_result_budget_chars == 24000
    assert load_chat_config({"CHAT_TOOL_RESULT_BUDGET_CHARS": "5000"}).tool_result_budget_chars == 5000
    assert load_chat_config({"CHAT_TOOL_RESULT_BUDGET_CHARS": "10"}).tool_result_budget_chars == 24000


async def test_j2_a_two_hop_trail_is_followed_and_both_pages_cited(conn, corpus):
    router = FakeRouter(steps=[
        _call("c1", "chapter 3 method"),
        _call("c2", "MIMIC-IV collected by"),
        reply("It uses MIMIC-IV (page 18), collected at Beth Israel (page 22).")])
    events, claim = await _run(conn, router, text="Which dataset does chapter 3 use, and who collected it?")
    assert corpus == ["chapter 3 method", "MIMIC-IV collected by"]
    assert [e["round"] for e in events if e["type"] == "tool-input-available"] == [1, 2]
    # Rounds remained, so every step offered the tools and no result said "last".
    assert all(c["tools"] for c in router.calls)
    assert all("note" not in r for r in _tool_results(router.calls[2]))
    second = _tool_results(router.calls[2])[1]
    assert second["passages"][0]["page"] == 22
    status, finish, content, _, tool_calls, _, stats = await _msg(conn, claim.turn_id)
    assert (status, finish) == ("complete", "stop")
    assert "(page 18)" in content and "(page 22)" in content
    assert [t["arguments"]["query"] for t in tool_calls] == ["chapter 3 method", "MIMIC-IV collected by"]
    assert stats["steps"] == 3


async def test_the_capped_round_says_so_and_the_final_step_has_no_tools(conn, corpus):
    router = FakeRouter(steps=[_call("c1", "a"), _call("c2", "b"), _call("c3", "c"), reply("Done.")])
    await _run(conn, router, text="q")
    assert [bool(c["tools"]) for c in router.calls] == [True, True, True, False]
    last = _tool_results(router.calls[3])
    assert last[-1]["note"] == orchestrator.LAST_ROUND_NOTE
    assert all("note" not in r for r in last[:-1])


async def test_a_four_round_turn_saves_every_call_in_order(conn, corpus):
    router = FakeRouter(steps=[_call(f"c{i}", q) for i, q in enumerate("abcd", 1)] + [reply("Done.")])
    events, claim = await _run(conn, router, cfg=ChatConfig(max_tool_rounds=4), text="q")
    assert [e["round"] for e in events if e["type"] == "tool-input-available"] == [1, 2, 3, 4]
    tool_calls = (await _msg(conn, claim.turn_id))[4]
    assert [t["arguments"]["query"] for t in tool_calls] == list("abcd")


async def test_the_daily_budget_is_checked_between_rounds(conn, corpus):
    alice = await member(conn, "alice")
    await conn.execute("UPDATE users SET inference_daily_token_budget = 25 WHERE id = %s", (alice.user_id,))
    router = FakeRouter(steps=[_call("c1", "a"), _call("c2", "b"), _call("c3", "c"), reply("Done.")])
    events, _ = await _run(conn, router, user=alice, text="q")
    # 10 tokens a step: after 3 steps 30 >= 25, so the 4th never starts.
    assert len(router.calls) == 3
    assert (events[-1]["type"], events[-1]["code"]) == ("error", "budget_exhausted")


async def test_a_repeated_call_is_not_run_again(conn, corpus):
    router = FakeRouter(steps=[_call("c1", "chapter 3 method"), _call("c2", "chapter 3 method"), reply("Done.")])
    events, claim = await _run(conn, router, text="q")
    assert corpus == ["chapter 3 method"]
    assert _tool_results(router.calls[2])[1] == {"message": TurnTools.REPEATED}
    outputs = [e for e in events if e["type"] == "tool-output-available"]
    assert outputs[1]["output"]["summary_text"] == TurnTools.REPEATED


async def test_the_result_budget_refuses_what_would_exceed_it(conn, corpus, monkeypatch):
    long = "x" * 900
    monkeypatch.setitem(CORPUS, "long", [{"id": 31, "page": 5, "chunk_type": "page", "score": 0.9, "text": long}])
    router = FakeRouter(steps=[_call("c1", "chapter 3 method"), _call("c2", "long"), reply("Done.")])
    await _run(conn, router, cfg=ChatConfig(tool_result_budget_chars=600), text="q")
    first, second = _tool_results(router.calls[2])
    assert first["passages"][0]["page"] == 18
    assert second == {"message": TurnTools.BUDGET_USED}
    assert long not in json.dumps([m.content for m in router.calls[2]["messages"]])


async def test_a_refused_result_leaves_nothing_marked_as_shown():
    """The budget discards a result after running it: its passages were never
    seen, so a later search must not call them "already shown"."""
    ctx = ToolContext("u", DOC, cfg=ChatConfig(tool_result_budget_chars=10))

    class Big:
        name = "big"
        spec = None

        def available(self, c):
            return True

        async def execute(self, args, c):
            c.shown.add(99)
            c.seen_text.append("text")
            return {"text": "y" * 100}

        def summarize(self, args, result, c):
            return {"ok": True}

    tools = TurnTools(ctx)
    run = await tools.run(ToolCall("c1", "big", {}), [Big()])
    assert run.result == {"message": TurnTools.BUDGET_USED}
    assert ctx.shown == set() and ctx.seen_text == []


# ---- the web search guard (Review focus 4) ----

PASSAGE = "The committee approved the budget for the new hospital wing in March after a long debate."


@pytest.mark.parametrize("query,refused", [
    ("committee approved the budget for the new hospital wing in March", True),
    ("COMMITTEE, approved the budget: for the new hospital wing!", True),     # case and punctuation
    ("hospital wing budget approval news", False),                          # the topic
    ("the budget for the new hospital", False),                              # 6 words: not a run of 8
    ("x" * 301, True),
])
async def test_web_search_refuses_document_text_and_allows_the_topic(query, refused, monkeypatch):
    monkeypatch.setattr(ws_tool, "web_search", _fake_web())
    ctx = ToolContext("u", DOC)
    ctx.seen_text.append(PASSAGE)
    result = await ws_tool.TOOL.execute({"query": query}, ctx)
    if refused:
        assert "error" in result and "not text from the document" in result["error"]
    else:
        assert "error" not in result


async def test_every_kind_of_document_text_is_guarded(conn, open_doc, monkeypatch):
    """The prefetch block, pins and passages from an earlier round are all
    document text the model has; none may reach the web."""
    pinned = "Pinned: the quarterly revenue of the northern division fell by twelve percent overall."

    async def fake_prefetch(doc, question, cfg):
        return Prefetch([{"id": 1, "page": 2, "score": 0.9, "text": PASSAGE}], 0.9,
                        {"kind": "prefetch", "docId": doc.doc_id, "docName": doc.name, "count": 1,
                         "topScore": 0.9, "pages": [2]})

    async def fake_pins(session_id, exclude):
        return [{"fileName": "Thesis.pdf", "kind": "page", "page": 3, "text": pinned}], []

    sent = []
    monkeypatch.setattr(ctx_mod, "prefetch", fake_prefetch)
    monkeypatch.setattr(orchestrator.store, "load_turn_context", fake_pins)
    monkeypatch.setattr(ws_tool, "web_search", _fake_web(sent))
    router = FakeRouter(steps=[
        _call("c1", "committee approved the budget for the new hospital wing in March", "web_search"),
        _call("c2", "quarterly revenue of the northern division fell by twelve percent", "web_search"),
        _call("c3", "hospital wing budget", "web_search"),
        reply("Done.")])
    await _run(conn, router, text="q")
    results = _tool_results(router.calls[3])
    assert "error" in results[0] and "error" in results[1] and "error" not in results[2]
    assert sent == ["hospital wing budget"]


async def test_passages_from_an_earlier_round_are_guarded(conn, corpus):
    ctx = ToolContext("u", DOC)
    tools = TurnTools(ctx)
    offered = available_tools(ctx)
    await tools.run(ToolCall("c1", "search_documents", {"query": "MIMIC-IV collected by"}), offered)
    run = await tools.run(ToolCall("c2", "web_search", {
        "query": "MIMIC-IV was collected at Beth Israel Deaconess Medical Center"}), offered)
    assert "not text from the document" in run.result["error"]


# ---- Task D review fix round 1 ----

CJK_PASSAGE = "委员会在三月经过长时间辩论后批准了新医院大楼的预算。"


@pytest.mark.parametrize("query,refused", [
    ("委员会在三月经过长时间辩论后批准了新医院大楼的预算", True),       # copied: no spaces to count words by
    ("长时间辩论后批准了新医院大楼", True),                              # 14 characters of it
    ("新医院大楼 预算", False),                                           # the topic
    ("ความเห็นของคณะกรรมการเกี่ยวกับงบประมาณโรงพยาบาลแห่งใหม่", True),  # Thai, copied
])
async def test_the_guard_works_for_scripts_written_without_spaces(query, refused, monkeypatch):
    """Review I1: \\w+ reads a whole line of Chinese or Thai as one word."""
    monkeypatch.setattr(ws_tool, "web_search", _fake_web())
    ctx = ToolContext("u", DOC)
    ctx.seen_text.extend([CJK_PASSAGE, "ความเห็นของคณะกรรมการเกี่ยวกับงบประมาณโรงพยาบาลแห่งใหม่ถูกอนุมัติแล้ว"])
    result = await ws_tool.TOOL.execute({"query": query}, ctx)
    assert ("error" in result) == refused


async def test_quotes_in_the_models_earlier_answers_are_guarded(conn, open_doc, monkeypatch):
    """Review I2: the model's own earlier answers in this chat often quote the
    document; their text is document text too. The user's own words are not."""
    from server.chat.store import StoredMessage
    answer = "The report says: the quarterly revenue of the northern division fell by twelve percent."
    asked = "Please search the web for the latest quarterly revenue report news from the northern division"

    async def fake_context(session_id, exclude):
        return [], [StoredMessage("m1", "user", asked, ()), StoredMessage("m2", "assistant", answer, ())]

    sent = []
    monkeypatch.setattr(orchestrator.store, "load_turn_context", fake_context)
    monkeypatch.setattr(ws_tool, "web_search", _fake_web(sent))
    router = FakeRouter(steps=[
        _call("c1", "quarterly revenue of the northern division fell by twelve percent", "web_search"),
        _call("c2", "latest quarterly revenue report news from the northern division", "web_search"),
        reply("Done.")])
    await _run(conn, router, text="q")
    first, second = _tool_results(router.calls[2])
    assert "error" in first and "error" not in second
    assert sent == ["latest quarterly revenue report news from the northern division"]


async def test_a_result_too_big_for_the_budget_is_not_run_again(conn, corpus, monkeypatch):
    """Review M2: it would not fit the second time either."""
    monkeypatch.setitem(CORPUS, "long", [{"id": 31, "page": 5, "chunk_type": "page", "score": 0.9,
                                          "text": "x" * 900}])
    router = FakeRouter(steps=[_call("c1", "long"), _call("c2", "long"), reply("Done.")])
    await _run(conn, router, cfg=ChatConfig(tool_result_budget_chars=600), text="q")
    assert corpus == ["long"]
    assert _tool_results(router.calls[2]) == [{"message": TurnTools.BUDGET_USED}] * 2


async def test_a_call_that_failed_can_be_retried(conn, corpus, monkeypatch):
    """Review M3: "Already searched: see the results above" is wrong when the
    result above is an error."""
    calls = []

    async def flaky(query, count):
        calls.append(query)
        if len(calls) == 1:
            raise RuntimeError("searxng down")
        return {"query": query, "results": []}

    monkeypatch.setattr(ws_tool, "web_search", flaky)
    router = FakeRouter(steps=[_call("c1", "news", "web_search"), _call("c2", "news", "web_search"), reply("Done.")])
    await _run(conn, router, text="q")
    assert calls == ["news", "news"]
