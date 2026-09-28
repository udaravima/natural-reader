import asyncio
import json
import time

import pytest

from server.chat import context as ctx_mod
from server.chat import orchestrator, store
from server.chat.config import ChatConfig
from server.chat.context import Prefetch
from server.chat.orchestrator import TurnRequest, run_turn
from server.chat.tools import search_document as sd_tool
from server.chat.tools import web_search as ws_tool
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
    assert sent["tools"] == ["web_search"]                  # no open document: no search_document
    assert sent["messages"][-1].content == "Hi" and sent["messages"][-2].role == "system"
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
        [ToolCallReady(ToolCall("c1", "web_search", {"query": "news"})), Usage(5, 2), Finish("tool_calls")],
        [TextDelta("Here is the news."), Usage(20, 4), Finish("stop")]])
    events, claim = await _run(conn, router)
    assert types(events) == ["start", "start-step", "finish-step", "tool-input-available",
                             "tool-output-available", "start-step", "text-start", "text-delta", "text-end",
                             "finish-step", "finish"]
    out = next(e for e in events if e["type"] == "tool-output-available")
    assert out["output"] == {"ok": True, "chunk_count": None, "query": "news",
                             "summary_text": 'Web search for "news" returned 1 result(s).'}
    second = router.calls[1]["messages"]
    assert second[-2].role == "assistant" and second[-2].tool_calls[0].id == "c1"
    assert second[-1].role == "tool" and second[-1].tool_call_id == "c1"
    assert json.loads(second[-1].content)["query"] == "news"
    assert router.calls[1]["tools"] == []                   # CHAT_MAX_TOOL_ROUNDS=1: final step has tools off
    status, reason, content, _, tool_calls, _, _ = await _msg(conn, claim.turn_id)
    assert (status, reason, content) == ("complete", "stop", "Here is the news.")
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
    sent = [(m.role, m.content) for m in router.calls[1]["messages"] if m.role != "system"]
    assert sent == [("user", "first"), ("assistant", "Hello there."), ("user", "second")]


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
