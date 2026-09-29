import asyncio
import dataclasses
import json
import time

import pytest

from server.chat import context as ctx_mod
from server.chat import orchestrator, store
from server.chat.config import ChatConfig
from server.chat.context import Prefetch
from server.chat.orchestrator import TurnRequest, run_turn
from server.chat.tools import search_documents as sd_tool
from server.chat.tools import web_search as ws_tool
from server.services.doc_search import ReadableDoc
from server.llm.types import (CallSettings, Capabilities, FeatureDropped, Finish, ProviderError,
                              ProviderTimeout, ProviderUnavailable, ReasoningDelta, TextDelta,
                              ToolCall, ToolCallReady, Usage)
from server.tests.chat_harness import FakeRouter, assert_fixture, member, reply, shim_pool


@pytest.fixture
def conn(db_conn, monkeypatch):
    shim_pool(monkeypatch, db_conn, store, orchestrator, ctx_mod, sd_tool)
    return db_conn


async def fake_web_search(query, count):
    return {"query": query, "results": [{"title": "t", "url": "https://example.com", "summary": "s"}]}


async def _start(user, text="Hi", sid="s-1"):
    claim = await store.begin_turn(session_id=sid, user_id=user.user_id, model_id="ollama:m", text=text,
                                   attachments=[], new_session_pins=None)
    req = TurnRequest(user_id=user.user_id, session_id=sid, model_id="ollama:m", text=text, attachments=(),
                      settings=CallSettings(), doc_id=None, timezone="UTC")
    return claim, req


async def _run(conn, router, user=None, cfg=ChatConfig(), budget=None, text="Hi"):
    user = user or await member(conn, "alice")
    claim, req = await _start(user, text=text)
    events = [e async for e in run_turn(req, claim, router=router, cfg=cfg, deployment_budget=budget)]
    return events, claim


async def _one(conn, sql, params=()):
    cur = await conn.execute(sql, params)
    return await cur.fetchone()


async def _msg(conn, mid):
    return await _one(conn, "SELECT status, finish_reason, content, thinking, tool_calls, doc_context, stats "
                            "FROM chat_messages WHERE id=%s", (mid,))


async def _claim(conn):
    row = await _one(conn, "SELECT active_turn_id FROM chat_sessions WHERE id='s-1'")
    return row[0] if row else None


async def _log(conn):
    cur = await conn.execute("SELECT kind FROM chat_events WHERE session_id='s-1' ORDER BY id")
    return [r[0] for r in await cur.fetchall()]


def types(events):
    return [e["type"] for e in events]


async def test_plain_reply_streams_saves_and_releases(conn):
    router = FakeRouter(steps=[[TextDelta("Hello "), TextDelta("there."), Usage(10, 3), Finish("stop")]])
    alice = await member(conn, "alice")
    events, claim = await _run(conn, router, user=alice)
    assert types(events) == ["start", "start-step", "text-start", "text-delta", "text-delta", "text-end",
                             "finish-step", "finish"]
    assert [e["seq"] for e in events] == list(range(1, 9))
    assert events[0]["messageId"] == claim.turn_id and events[0]["userMessageId"] == claim.user_message_id
    assert events[-1]["finishReason"] == "stop"
    assert events[-1]["usage"] == {"promptTokens": 10, "completionTokens": 3, "estimated": False}
    status, reason, content, _, _, _, stats = await _msg(conn, claim.turn_id)
    assert (status, reason, content) == ("complete", "stop", "Hello there.")
    assert (stats["model"], stats["evalCount"], stats["doneReason"]) == ("ollama:m", 3, "stop")
    assert await _claim(conn) is None
    assert await _one(conn, "SELECT prompt_tokens, eval_tokens FROM inference_usage WHERE user_id=%s",
                      (alice.user_id,)) == (10, 3)
    sent = router.calls[0]
    assert sent["tools"] == ["web_search"]                  # no open document: no search_documents
    # v2.3: the rules are the one leading system message, and name only the
    # tool offered; the volatile block (just the time line) leads the user message.
    system, only = sent["messages"]
    assert system.role == "system" and "web_search" in system.content and "search_documents" not in system.content
    assert only.role == "user" and only.content.startswith("Current time: ") and only.content.endswith("\n\nHi")
    assert await _log(conn) == ["sent", "received"]
    assert_fixture("plain", events)


async def test_reasoning_closes_before_text(conn):
    router = FakeRouter(steps=[[ReasoningDelta("think"), TextDelta("Answer"), Usage(1, 1), Finish("stop")]])
    events, claim = await _run(conn, router)
    assert types(events)[2:8] == ["reasoning-start", "reasoning-delta", "reasoning-end",
                                  "text-start", "text-delta", "text-end"]
    assert (await _msg(conn, claim.turn_id))[3] == "think"


async def test_one_tool_round_then_answer(conn, monkeypatch):
    monkeypatch.setattr(ws_tool, "web_search", fake_web_search)
    router = FakeRouter(steps=[
        [TextDelta("Let me search."), ToolCallReady(ToolCall("c1", "web_search", {"query": "news"})),
         Usage(5, 2), Finish("tool_calls")],
        [TextDelta("Here is the news."), Usage(20, 4), Finish("stop")]])
    events, claim = await _run(conn, router)
    assert types(events) == ["start", "start-step", "text-start", "text-delta", "text-end", "finish-step",
                             "tool-input-available", "tool-output-available", "start-step", "text-start",
                             "text-delta", "text-end", "finish-step", "finish"]
    out = next(e for e in events if e["type"] == "tool-output-available")
    assert out["output"] == {"ok": True, "chunk_count": None, "query": "news",
                             "summary_text": 'Web search for "news" returned 1 result(s).'}
    second = router.calls[1]["messages"]
    assert second[-2].role == "assistant" and second[-2].tool_calls[0].id == "c1"
    assert second[-2].content == "Let me search."
    assert second[-1].role == "tool" and second[-1].tool_call_id == "c1"
    assert json.loads(second[-1].content)["query"] == "news"
    assert router.calls[1]["tools"] == []                   # CHAT_MAX_TOOL_ROUNDS=1: final step has tools off
    status, reason, content, _, tool_calls, _, _ = await _msg(conn, claim.turn_id)
    # Final review M5: two steps' text is two paragraphs, not "search.Here".
    assert (status, reason, content) == ("complete", "stop", "Let me search.\n\nHere is the news.")
    assert tool_calls == [{"name": "web_search", "arguments": {"query": "news"}, "result_summary": out["output"]}]
    assert events[-1]["usage"] == {"promptTokens": 25, "completionTokens": 6, "estimated": False}
    assert "tool-call" in await _log(conn)
    assert_fixture("tool_round", events)


async def test_silent_final_step_after_the_cap_is_max_steps(conn, monkeypatch):
    monkeypatch.setattr(ws_tool, "web_search", fake_web_search)
    router = FakeRouter(steps=[
        [ToolCallReady(ToolCall("c1", "web_search", {"query": "a"})), Usage(1, 1), Finish("tool_calls")],
        [Usage(1, 0), Finish("stop")]])
    events, _ = await _run(conn, router)
    assert events[-1]["type"] == "finish" and events[-1]["finishReason"] == "max-steps"


async def test_two_rounds_when_the_cap_allows_it(conn, monkeypatch):
    monkeypatch.setattr(ws_tool, "web_search", fake_web_search)
    call = lambda i: [ToolCallReady(ToolCall(f"c{i}", "web_search", {"query": "a"})), Usage(1, 1), Finish("tool_calls")]
    router = FakeRouter(steps=[call(1), call(2), reply("done")])
    events, _ = await _run(conn, router, cfg=ChatConfig(max_tool_rounds=2))
    assert [c["tools"] for c in router.calls] == [["web_search"], ["web_search"], []]
    assert events[-1]["finishReason"] == "stop"


async def test_tool_error_is_reported_and_turn_continues(conn):
    router = FakeRouter(steps=[
        [ToolCallReady(ToolCall("c1", "made_up", {})), ToolCallReady(ToolCall("c2", "web_search", {})),
         Usage(1, 1), Finish("tool_calls")],
        reply("Sorry, I couldn't search.")])
    events, claim = await _run(conn, router)
    errors = [e["errorText"] for e in events if e["type"] == "tool-output-error"]
    assert errors == ["Unknown tool: made_up", "query is required and must be non-empty."]
    assert events[-1]["type"] == "finish"
    assert (await _msg(conn, claim.turn_id))[2] == "Sorry, I couldn't search."


@pytest.mark.parametrize("exc,code", [
    (ProviderError(500, "out of memory"), "provider_error"),
    (ProviderUnavailable("refused"), "provider_unavailable"),
    (ProviderTimeout(), "provider_timeout"),
])
async def test_provider_failure_saves_the_partial_reply(conn, exc, code):
    router = FakeRouter(steps=[([TextDelta("Partial")], exc)])
    events, claim = await _run(conn, router)
    assert events[-1]["type"] == "error" and events[-1]["code"] == code
    assert (await _msg(conn, claim.turn_id))[:3] == ("error", "error", "Partial")
    assert await _claim(conn) is None
    assert "error" in await _log(conn)
    if code == "provider_error":
        assert "out of memory" in events[-1]["message"]
        assert_fixture("error", events)


async def test_an_explained_provider_error_is_shown_as_is(conn):
    """Task 10: a mapped status (429/401/402) already says what happened, so
    it isn't wrapped in "The model provider returned an error: ..."."""
    msg = "This model is busy or rate-limited at the provider. Try again in a moment, or pick another model."
    router = FakeRouter(steps=[([], ProviderError(429, msg, explained=True))])
    events, claim = await _run(conn, router)
    assert events[-1]["type"] == "error" and events[-1]["code"] == "provider_error"
    assert events[-1]["message"] == msg


async def test_budget_runs_out_between_steps(conn, monkeypatch):
    monkeypatch.setattr(ws_tool, "web_search", fake_web_search)
    alice = await member(conn, "alice")
    await conn.execute("UPDATE users SET inference_daily_token_budget = 10 WHERE id = %s", (alice.user_id,))
    router = FakeRouter(steps=[
        [ToolCallReady(ToolCall("c1", "web_search", {"query": "a"})), Usage(8, 4), Finish("tool_calls")],
        reply()])
    events, claim = await _run(conn, router, user=alice)
    assert (events[-1]["type"], events[-1]["code"]) == ("error", "budget_exhausted")
    assert len(router.calls) == 1
    assert (await _msg(conn, claim.turn_id))[0] == "error"
    assert "budget" in await _log(conn)


async def test_disconnect_mid_step_saves_aborted(conn):
    router = FakeRouter(steps=[[TextDelta("Part"), TextDelta("ial"), Usage(1, 1), Finish("stop")]])
    alice = await member(conn, "alice")
    claim, req = await _start(alice)
    agen = run_turn(req, claim, router=router, cfg=ChatConfig(), deployment_budget=None)
    async for e in agen:
        if e["type"] == "text-delta":
            break
    await agen.aclose()
    assert (await _msg(conn, claim.turn_id))[:3] == ("aborted", "aborted", "Part")
    assert await _claim(conn) is None
    assert "aborted" in await _log(conn)


async def test_cancel_during_tool_saves_aborted_and_releases_claim(conn, monkeypatch):
    started = asyncio.Event()

    async def slow_web_search(query, count):
        started.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(ws_tool, "web_search", slow_web_search)
    router = FakeRouter(steps=[[TextDelta("Let me look. "), ToolCallReady(ToolCall("c1", "web_search", {"query": "x"})),
                                Usage(1, 1), Finish("tool_calls")]])
    alice = await member(conn, "alice")
    claim, req = await _start(alice)

    async def consume():
        async for _ in run_turn(req, claim, router=router, cfg=ChatConfig(), deployment_budget=None):
            pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await _msg(conn, claim.turn_id))[:3] == ("aborted", "aborted", "Let me look. ")
    assert await _claim(conn) is None


async def test_model_without_tools_is_offered_none(conn):
    router = FakeRouter(caps=Capabilities(tools=False))
    await _run(conn, router)
    assert router.calls[0]["tools"] == []


async def test_missing_usage_is_estimated_and_recorded(conn):
    router = FakeRouter(steps=[[TextDelta("x" * 40), Finish("stop")]])
    events, claim = await _run(conn, router)
    assert events[-1]["usage"]["estimated"] is True and events[-1]["usage"]["completionTokens"] == 10
    assert (await _msg(conn, claim.turn_id))[6]["usageEstimated"] is True


async def test_dropped_feature_becomes_a_notice_and_a_log_line(conn):
    router = FakeRouter(steps=[[FeatureDropped("tools"), *reply()]])
    events, _ = await _run(conn, router)
    notice = next(e for e in events if e["type"] == "data-notice")
    assert notice["code"] == "tools_unsupported"
    assert "tool-fallback" in await _log(conn)
    assert_fixture("notice", events)


async def test_prefetch_note_comes_before_the_first_step_and_is_saved(conn, monkeypatch):
    note = {"kind": "prefetch", "docId": "d" * 64, "docName": "Thesis.pdf", "count": 1, "topScore": 0.9, "pages": [3]}

    async def fake_prefetch(doc, question, cfg):
        return Prefetch([{"page": 3, "score": 0.9, "text": "passage"}], 0.9, note)

    monkeypatch.setattr(ctx_mod, "prefetch", fake_prefetch)
    events, claim = await _run(conn, FakeRouter())
    assert types(events)[:3] == ["start", "data-context", "start-step"]
    assert events[1]["items"] == [note]
    assert (await _msg(conn, claim.turn_id))[5] == {"notes": [note]}
    assert_fixture("context", events)


async def test_second_turn_sends_the_first_as_history(conn):
    router = FakeRouter()
    alice = await member(conn, "alice")
    claim, req = await _start(alice, text="first")
    [_ async for _ in run_turn(req, claim, router=router, cfg=ChatConfig(), deployment_budget=None)]
    claim, req = await _start(alice, text="second")
    [_ async for _ in run_turn(req, claim, router=router, cfg=ChatConfig(), deployment_budget=None)]
    sent = [(m.role, m.content.rsplit("\n\n", 1)[-1]) for m in router.calls[1]["messages"][1:]]
    assert sent == [("user", "first"), ("assistant", "Hello there."), ("user", "second")]
    # The stored user message is what was typed, not the built prompt (I3).
    assert router.calls[1]["messages"][1].content == "first"


async def test_session_deleted_mid_turn_finishes_quietly(conn):
    router = FakeRouter(steps=[[TextDelta("a"), TextDelta("b"), Usage(1, 1), Finish("stop")]])
    alice = await member(conn, "alice")
    claim, req = await _start(alice)
    events = []
    async for e in run_turn(req, claim, router=router, cfg=ChatConfig(), deployment_budget=None):
        events.append(e)
        if e["type"] == "text-start":
            await conn.execute("DELETE FROM chat_sessions WHERE id='s-1'")
    assert events[-1]["type"] == "finish"
    assert (await _one(conn, "SELECT count(*) FROM chat_messages WHERE session_id='s-1'"))[0] == 0


# ---- Fix round 1 ----

async def test_add_event_failure_never_stops_the_turn(conn, monkeypatch):
    """Item 1: the sidebar's Log is advisory. A DB blip while writing it must
    not cost the SPA its `finish` event — every store.add_event call in the
    orchestrator now goes through _log_event, which swallows and logs."""
    async def boom(*a, **k):
        raise OSError("db blip")

    monkeypatch.setattr(store, "add_event", boom)
    router = FakeRouter()
    events, claim = await _run(conn, router)
    assert events[-1]["type"] == "finish"
    assert (await _msg(conn, claim.turn_id))[:3] == ("complete", "stop", "Hello there.")


async def test_save_progress_failure_becomes_an_internal_error(conn, monkeypatch):
    """Item 1's other half: save_progress is NOT cosmetic — its own failure is
    a genuine internal error, unlike an add_event blip, and must still surface
    as one (not raise out of run_turn uncaught)."""
    async def boom(*a, **k):
        raise OSError("db blip")

    monkeypatch.setattr(store, "save_progress", boom)
    router = FakeRouter()
    events, claim = await _run(conn, router)
    assert events[-1]["type"] == "error" and events[-1]["code"] == "internal_error"
    assert (await _msg(conn, claim.turn_id))[0] == "error"


async def test_disconnect_mid_step_still_records_usage(conn):
    """Item 2: a step cut short by disconnect must still be billed — today it
    was free, because _record_usage only ran at the normal end of a step."""
    router = FakeRouter(steps=[[TextDelta("Part"), TextDelta("ial"), Usage(1, 1), Finish("stop")]])
    alice = await member(conn, "alice")
    claim, req = await _start(alice)
    agen = run_turn(req, claim, router=router, cfg=ChatConfig(), deployment_budget=None)
    async for e in agen:
        if e["type"] == "text-delta":
            break
    await agen.aclose()
    row = await _one(conn, "SELECT prompt_tokens, eval_tokens FROM inference_usage WHERE user_id=%s",
                     (alice.user_id,))
    assert row is not None and row[0] > 0 and row[1] > 0


async def test_provider_error_before_any_output_is_not_charged(conn):
    """The other side of item 2's guard: a provider that refuses the request
    BEFORE generating anything (e.g. an invalid model name, HTTP 400) never
    sent a Usage chunk and produced no text/reasoning — there is nothing to
    charge for, unlike test_provider_error_mid_text_still_records_usage where
    partial output did arrive."""
    router = FakeRouter(steps=[([], ProviderError(400, "invalid model name"))])
    alice = await member(conn, "alice")
    events, claim = await _run(conn, router, user=alice)
    assert events[-1]["type"] == "error"
    row = await _one(conn, "SELECT prompt_tokens, eval_tokens FROM inference_usage WHERE user_id=%s",
                     (alice.user_id,))
    assert row is None


async def test_provider_error_mid_text_still_records_usage(conn):
    """Item 2, the other trigger: a provider that dies mid-text must still be
    billed for the partial output, not just a cancellation."""
    router = FakeRouter(steps=[([TextDelta("Partial")], ProviderError(500, "out of memory"))])
    alice = await member(conn, "alice")
    events, claim = await _run(conn, router, user=alice)
    assert events[-1]["type"] == "error"
    row = await _one(conn, "SELECT prompt_tokens, eval_tokens FROM inference_usage WHERE user_id=%s",
                     (alice.user_id,))
    assert row is not None and row[0] > 0 and row[1] > 0


async def test_completed_step_before_cancellation_is_not_double_recorded(conn, monkeypatch):
    """Item 2's guard: a step whose usage was already recorded normally (here,
    the tool round) must not be charged again just because the turn is later
    cancelled mid-tool-call."""
    started = asyncio.Event()

    async def slow_web_search(query, count):
        started.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(ws_tool, "web_search", slow_web_search)
    router = FakeRouter(steps=[[TextDelta("Let me look. "), ToolCallReady(ToolCall("c1", "web_search", {"query": "x"})),
                                Usage(7, 3), Finish("tool_calls")]])
    alice = await member(conn, "alice")
    claim, req = await _start(alice)

    async def consume():
        async for _ in run_turn(req, claim, router=router, cfg=ChatConfig(), deployment_budget=None):
            pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    row = await _one(conn, "SELECT prompt_tokens, eval_tokens FROM inference_usage WHERE user_id=%s",
                     (alice.user_id,))
    assert row == (7, 3)   # exactly the one step's usage — no second charge from _finalize


async def test_zero_tool_rounds_is_not_labelled_max_steps(conn):
    """Item 3: CHAT_MAX_TOOL_ROUNDS=0 means tools are off from step 1 — an
    empty reply there is an ordinary stop, not a capped tool loop."""
    router = FakeRouter(steps=[[Usage(1, 0), Finish("stop")]])
    events, _ = await _run(conn, router, cfg=ChatConfig(max_tool_rounds=0))
    assert events[-1]["finishReason"] == "stop"


async def test_finalize_reraises_a_lone_cancellation_and_logs_the_background_failure(conn, monkeypatch, caplog):
    """Items 4 and 5, in the one scenario that links them: _finalize's own
    await gets cancelled while nothing else is in flight (item 5 — must
    re-raise, not swallow), and the write it no longer awaits later fails on
    its own (item 4 — the done-callback must still log it)."""
    write_started = asyncio.Event()

    async def flaky_finish_turn(*a, **k):
        write_started.set()
        await asyncio.sleep(0.05)
        raise OSError("db gone")

    monkeypatch.setattr(store, "finish_turn", flaky_finish_turn)
    alice = await member(conn, "alice")
    claim, req = await _start(alice)
    state = orchestrator._State(content="partial")

    async def call_finalize():
        await orchestrator._finalize(claim, state, "aborted", req, time.monotonic())

    task = asyncio.create_task(call_finalize())
    await asyncio.wait_for(write_started.wait(), 5)
    task.cancel()
    with caplog.at_level("WARNING", logger="server.chat.orchestrator"):
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.2)   # let the shielded write() finish (and fail) in the background
    assert "cancelled again while saving" in caplog.text
    assert claim.turn_id in caplog.text and "failed" in caplog.text


async def test_heartbeat_loss_does_not_break_the_turn(conn, monkeypatch):
    """Item 7: losing the claim's heartbeat (another worker took over, or the
    chat was deleted) stops the heartbeat task quietly; it must never
    interrupt or fail the turn itself."""
    monkeypatch.setattr(store, "HEARTBEAT_S", 0.01)
    calls = {"n": 0}

    async def flaky_heartbeat(session_id, turn_id):
        calls["n"] += 1
        return calls["n"] == 1

    monkeypatch.setattr(store, "heartbeat", flaky_heartbeat)

    class SlowRouter(FakeRouter):
        def stream_chat(self, model_id, messages, tools, settings):
            self.calls.append({"model": model_id, "messages": list(messages),
                               "tools": [t.name for t in tools], "settings": settings})
            return self._slow()

        async def _slow(self):
            yield TextDelta("a")
            await asyncio.sleep(0.05)
            yield TextDelta("b")
            yield Usage(1, 1)
            yield Finish("stop")

    events, claim = await _run(conn, SlowRouter())
    assert events[-1]["type"] == "finish"
    assert (await _msg(conn, claim.turn_id))[:3] == ("complete", "stop", "ab")



# ---- Final review ----

async def test_a_silent_step_adds_no_separator(conn, monkeypatch):
    """M5: the blank line goes only BETWEEN two non-empty step texts."""
    monkeypatch.setattr(ws_tool, "web_search", fake_web_search)
    router = FakeRouter(steps=[
        [ToolCallReady(ToolCall("c1", "web_search", {"query": "a"})), Usage(1, 1), Finish("tool_calls")],
        reply("Answer.")])
    _, claim = await _run(conn, router)
    assert (await _msg(conn, claim.turn_id))[2] == "Answer."


@pytest.mark.parametrize("hit,calls,want", [
    (True, [], "prefetch_hit=True search_documents_called=False"),
    (False, [ToolCall("c1", "search_documents", {"query": "q"})], "prefetch_hit=False search_documents_called=True"),
])
async def test_the_prefetch_metric_is_logged_at_debug_per_turn(conn, monkeypatch, caplog, hit, calls, want):
    """M1 (spec §5.2.1): whether search_documents still ran after a prefetch,
    so a deployer can tune CHAT_PREFETCH_MIN_SCORE. No user text in it."""
    async def fake_prefetch(doc, question, cfg):
        return Prefetch([{"page": 1, "score": 0.9, "text": "p"}], 0.9,
                        {"kind": "prefetch", "docId": "d", "docName": "D", "count": 1, "topScore": 0.9, "pages": [1]}
                        ) if hit else Prefetch()

    monkeypatch.setattr(ctx_mod, "prefetch", fake_prefetch)
    steps = [[*(ToolCallReady(c) for c in calls), Usage(1, 1), Finish("tool_calls")]] if calls else []
    with caplog.at_level("DEBUG", logger="server.chat.orchestrator"):
        await _run(conn, FakeRouter(steps=steps), text="my secret question")
    [line] = [r for r in caplog.records if "prefetch_hit=" in r.getMessage()]
    assert line.levelname == "DEBUG" and want in line.getMessage()
    assert "my secret question" not in line.getMessage()


@pytest.mark.parametrize("kind,want", [("ollama", 1024), ("openai", 32768)])
async def test_num_ctx_sets_the_trimming_window_only_for_ollama_providers(conn, monkeypatch, kind, want):
    """M4: num_ctx is an Ollama option. A value left over from an Ollama model
    must not trim an OpenAI-kind chat to the wrong window; there the model's
    own context length is the window (ruling R14 applies to Ollama only)."""
    seen = {}
    real = orchestrator.build_context

    async def spy(turn, cfg):
        seen["window"] = turn.window
        return await real(turn, cfg)

    monkeypatch.setattr(orchestrator, "build_context", spy)
    router = FakeRouter(kind=kind, caps=Capabilities(context_window=32768))
    alice = await member(conn, "alice")
    claim, req = await _start(alice)
    req = dataclasses.replace(req, settings=CallSettings(num_ctx=1024))
    [_ async for _ in run_turn(req, claim, router=router, cfg=ChatConfig(), deployment_budget=None)]
    assert seen["window"] == want


# ---- v2.1 follow-ups, Task 2: tool calls a model writes as text ----

OPEN_DOC = ReadableDoc("d" * 64, "Thesis.pdf", "indexed")


@pytest.fixture
def open_doc(monkeypatch):
    """An open, indexed document, so search_documents is offered; its search
    finds nothing and no embedding model is called."""
    async def fake_open_doc(req):
        return OPEN_DOC

    async def fake_prefetch(doc, question, cfg):
        return Prefetch()

    async def fake_embed_one(text):
        return [0.0]

    async def fake_search_chunks(conn, doc_id, qvec, k):
        return []

    monkeypatch.setattr(orchestrator, "_open_doc", fake_open_doc)
    monkeypatch.setattr(ctx_mod, "prefetch", fake_prefetch)
    monkeypatch.setattr(sd_tool, "embed_one", fake_embed_one)
    monkeypatch.setattr(sd_tool, "search_chunks", fake_search_chunks)


TEXT_CALL = '{"name": "search_documents", "parameters": {"query": "the main finding"}}'


def _in_pieces(text: str, size: int = 7) -> list:
    return [TextDelta(text[i:i + size]) for i in range(0, len(text), size)]


async def test_the_last_round_says_so_in_its_results_and_the_rules_never_change(conn, open_doc):
    """v2.3 Task A: the final step offers no tools, and the rules (stable for
    the turn, for the prefix cache) can't say that, so the last round's tool
    results do. Nothing ever tells the model to call a tool it doesn't have."""
    router = FakeRouter(steps=[
        [ToolCallReady(ToolCall("c1", "search_documents", {"query": "results"})), Usage(5, 5), Finish("tool_calls")],
        reply("The result is X (page 3).")])
    events, claim = await _run(conn, router, text="what is the result?")
    first, final = router.calls
    assert first["tools"] == ["search_documents", "web_search"] and final["tools"] == []
    assert first["messages"][0].role == "system"
    assert final["messages"][0].content == first["messages"][0].content    # same rules on every step
    tool_msg = final["messages"][-1]
    assert tool_msg.role == "tool"
    assert json.loads(tool_msg.content)["note"] == orchestrator.LAST_ROUND_NOTE
    assert "answer now" in orchestrator.LAST_ROUND_NOTE


@pytest.mark.parametrize("why", ["no rounds", "model without tools"])
async def test_no_tool_is_named_when_the_turn_offers_none(conn, open_doc, why):
    """Review M8: CHAT_MAX_TOOL_ROUNDS=0 or a model that can't take tools:
    the rules still say which document is open, and name no tool."""
    from server.llm.types import Capabilities
    router = FakeRouter(steps=[reply("From the passages (page 1).")])
    cfg = ChatConfig(max_tool_rounds=0) if why == "no rounds" else ChatConfig()
    if why == "model without tools":
        router.caps = Capabilities(tools=False, thinking=True, vision=True, audio=None, context_window=None)
    await _run(conn, router, cfg=cfg, text="q")
    sent = router.calls[0]
    assert sent["tools"] == []
    system = sent["messages"][0].content
    assert '"Thesis.pdf"' in system
    for name in ("search_documents", "web_search", "Tools you can call"):
        assert name not in system


async def test_no_note_while_rounds_remain(conn, open_doc):
    router = FakeRouter(steps=[
        [ToolCallReady(ToolCall("c1", "search_documents", {"query": "a"})), Usage(5, 5), Finish("tool_calls")],
        reply("Done (page 1).")])
    await _run(conn, router, cfg=ChatConfig(max_tool_rounds=2), text="q")
    assert "note" not in json.loads(router.calls[1]["messages"][-1].content)
    assert router.calls[1]["tools"] == ["search_documents", "web_search"]


async def test_a_tool_call_written_as_text_is_run_not_saved(conn, open_doc, caplog):
    """llama3.2:3b writes its search_documents call as reply text. It must run
    as a tool call and leave no JSON in the answer."""
    router = FakeRouter(steps=[[*_in_pieces(TEXT_CALL), Usage(5, 20), Finish("stop")],
                               reply("The main finding is X.")])
    with caplog.at_level("DEBUG", logger="server.chat.orchestrator"):
        events, claim = await _run(conn, router, text="what's the main finding?")
    assert types(events) == ["start", "start-step", "finish-step", "tool-input-available",
                             "tool-output-available", "start-step", "text-start", "text-delta", "text-end",
                             "finish-step", "finish"]
    call = next(e for e in events if e["type"] == "tool-input-available")
    assert (call["toolName"], call["input"]) == ("search_documents", {"query": "the main finding"})
    assert events[2]["finishReason"] == "tool_calls"
    second = router.calls[1]["messages"]
    assert second[-2].role == "assistant" and second[-2].content == ""
    assert [(c.name, c.arguments) for c in second[-2].tool_calls] == [("search_documents", {"query": "the main finding"})]
    assert second[-1].role == "tool" and second[-1].tool_call_id == second[-2].tool_calls[0].id
    status, reason, content, _, tool_calls, _, _ = await _msg(conn, claim.turn_id)
    assert (status, reason, content) == ("complete", "stop", "The main finding is X.")
    assert [t["name"] for t in tool_calls] == ["search_documents"]
    # Like a native call, its saved summary names the document (Task 6 citations).
    assert tool_calls[0]["result_summary"]["docId"] == OPEN_DOC.doc_id
    logs = [r.getMessage() for r in caplog.records]
    assert "recovered text tool call name=search_documents" in logs
    assert any("search_documents_called=True" in m for m in logs)   # the prefetch metric counts it


@pytest.mark.parametrize("written", [
    "```json\n" + TEXT_CALL + "\n```",
    "  ```\n" + TEXT_CALL + "```\n",
    '\n{"name": "search_documents", "arguments": {"query": "the main finding"}}',
    '{"type": "function", "name": "search_documents", "parameters": {"query": "the main finding"}}',
])
async def test_fenced_and_variant_text_tool_calls_are_recovered(conn, open_doc, written):
    router = FakeRouter(steps=[[*_in_pieces(written, 3), Usage(5, 20), Finish("stop")],
                               reply("Answer.")])
    events, claim = await _run(conn, router)
    call = next(e for e in events if e["type"] == "tool-input-available")
    assert (call["toolName"], call["input"]) == ("search_documents", {"query": "the main finding"})
    assert (await _msg(conn, claim.turn_id))[2] == "Answer."


async def test_a_text_tool_call_naming_a_tool_not_offered_is_text(conn):
    """No open document: search_documents isn't offered, so this is the answer."""
    router = FakeRouter(steps=[[*_in_pieces(TEXT_CALL), Usage(5, 20), Finish("stop")]])
    events, claim = await _run(conn, router)
    assert types(events) == ["start", "start-step", "text-start", "text-delta", "text-end", "finish-step", "finish"]
    assert events[3]["delta"] == TEXT_CALL
    assert (await _msg(conn, claim.turn_id))[2] == TEXT_CALL
    assert len(router.calls) == 1


@pytest.mark.parametrize("answer", [
    '{"verdict": "sound", "parameters": {"n": 3}}\nThat is the summary you asked for.',
    '{"name": ["search_documents"], "parameters": {"query": "q"}}',
    '{"name": "search_documents", "parameters": "the main finding"}',
    '{"name": "search_documents", "parameters": {"query": "q"}, "note": "extra"}',
    '```json\n' + TEXT_CALL + '\n```\nThat was my search.',
])
async def test_json_that_is_not_a_tool_call_is_streamed_unchanged(conn, open_doc, answer):
    router = FakeRouter(steps=[[*_in_pieces(answer), Usage(5, 20), Finish("stop")]])
    events, claim = await _run(conn, router)
    assert "tool-input-available" not in types(events)
    assert "".join(e["delta"] for e in events if e["type"] == "text-delta") == answer
    assert (await _msg(conn, claim.turn_id))[2] == answer


async def test_a_native_tool_call_means_text_is_never_recovered(conn, open_doc, monkeypatch):
    monkeypatch.setattr(ws_tool, "web_search", fake_web_search)
    router = FakeRouter(steps=[
        [*_in_pieces(TEXT_CALL), ToolCallReady(ToolCall("c1", "web_search", {"query": "news"})),
         Usage(5, 20), Finish("tool_calls")],
        reply("Answer.")])
    events, claim = await _run(conn, router)
    assert [e["toolName"] for e in events if e["type"] == "tool-input-available"] == ["web_search"]
    assert (await _msg(conn, claim.turn_id))[2] == TEXT_CALL + "\n\nAnswer."


async def test_a_step_whose_tools_were_dropped_never_recovers(conn, open_doc):
    router = FakeRouter(steps=[[FeatureDropped("tools"), *_in_pieces(TEXT_CALL), Usage(5, 20), Finish("stop")]])
    events, claim = await _run(conn, router)
    assert "tool-input-available" not in types(events)
    assert (await _msg(conn, claim.turn_id))[2] == TEXT_CALL


class _WatchedRouter(FakeRouter):
    """Records how many chunks the provider has handed over, so a test can see
    WHEN the orchestrator emitted something, not only what it emitted."""
    def stream_chat(self, model_id, messages, tools, settings):
        self.calls.append({"model": model_id, "messages": list(messages),
                           "tools": [t.name for t in tools], "settings": settings})
        self.sent = 0
        return self._watched(self.steps.pop(0) if self.steps else reply())

    async def _watched(self, chunks):
        for c in chunks:
            self.sent += 1
            yield c


@pytest.mark.parametrize("pieces,first_after,caps", [
    (["Hello", " there", "."], 1, None),                                  # prose: no hold at all
    (["  \n", "Sure", ", here."], 2, None),                              # leading whitespace, then prose
    (["```python\n", "print(1)", "\n```"], 2, None),                    # a fence that isn't JSON
    (["``", "`js", "\nlet x", " = 1"], 3, None),                         # ...decided once it can't be
    (["{" + "a" * 2000, "tail", "}"], 1, None),                          # the probe cap is passed
    ([TEXT_CALL[:20], TEXT_CALL[20:]], 1, Capabilities(tools=False)),    # no tools offered: no hold
])
async def test_text_that_cannot_be_a_tool_call_is_not_held(conn, pieces, first_after, caps):
    router = _WatchedRouter(steps=[[*(TextDelta(p) for p in pieces), Usage(1, 1), Finish("stop")]],
                            **({"caps": caps} if caps else {}))
    alice = await member(conn, "alice")
    claim, req = await _start(alice)
    seen_at = None
    async for e in run_turn(req, claim, router=router, cfg=ChatConfig(), deployment_budget=None):
        if e["type"] == "text-delta" and seen_at is None:
            seen_at = router.sent
            assert e["delta"] == "".join(pieces[:first_after])
    assert seen_at == first_after
    assert (await _msg(conn, claim.turn_id))[2] == "".join(pieces)


async def test_no_recovery_when_the_provider_already_strips_tools_for_this_model(conn, open_doc):
    """Fix round 1: once a provider remembers that a model rejected tools, it
    strips them silently (no FeatureDropped). A vLLM-style server lists no
    capabilities (tools=None), so only the remembered rejection says the tools
    are never sent: the JSON is the model's answer, and no tool runs."""
    import httpx

    from server.llm.providers.base import ProviderConfig
    from server.llm.router import Router
    from server.tests.llm_fakes import FakeUpstream, sse

    up = (FakeUpstream()
          .on("GET", "/v1/models", lambda: httpx.Response(200, json={"data": [{"id": "Qwen"}]}))
          .on("POST", "/v1/chat/completions", lambda: sse(
              {"choices": [{"index": 0, "delta": {"content": TEXT_CALL}, "finish_reason": None}]},
              {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
              {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 20}})))
    router = Router([ProviderConfig(name="vllm", kind="openai", url="http://vllm.test/v1")], up.client())
    router.providers["vllm"]._features.remember("Qwen", "tools")   # learned on an earlier turn
    alice = await member(conn, "alice")
    claim, req = await _start(alice)
    req = dataclasses.replace(req, model_id="vllm:Qwen")
    events = [e async for e in run_turn(req, claim, router=router, cfg=ChatConfig(), deployment_budget=None)]
    assert types(events) == ["start", "start-step", "text-start", "text-delta", "text-end", "finish-step", "finish"]
    assert events[3]["delta"] == TEXT_CALL                                  # never held, never run
    assert (await _msg(conn, claim.turn_id))[2] == TEXT_CALL
    [body] = up.bodies("/v1/chat/completions")
    assert "tools" not in body


# ---- v2.3 Task B ----

async def test_prefetched_passages_are_not_repeated_by_a_search_and_the_floor_comes_from_config(
        conn, open_doc, monkeypatch):
    """The prefetch block already holds chunk 11; a search that finds it again
    returns only its page. CHAT_SEARCH_MIN_SCORE reaches the tool."""
    async def fake_prefetch(doc, question, cfg):
        return Prefetch([{"id": 11, "page": 3, "score": 0.8, "text": "prefetched"}], 0.8,
                        {"kind": "prefetch", "docId": doc.doc_id, "docName": doc.name, "count": 1,
                         "topScore": 0.8, "pages": [3]})

    async def fake_search_chunks(conn, doc_id, qvec, k):
        return [{"id": 11, "page": 3, "score": 0.8, "text": "prefetched"},
                {"id": 12, "page": 5, "score": 0.6, "text": "new"},
                {"id": 13, "page": 8, "score": 0.5, "text": "below this config's floor"}]

    monkeypatch.setattr(ctx_mod, "prefetch", fake_prefetch)
    monkeypatch.setattr(sd_tool, "search_chunks", fake_search_chunks)
    router = FakeRouter(steps=[
        [ToolCallReady(ToolCall("c1", "search_documents", {"query": "more"})), Usage(5, 5), Finish("tool_calls")],
        reply("Done (page 5).")])
    await _run(conn, router, cfg=ChatConfig(search_min_score=0.55), text="q")
    result = json.loads(router.calls[1]["messages"][-1].content)
    assert result["already_shown"] == [{"ref": 1, "pages": [3]}]
    assert result["passages"] == [{"ref": 1, "page": 5, "relevance": "moderate", "text": "new"}]
    assert "_saved" not in result


def test_chat_search_min_score_is_read_from_the_environment():
    from server.chat.config import load_chat_config
    assert load_chat_config({}).search_min_score == 0.45
    assert load_chat_config({"CHAT_SEARCH_MIN_SCORE": "0.5"}).search_min_score == 0.5
    assert load_chat_config({"CHAT_SEARCH_MIN_SCORE": "2"}).search_min_score == 0.45
