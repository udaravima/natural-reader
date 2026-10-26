import asyncio
import dataclasses
import json
import time
from types import SimpleNamespace

import pytest

from server.auth.users import SEED_ADMIN_ID
from server.chat import context as ctx_mod
from server.chat import orchestrator, store
from server.chat.config import ChatConfig
from server.chat.context import Prefetch
from server.chat import reader
from server.chat.reader import BatchCall
from server.chat.orchestrator import TurnRequest, run_turn
from server.chat.tools import TurnTools
from server.chat.tools import extract_from_documents as ex_tool
from server.chat.tools import search_documents as sd_tool
from server.chat.tools import web_search as ws_tool
from server.services import console_settings, model_settings
from server.services.doc_search import ReadableDoc
from server.services.inference_limits import InferenceLimits
from server.llm.providers.base import ProviderConfig
from server.llm.router import UnknownModel
from server.llm.types import (CallSettings, Capabilities, FeatureDropped, Finish, Message, ProviderError,
                              ProviderTimeout, ProviderUnavailable, ReasoningDelta, TextDelta,
                              ToolCall, ToolCallReady, Usage)
from server.tests.chat_harness import FakeRouter, assert_fixture, member, reply, shim_pool
from server.tests.test_chat_extract import by_label
from server.tests.test_chat_project_scope import A2, B, _project_turn, _search_step
from server.tests.test_chat_web import PAGE, hit, url, use_router, web  # noqa: F401 — web and use_router are fixtures


@pytest.fixture
def conn(db_conn, monkeypatch):
    shim_pool(monkeypatch, db_conn, store, orchestrator, ctx_mod, sd_tool)
    return db_conn


@pytest.fixture
def web_auto(monkeypatch):
    """The deployment searches without asking (CHAT_WEB_SEARCH=auto): for the tests of the web tools' turn
    mechanics. The default, "ask", is covered by test_chat_web_consent.py."""
    monkeypatch.setenv("CHAT_WEB_SEARCH", "auto")


async def fake_web_search(query, count):
    return {"query": query, "results": [{"title": "t", "url": "https://example.com", "summary": "s"}]}


async def _start(user, text="Hi", sid="s-1", model_id="ollama:m", settings=CallSettings()):
    claim = await store.begin_turn(session_id=sid, user_id=user.user_id, model_id=model_id, text=text,
                                   attachments=[], new_session_pins=None)
    req = TurnRequest(user_id=user.user_id, session_id=sid, model_id=model_id, text=text, attachments=(),
                      settings=settings, doc_id=None, timezone="UTC")
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


async def test_plain_reply_streams_saves_and_releases(conn, web_auto):
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
    assert sent["tools"] == ["web_search", "read_web_pages"]                  # no open document: no search_documents
    # v2.3: the rules are the one leading system message, and name only the
    # tool offered. v2.4: the date is in them; the user message is what they typed.
    system, only = sent["messages"]
    assert system.role == "system" and "web_search" in system.content and "search_documents" not in system.content
    assert "Today is " in system.content
    assert only.role == "user" and only.content == "Hi"
    assert await _log(conn) == ["sent", "received"]
    assert_fixture("plain", events)


async def test_reasoning_closes_before_text(conn):
    router = FakeRouter(steps=[[ReasoningDelta("think"), TextDelta("Answer"), Usage(1, 1), Finish("stop")]])
    events, claim = await _run(conn, router)
    assert types(events)[2:8] == ["reasoning-start", "reasoning-delta", "reasoning-end",
                                  "text-start", "text-delta", "text-end"]
    assert (await _msg(conn, claim.turn_id))[3] == "think"


async def test_one_tool_round_then_answer(conn, monkeypatch, web_auto):
    monkeypatch.setattr(ws_tool, "web_search", fake_web_search)
    router = FakeRouter(steps=[
        [TextDelta("Let me search."), ToolCallReady(ToolCall("c1", "web_search", {"query": "news"})),
         Usage(5, 2), Finish("tool_calls")],
        [TextDelta("Here is the news."), Usage(20, 4), Finish("stop")]])
    events, claim = await _run(conn, router, cfg=ChatConfig(max_tool_rounds=1))
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
    events, _ = await _run(conn, router, cfg=ChatConfig(max_tool_rounds=1))
    assert events[-1]["type"] == "finish" and events[-1]["finishReason"] == "max-steps"


async def test_two_rounds_when_the_cap_allows_it(conn, monkeypatch, web_auto):
    monkeypatch.setattr(ws_tool, "web_search", fake_web_search)
    call = lambda i: [ToolCallReady(ToolCall(f"c{i}", "web_search", {"query": "a"})), Usage(1, 1), Finish("tool_calls")]
    router = FakeRouter(steps=[call(1), call(2), reply("done")])
    events, _ = await _run(conn, router, cfg=ChatConfig(max_tool_rounds=2))
    assert [c["tools"] for c in router.calls] == [["web_search", "read_web_pages"], ["web_search", "read_web_pages"], []]
    assert events[-1]["finishReason"] == "stop"


async def test_tool_error_is_reported_and_turn_continues(conn, web_auto):
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


async def test_cancel_during_tool_saves_aborted_and_releases_claim(conn, monkeypatch, web_auto):
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

    async def fake_prefetch(scope, question, cfg):
        return Prefetch([{"ref": 1, "doc_id": "d" * 64, "page": 3, "score": 0.9, "text": "passage"}], 0.9, note)

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


async def test_completed_step_before_cancellation_is_not_double_recorded(conn, monkeypatch, web_auto):
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
    async def fake_prefetch(scope, question, cfg):
        return Prefetch([{"ref": 1, "doc_id": "d", "page": 1, "score": 0.9, "text": "p"}], 0.9,
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


# ---- admin console v2 phase 2a, Task 2: one context size per local model ----

@pytest.fixture
def window_spy(monkeypatch):
    """The window each turn's context is built with; the env has no INFERENCE_LOCAL_NUM_CTX unless a test sets it."""
    for var in ("INFERENCE_LOCAL_NUM_CTX", "INFERENCE_NUM_CTX_MAX", "INFERENCE_VLLM_CONTEXT_WINDOW"):
        monkeypatch.delenv(var, raising=False)
    seen = []
    real = orchestrator.build_context

    async def spy(turn, cfg):
        seen.append(turn.window)
        return await real(turn, cfg)

    monkeypatch.setattr(orchestrator, "build_context", spy)
    return seen


async def _sizes_turn(conn, router, *, model_id="ollama:m", num_ctx=None):
    """A two-step turn (one tool round), so 'every call' means more than one. Returns the num_ctx each
    stream_chat call carried."""
    router.steps = [
        [ToolCallReady(ToolCall("c1", "web_search", {"query": "news"})), Usage(5, 2), Finish("tool_calls")],
        reply()]
    claim, req = await _start(await member(conn, "alice"), model_id=model_id, settings=CallSettings(num_ctx=num_ctx))
    [_ async for _ in run_turn(req, claim, router=router, cfg=ChatConfig(max_tool_rounds=1), deployment_budget=None)]
    assert len(router.calls) == 2
    return [c["settings"].num_ctx for c in router.calls]


async def _save_models(conn, **changes):
    await console_settings.save(conn, model_settings.SECTION, changes, updated_by=SEED_ADMIN_ID)


async def test_local_ollama_turn_sends_16384_without_any_setting(conn, monkeypatch, web_auto, window_spy):
    monkeypatch.setattr(ws_tool, "web_search", fake_web_search)
    assert await _sizes_turn(conn, FakeRouter()) == [16384, 16384]
    assert window_spy == [16384]


async def test_self_hosted_remote_ollama_is_sized_too(conn, monkeypatch, web_auto, window_spy):
    monkeypatch.setattr(ws_tool, "web_search", fake_web_search)
    router = FakeRouter(url="http://gpu.example.com:11434")
    assert await _sizes_turn(conn, router, model_id="ollama:qwen3:8b") == [16384, 16384]
    assert window_spy == [16384]


async def test_admin_size_beats_personal_value_and_unset_keeps_it(conn, monkeypatch, web_auto, window_spy):
    monkeypatch.setattr(ws_tool, "web_search", fake_web_search)
    await _save_models(conn, context_size=8192)
    assert await _sizes_turn(conn, FakeRouter(), num_ctx=4096) == [8192, 8192]
    assert window_spy == [8192]
    await _save_models(conn, context_size=None)
    assert await _sizes_turn(conn, FakeRouter(), num_ctx=4096) == [4096, 4096]
    assert window_spy[-1] == 4096


async def test_cloud_model_keeps_the_personal_value(conn, monkeypatch, web_auto, window_spy):
    monkeypatch.setattr(ws_tool, "web_search", fake_web_search)
    cloud = "ollama:gemma4:31b-cloud"
    assert await _sizes_turn(conn, FakeRouter(), model_id=cloud, num_ctx=4096) == [4096, 4096]
    assert await _sizes_turn(conn, FakeRouter(caps=Capabilities(tools=True, context_window=262144)),
                             model_id=cloud) == [None, None]
    assert window_spy[-1] == 262144     # nothing sent, so the model's own window trims


async def test_openai_model_trims_to_a_declared_window(conn, window_spy):
    await _save_models(conn, context_windows={"vllm:m": 9000})
    router = FakeRouter(kind="openai", provider_name="vllm")      # reports no window
    claim, req = await _start(await member(conn, "alice"), model_id="vllm:m")
    [_ async for _ in run_turn(req, claim, router=router, cfg=ChatConfig(), deployment_budget=None)]
    assert window_spy == [9000]
    assert router.calls[0]["settings"].num_ctx is None                # openai ignores num_ctx: none is sent


async def test_unreadable_models_row_falls_back_to_env(conn, monkeypatch, caplog, window_spy):
    monkeypatch.setenv("INFERENCE_LOCAL_NUM_CTX", "6144")

    async def broken(conn, env=None):
        raise RuntimeError("no pool")

    monkeypatch.setattr(model_settings, "load_models_settings", broken)
    router = FakeRouter()
    claim, req = await _start(await member(conn, "alice"))
    events = [e async for e in run_turn(req, claim, router=router, cfg=ChatConfig(), deployment_budget=None)]
    assert events[-1]["type"] == "finish"
    assert router.calls[0]["settings"].num_ctx == 6144 and window_spy == [6144]
    assert any(r.levelname == "WARNING" for r in caplog.records)


async def test_a_turn_gets_the_deployment_zone_as_its_default(conn, monkeypatch):
    seen = []
    real = orchestrator.build_context

    async def spy(turn, cfg):
        seen.append(turn.default_timezone)
        return await real(turn, cfg)

    async def colombo():
        return "Asia/Colombo"

    monkeypatch.setattr(orchestrator, "build_context", spy)
    alice = await member(conn, "alice")
    monkeypatch.setattr(orchestrator, "_deployment_timezone", colombo)
    claim, req = await _start(alice)
    [_ async for _ in run_turn(req, claim, router=FakeRouter(), cfg=ChatConfig(), deployment_budget=None)]
    assert seen == ["Asia/Colombo"]


async def test_the_deployment_zone_is_utc_when_it_cannot_be_read(monkeypatch):
    def broken():
        raise RuntimeError("no pool")

    monkeypatch.setattr(orchestrator, "get_pool", broken)
    assert await orchestrator._deployment_timezone() == "UTC"


# ---- v2.1 follow-ups, Task 2: tool calls a model writes as text ----

OPEN_DOC = ReadableDoc("d" * 64, "Thesis.pdf", "indexed")


@pytest.fixture
def open_doc(monkeypatch):
    """An open, indexed document, so search_documents is offered; its search
    finds nothing and no embedding model is called."""
    async def fake_open_doc(req):
        return OPEN_DOC

    async def fake_prefetch(scope, question, cfg):
        return Prefetch()

    async def fake_embed_one(text):
        return [0.0]

    async def fake_search_scope(conn, doc_ids, qvec, k, text=None, *, per_doc=None, depth=0, exclude=()):
        return []

    monkeypatch.setattr(orchestrator, "_open_doc", fake_open_doc)
    monkeypatch.setattr(ctx_mod, "prefetch", fake_prefetch)
    monkeypatch.setattr(sd_tool, "embed_query", fake_embed_one)
    monkeypatch.setattr(sd_tool, "search_scope", fake_search_scope)


TEXT_CALL = '{"name": "search_documents", "parameters": {"query": "the main finding"}}'


def _in_pieces(text: str, size: int = 7) -> list:
    return [TextDelta(text[i:i + size]) for i in range(0, len(text), size)]


async def test_the_last_round_says_so_in_its_results_and_the_rules_never_change(conn, open_doc, web_auto):
    """v2.3 Task A: the final step offers no tools, and the rules (stable for
    the turn, for the prefix cache) can't say that, so the last round's tool
    results do. Nothing ever tells the model to call a tool it doesn't have."""
    router = FakeRouter(steps=[
        [ToolCallReady(ToolCall("c1", "search_documents", {"query": "results"})), Usage(5, 5), Finish("tool_calls")],
        reply("The result is X (page 3).")])
    events, claim = await _run(conn, router, cfg=ChatConfig(max_tool_rounds=1), text="what is the result?")
    first, final = router.calls
    assert first["tools"] == ["search_documents", "read_document_pages", "extract_from_documents", "web_search", "read_web_pages"] and final["tools"] == []
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
    for name in ("search_documents", "web_search", "Tool results"):
        assert name not in system


async def test_the_tool_context_carries_the_turns_model(conn, monkeypatch):
    """C2.1 Task 11: the reader sub-agent reads with the turn's own model when READER_MODEL=chat."""
    seen = []

    class Spy(orchestrator.TurnTools):
        def __init__(self, ctx):
            seen.append(ctx.model)
            super().__init__(ctx)

    monkeypatch.setattr(orchestrator, "TurnTools", Spy)
    await _run(conn, FakeRouter(steps=[reply("Hi there.")]))
    assert seen == ["ollama:m"]


# ---- admin console v2 phase 2a, Task 3: the reader's model and size ----

@pytest.fixture
def reader_env(monkeypatch):
    for var in ("READER_MODEL", "SUMMARIZE_MODEL", "WEB_SEARCH_SUMMARY_MODEL", "INFERENCE_LOCAL_NUM_CTX",
                "INFERENCE_NUM_CTX_MAX"):
        monkeypatch.delenv(var, raising=False)


class _TwoKinds(FakeRouter):
    """`vllm:` ids belong to an openai-kind provider, `gone` to none, everything else to the Ollama."""
    def resolve(self, model_id):
        if model_id == "gone":
            raise UnknownModel(model_id)
        if model_id.startswith("vllm:"):
            return SimpleNamespace(config=ProviderConfig("vllm", "openai", "http://vllm.example.com/v1")), model_id[5:]
        return super().resolve(model_id)


async def _reader_cfg_of(conn, monkeypatch, router=None, *, model_id="ollama:m", num_ctx=None):
    """The ToolContext.reader_cfg a one-step turn hands to its tools."""
    seen = []

    class Spy(orchestrator.TurnTools):
        def __init__(self, ctx):
            seen.append(ctx.reader_cfg)
            super().__init__(ctx)

    monkeypatch.setattr(orchestrator, "TurnTools", Spy)
    claim, req = await _start(await member(conn, "alice"), model_id=model_id, settings=CallSettings(num_ctx=num_ctx))
    events = [e async for e in run_turn(req, claim, router=router or FakeRouter(), cfg=ChatConfig(),
                                        deployment_budget=None)]
    assert events[-1]["type"] == "finish"
    [cfg] = seen
    return cfg


async def test_turn_reader_is_sized_to_its_own_model(conn, monkeypatch, reader_env):
    # the reader is the chat model (the default): the chat's size, whatever decided it
    cfg = await _reader_cfg_of(conn, monkeypatch)
    assert (cfg.model, cfg.num_ctx) == ("ollama:m", 16384)
    cfg = await _reader_cfg_of(conn, monkeypatch, num_ctx=4096)
    assert (cfg.model, cfg.num_ctx) == ("ollama:m", 4096)
    # ... and ":latest" doesn't make it another model
    await _save_models(conn, reader="ollama:m:latest")
    assert (await _reader_cfg_of(conn, monkeypatch, num_ctx=4096)).num_ctx == 4096

    # a different local model, the person's chat value 4096 and no admin size: its own default, not the person's
    await _save_models(conn, reader="ollama:other")
    cfg = await _reader_cfg_of(conn, monkeypatch, num_ctx=4096)
    assert (cfg.model, cfg.num_ctx) == ("ollama:other", 16384)
    # ... or the admin's size for that model
    await _save_models(conn, context_sizes={"ollama:other": 8192})
    assert (await _reader_cfg_of(conn, monkeypatch, num_ctx=4096)).num_ctx == 8192

    # the chat is a cloud model and so is the reader (chat): nothing to send
    await _save_models(conn, reader="chat")
    cfg = await _reader_cfg_of(conn, monkeypatch, model_id="ollama:gemma4:31b-cloud")
    assert (cfg.model, cfg.num_ctx) == ("ollama:gemma4:31b-cloud", None)
    # a cloud chat with a local reader: the reader is sized, the chat isn't
    await _save_models(conn, reader="ollama:other")
    assert (await _reader_cfg_of(conn, monkeypatch, model_id="ollama:gemma4:31b-cloud")).num_ctx == 8192


async def test_cloud_chat_with_a_personal_size_gives_the_reader_none(conn, monkeypatch, reader_env):
    """The chat model is Ollama cloud: the person's 4096 is sent to it, but it isn't a size the reader (the same
    model) may send: a cloud model needs none, and without one its batches are full-size."""
    cfg = await _reader_cfg_of(conn, monkeypatch, model_id="ollama:gemma4:31b-cloud", num_ctx=4096)
    assert (cfg.model, cfg.num_ctx) == ("ollama:gemma4:31b-cloud", None)
    calls = []

    class Router:
        async def complete(self, model_id, messages, settings, *, received=None):
            calls.append((model_id, settings))
            return '{"found": false, "facts": []}'

    monkeypatch.setattr(reader, "get_router", lambda: Router())
    await reader.read_batch("q", "n", [reader.Passage("[1]", "text")], chat_model="ollama:gemma4:31b-cloud", cfg=cfg)
    [(model, settings)] = calls
    assert model == "ollama:gemma4:31b-cloud" and settings.num_ctx is None
    assert reader.fit_input_chars(cfg, "q", "n") == cfg.input_chars


async def test_a_different_local_reader_is_held_to_the_ceiling(conn, monkeypatch, reader_env):
    monkeypatch.setenv("INFERENCE_NUM_CTX_MAX", "8192")
    await _save_models(conn, reader="ollama:other")
    cfg = await _reader_cfg_of(conn, monkeypatch, num_ctx=4096)
    assert (cfg.model, cfg.num_ctx) == ("ollama:other", 8192)      # its 16384 default, clamped


async def test_a_reader_that_ignores_num_ctx_is_sent_none(conn, monkeypatch, reader_env):
    await _save_models(conn, reader="vllm:small")
    cfg = await _reader_cfg_of(conn, monkeypatch, _TwoKinds())
    assert (cfg.model, cfg.num_ctx) == ("vllm:small", None)
    await _save_models(conn, reader="ollama:gemma4:31b-cloud")
    assert (await _reader_cfg_of(conn, monkeypatch, _TwoKinds())).num_ctx is None


async def test_an_unresolvable_reader_never_fails_the_turn(conn, monkeypatch, reader_env):
    await _save_models(conn, reader="gone")
    cfg = await _reader_cfg_of(conn, monkeypatch, _TwoKinds())
    assert (cfg.model, cfg.num_ctx) == ("gone", None)       # the reader's own provider_error result handles it later


async def test_console_reader_wins_over_env(conn, monkeypatch, reader_env):
    monkeypatch.setenv("READER_MODEL", "chat")
    await _save_models(conn, reader="ollama:gemma4:e4b")
    cfg = await _reader_cfg_of(conn, monkeypatch)
    assert cfg.model == "ollama:gemma4:e4b"
    assert cfg.num_ctx == 16384


async def test_the_reader_keeps_its_knobs_from_the_environment(conn, monkeypatch, reader_env):
    monkeypatch.setenv("READER_INPUT_CHARS", "8000")
    monkeypatch.setenv("READER_TIMEOUT_S", "90")
    cfg = await _reader_cfg_of(conn, monkeypatch)
    assert (cfg.input_chars, cfg.timeout_s) == (8000, 90.0)


async def test_no_note_while_rounds_remain(conn, open_doc, web_auto):
    router = FakeRouter(steps=[
        [ToolCallReady(ToolCall("c1", "search_documents", {"query": "a"})), Usage(5, 5), Finish("tool_calls")],
        reply("Done (page 1).")])
    await _run(conn, router, cfg=ChatConfig(max_tool_rounds=2), text="q")
    assert "note" not in json.loads(router.calls[1]["messages"][-1].content)
    assert router.calls[1]["tools"] == ["search_documents", "read_document_pages", "extract_from_documents", "web_search", "read_web_pages"]


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
    async def fake_prefetch(scope, question, cfg):
        doc = scope[0].doc
        return Prefetch([{"id": 11, "ref": 1, "doc_id": doc.doc_id, "page": 3, "score": 0.8, "text": "prefetched"}], 0.8,
                        {"kind": "prefetch", "docId": doc.doc_id, "docName": doc.name, "count": 1,
                         "topScore": 0.8, "pages": [3]})

    async def fake_search_scope(conn, doc_ids, qvec, k, text=None, *, per_doc=None, depth=0, exclude=()):
        return [{"doc_id": doc_ids[0], **r} for r in (
            {"id": 11, "page": 3, "score": 0.8, "text": "prefetched"},
            {"id": 12, "page": 5, "score": 0.6, "text": "new"},
            {"id": 13, "page": 8, "score": 0.5, "text": "below this config's floor"})]

    monkeypatch.setattr(ctx_mod, "prefetch", fake_prefetch)
    monkeypatch.setattr(sd_tool, "search_scope", fake_search_scope)
    router = FakeRouter(steps=[
        [ToolCallReady(ToolCall("c1", "search_documents", {"query": "more"})), Usage(5, 5), Finish("tool_calls")],
        reply("Done (page 5).")])
    # prefetch="on": this tests prefetched passages next to a search (the default, auto, skips it with tools).
    await _run(conn, router, cfg=ChatConfig(search_min_score=0.55, prefetch="on"), text="q")
    result = json.loads(router.calls[1]["messages"][-1].content)
    assert result["already_shown"] == [{"ref": 1, "pages": [3]}]
    assert result["passages"] == [{"ref": 1, "page": 5, "text": "new"}]
    assert "_saved" not in result


def test_chat_search_min_score_is_read_from_the_environment():
    from server.chat.config import load_chat_config
    assert load_chat_config({}).search_min_score == 0.45
    assert load_chat_config({"CHAT_SEARCH_MIN_SCORE": "0.5"}).search_min_score == 0.5
    assert load_chat_config({"CHAT_SEARCH_MIN_SCORE": "2"}).search_min_score == 0.45


async def test_the_deployment_profile_leads_the_system_message(conn):
    """v2.4 Task A2: an admin-saved profile reaches the model first, every turn."""
    from server.services.assistant_profile import save_profile
    await save_profile(conn, "You are Ada, the reading-room assistant.", updated_by=None)
    router = FakeRouter(steps=[reply("Hello!")])
    await _run(conn, router)
    system = router.calls[0]["messages"][0]
    assert system.role == "system"
    assert system.content.startswith("You are Ada, the reading-room assistant.\n\nYou are working in Natural Reader")


# ---- C2.1 Task 15: sources by document, and web sources ----

SCOPE_NOTE = {"kind": "scope", "documents": [{"ref": 1, "docId": A2, "name": "Alpha.md"},
                                             {"ref": 2, "docId": B, "name": "Beta.md"}]}


def _event(events, kind):
    return next(e for e in events if e["type"] == kind)


def _last(kinds, kind):
    """The position of the last `kind`: a multi-step turn has several text-end events, and the notes come after the final one."""
    return len(kinds) - 1 - kinds[::-1].index(kind)


def _notes(row):
    return (row[5] or {}).get("notes", [])


async def test_project_turn_saves_scope_and_sources_notes(db_conn, monkeypatch):
    router = FakeRouter(steps=[_search_step(0), reply("Both cover ZEPHYR-9 (Alpha.md, page 1; Beta.md, page 4).")])
    events, claim = await _project_turn(db_conn, monkeypatch, router)
    # The scope note is the first thing the client gets, in data-context, before any model step.
    kinds = types(events)
    assert kinds.index("data-context") < kinds.index("start-step")
    assert _event(events, "data-context")["items"] == [SCOPE_NOTE]
    # The sources arrive after the answer is written, before `finish`; one entry per document.
    sources = {"documents": [{"docId": A2, "docName": "Alpha.md", "pages": [1], "used": True},
                             {"docId": B, "docName": "Beta.md", "pages": [4], "used": True}]}
    event = _event(events, "data-sources")
    assert {k: v for k, v in event.items() if k not in ("type", "seq")} == sources
    assert _last(kinds, "text-end") < kinds.index("data-sources") < kinds.index("finish")
    assert "data-web-sources" not in kinds
    row = await _msg(db_conn, claim.turn_id)
    assert _notes(row) == [SCOPE_NOTE, {"kind": "sources", **sources}]


async def test_a_project_turn_without_document_text_saves_only_the_scope(db_conn, monkeypatch):
    events, claim = await _project_turn(db_conn, monkeypatch, FakeRouter(steps=[reply("Hello!")]))
    assert "data-sources" not in types(events)
    assert _notes(await _msg(db_conn, claim.turn_id)) == [SCOPE_NOTE]


async def test_a_citation_of_an_unknown_name_makes_no_chip(db_conn, monkeypatch):
    router = FakeRouter(steps=[_search_step(0), reply("It is in (Gamma.md, page 2) and (Alpha.md (9), page 1).")])
    events, claim = await _project_turn(db_conn, monkeypatch, router)
    documents = _event(events, "data-sources")["documents"]
    assert [d["docName"] for d in documents] == ["Alpha.md", "Beta.md"]          # searched, in the scope's order
    assert not any(d["used"] for d in documents)                                  # nothing cited that it was given
    assert [n for n in _notes(await _msg(db_conn, claim.turn_id)) if n["kind"] == "sources"][0]["documents"] == documents


async def test_a_project_turn_with_no_documents_still_saves_an_empty_scope(db_conn, monkeypatch):
    from server.tests import seed

    shim_pool(monkeypatch, db_conn, store, orchestrator, ctx_mod, sd_tool)
    alice = await member(db_conn, "alice")
    pid = await seed.make_project(db_conn, alice.user_id, "Empty")
    claim = await store.begin_turn(session_id="s-1", user_id=alice.user_id, model_id="ollama:m", text="q",
                                   attachments=[], new_session_pins=None, scope_project_id=pid)
    req = TurnRequest(user_id=alice.user_id, session_id="s-1", model_id="ollama:m", text="q", attachments=(),
                      settings=CallSettings(), doc_id=None, timezone="UTC", project_id=pid)
    events = [e async for e in run_turn(req, claim, router=FakeRouter(), cfg=ChatConfig(), deployment_budget=None)]
    assert _event(events, "data-context")["items"] == [{"kind": "scope", "documents": []}]
    assert "data-sources" not in types(events)


def _web_turn_steps(answer, results=(1, 2), query="port"):
    return [[ToolCallReady(ToolCall("w1", "web_search", {"query": query})), Usage(5, 5), Finish("tool_calls")],
            [ToolCallReady(ToolCall("w2", "read_web_pages", {"results": list(results), "need": "the port"})),
             Usage(5, 5), Finish("tool_calls")],
            reply(answer)]


async def test_web_pages_with_facts_become_web_sources(conn, web, use_router, web_auto):
    web.searches["port"] = [hit("a"), hit("b")]
    web.pages[url("a")] = web.pages[url("b")] = PAGE
    use_router(by_label({"[web 2]": ("The port is 8080", "set port = 8080")}))        # page 1 holds no fact
    events, claim = await _run(conn, FakeRouter(steps=_web_turn_steps("It is 8080 (b.test).")), text="Which port?")
    kinds = types(events)
    pages = [{"n": 2, "title": "Title b", "url": url("b")}]
    event = _event(events, "data-web-sources")
    assert {k: v for k, v in event.items() if k not in ("type", "seq")} == {"pages": pages}
    assert _last(kinds, "text-end") < kinds.index("data-web-sources") < kinds.index("finish")
    assert "data-sources" not in kinds                                                # no document in this chat
    assert _notes(await _msg(conn, claim.turn_id)) == [{"kind": "web_sources", "pages": pages}]


async def test_a_page_that_was_searched_but_not_read_is_no_web_source(conn, web, use_router, web_auto):
    web.searches["port"] = [hit("a"), hit("b")]
    steps = [_web_turn_steps("Not found.")[0], reply("The search only listed pages.")]
    events, claim = await _run(conn, FakeRouter(steps=steps), text="Which port?")
    assert "data-web-sources" not in types(events)
    assert _notes(await _msg(conn, claim.turn_id)) == []


async def test_a_javascript_url_is_never_a_web_source(conn, web, use_router, web_auto):
    """A search engine can list anything; a link the client renders must never run script."""
    evil = {"title": "Evil", "url": "javascript:alert(1)", "snippet": "s"}
    web.searches["port"] = [evil, hit("b")]
    web.pages["javascript:alert(1)"] = web.pages[url("b")] = PAGE
    use_router(by_label({"[web 1]": ("The port is 8080", "set port = 8080"),
                         "[web 2]": ("The port is 8080", "set port = 8080")}))
    events, claim = await _run(conn, FakeRouter(steps=_web_turn_steps("It is 8080 (b.test).")), text="Which port?")
    pages = [{"n": 2, "title": "Title b", "url": url("b")}]
    assert _event(events, "data-web-sources")["pages"] == pages
    assert "javascript" not in json.dumps(_notes(await _msg(conn, claim.turn_id)))


async def test_only_unsafe_urls_means_no_web_sources_event_at_all(conn, web, use_router, web_auto):
    web.searches["port"] = [{"title": "Evil", "url": "data:text/html,<b>x</b>", "snippet": "s"}]
    web.pages["data:text/html,<b>x</b>"] = PAGE
    use_router(by_label({"[web 1]": ("The port is 8080", "set port = 8080")}))
    events, claim = await _run(conn, FakeRouter(steps=_web_turn_steps("It is 8080.", results=(1,))), text="Which port?")
    assert "data-web-sources" not in types(events)
    assert _notes(await _msg(conn, claim.turn_id)) == []


async def test_a_result_dropped_for_the_budget_is_never_a_web_source(conn, web, use_router, web_auto):
    """TurnTools throws a result that doesn't fit unseen and takes back the `read` mark: the model never got those
    facts, so the answer can't have drawn on the page."""
    fact = "The port is set in server.toml, " + "and the manual repeats it at length. " * 14
    use_router(by_label({"[web 1]": (fact, "set port = 8080")}))
    web.searches["port"] = [hit(n) for n in "abcde"]
    web.pages[url("a")] = PAGE
    events, claim = await _run(conn, FakeRouter(steps=_web_turn_steps("Unsure.", results=(1,))),
                               cfg=ChatConfig(tool_result_budget_chars=1200), text="Which port?")
    read = [e for e in events if e["type"] == "tool-output-available"][1]
    assert read["output"]["summary_text"] == TurnTools.BUDGET_USED
    assert "data-web-sources" not in types(events)
    assert _notes(await _msg(conn, claim.turn_id)) == []


# ---- the reader's model calls are charged to the user (final review I1) ----

def _spy_charges(monkeypatch) -> list:
    """Every (user_id, BatchCall) the turn hands to its reader charge; the real charge still runs."""
    seen = []
    real = orchestrator._charge_reader

    async def spy(user_id, call):
        seen.append((user_id, call))
        await real(user_id, call)

    monkeypatch.setattr(orchestrator, "_charge_reader", spy)
    return seen


async def _usage(conn, user_id):
    return await _one(conn, "SELECT prompt_tokens, eval_tokens, requests FROM inference_usage WHERE user_id=%s",
                      (user_id,))


async def test_a_read_web_pages_turn_charges_its_reader_calls_to_the_user(conn, web, use_router, web_auto, monkeypatch):
    """Each page read is one reader call: the provider's own count is added to the user's totals for the day,
    one request each, with the reader model named. The reply's own usage stays the chat model's."""
    monkeypatch.setenv("READER_MODEL", "reader-model")
    web.searches["port"] = [hit("a"), hit("b")]
    web.pages[url("a")] = web.pages[url("b")] = PAGE
    use_router(by_label({"[web 2]": ("The port is 8080", "set port = 8080")})).usage = Usage(400, 30)
    charged = _spy_charges(monkeypatch)
    alice = await member(conn, "alice")
    events, _ = await _run(conn, FakeRouter(steps=_web_turn_steps("It is 8080 (b.test).")), user=alice,
                           text="Which port?")
    assert [(user, call.model, call.usage) for user, call in charged] == [(alice.user_id, "reader-model", Usage(400, 30))] * 2
    # Chat steps 5+5, 5+5 and 10+3 (three requests); reader 2 × (400+30) (two requests).
    assert await _usage(conn, alice.user_id) == (20 + 800, 13 + 60, 5)
    assert events[-1]["usage"] == {"promptTokens": 20, "completionTokens": 13, "estimated": False}


async def test_an_extract_from_documents_turn_charges_its_reader_calls_to_the_user(db_conn, monkeypatch, web, use_router):
    monkeypatch.setenv("READER_MODEL", "reader-model")
    shim_pool(monkeypatch, db_conn, ex_tool)
    use_router(by_label({"[1, page 1]": ("Alpha uses ZEPHYR-9 nodes", "ZEPHYR-9 nodes")})).usage = Usage(900, 50)
    charged = _spy_charges(monkeypatch)
    steps = [[ToolCallReady(ToolCall("x1", "extract_from_documents", {"documents": [1], "need": "the node type"})),
              Usage(5, 5), Finish("tool_calls")],
             reply("Alpha uses ZEPHYR-9 nodes (Alpha.md, page 1).")]
    events, claim = await _project_turn(db_conn, monkeypatch, FakeRouter(steps=steps))
    assert next(e for e in events if e["type"] == "tool-output-available")["output"]["summary_text"] == (
        "Found 1 fact on page 1 of Alpha.md.")
    [(user, call)] = charged
    assert (call.model, call.usage, call.finished) == ("reader-model", Usage(900, 50), True)
    assert await _usage(db_conn, user) == (5 + 10 + 900, 5 + 3 + 50, 3)


async def test_reader_usage_counts_toward_the_daily_budget(conn, web, use_router, web_auto):
    """Budget 100 tokens. The first step spends 10; the read's two reader calls spend 860, and the read runs to the
    end (no refusal in the middle of a tool). The next step's own check then stops the turn. Without the reader's
    charge the turn would have spent 20 and gone on."""
    web.searches["port"] = [hit("a"), hit("b")]
    web.pages[url("a")] = web.pages[url("b")] = PAGE
    use_router(by_label({"[web 2]": ("The port is 8080", "set port = 8080")})).usage = Usage(400, 30)
    alice = await member(conn, "alice")
    await conn.execute("UPDATE users SET inference_daily_token_budget = 100 WHERE id = %s", (alice.user_id,))
    router = FakeRouter(steps=_web_turn_steps("It is 8080 (b.test)."))
    events, claim = await _run(conn, router, user=alice, text="Which port?")
    read = [e for e in events if e["type"] == "tool-output-available"][1]
    assert read["output"]["summary_text"].startswith("Found facts on 1 of 2 page(s)")
    assert (events[-1]["type"], events[-1]["code"]) == ("error", "budget_exhausted")
    assert len(router.calls) == 2
    assert await _usage(conn, alice.user_id) == (10 + 800, 10 + 60, 4)


SENT = (Message("system", "x" * 400), Message("user", "y" * 401))     # 801 characters: an estimate of 201 tokens


@pytest.mark.parametrize("call,row", [
    # A finished call: the provider's own count, else the chars/4 estimate _record_usage makes for a chat step.
    (BatchCall("m", SENT, "z" * 10, Usage(500, 20), True), (500, 20, 1)),
    (BatchCall("m", SENT, "z" * 10, None, True), (201, 3, 1)),
    # One that failed or timed out: only if it produced something (_billable), the count if one came.
    (BatchCall("m", SENT, "", None, False), None),
    (BatchCall("m", SENT, "z" * 5, None, False), (201, 2, 1)),
    (BatchCall("m", SENT, "", Usage(300, 0), False), (300, 0, 1)),
])
async def test_the_reader_charge_follows_the_chat_steps_rule(conn, call, row):
    alice = await member(conn, "alice")
    await orchestrator._charge_reader(alice.user_id, call)
    assert await _usage(conn, alice.user_id) == row


async def test_a_project_turn_has_document_sources_then_web_sources(db_conn, monkeypatch, web, use_router):
    monkeypatch.setenv("CHAT_WEB_SEARCH", "auto")
    web.searches["port"] = [hit("a")]
    web.pages[url("a")] = PAGE
    use_router(by_label({"[web 1]": ("The port is 8080", "set port = 8080")}))
    steps = [_search_step(0), *_web_turn_steps("Alpha says so (Alpha.md, page 1); the web says 8080 (a.test).",
                                                results=(1,))]
    events, claim = await _project_turn(db_conn, monkeypatch, FakeRouter(steps=steps))
    kinds = types(events)
    assert kinds.index("data-sources") < kinds.index("data-web-sources") < kinds.index("finish")
    assert [n["kind"] for n in _notes(await _msg(db_conn, claim.turn_id))] == ["scope", "sources", "web_sources"]
    assert _event(events, "data-sources")["documents"][0]["docName"] == "Alpha.md"


async def test_a_turn_that_ends_with_an_offer_still_gets_its_sources(db_conn, monkeypatch):
    """The offer ends the turn (no further model step), but the document pages it read before are still sources:
    data-sources follows data-web-offer, before `finish`, and the notes are saved in that order."""
    from server.services import web_search as ws

    monkeypatch.setattr(ws, "SEARXNG_URL", "http://searx.test")
    monkeypatch.delenv("CHAT_WEB_SEARCH", raising=False)                      # the default: ask
    offer = {"query": "ZEPHYR-9 rollout dates", "reason": "The documents give no dates."}
    router = FakeRouter(steps=[
        _search_step(0),
        [TextDelta("Alpha covers ZEPHYR-9 (Alpha.md, page 1), but no dates. "),
         ToolCallReady(ToolCall("o1", "offer_web_search", offer)), Usage(5, 5), Finish("tool_calls")],
        reply("SHOULD NEVER BE SENT")])
    events, claim = await _project_turn(db_conn, monkeypatch, router)
    kinds = types(events)
    assert len(router.calls) == 2                                              # the offer ended the turn
    assert _event(events, "data-web-offer")["query"] == offer["query"]
    assert kinds.index("data-web-offer") < kinds.index("data-sources") < kinds.index("finish")
    documents = _event(events, "data-sources")["documents"]
    assert [(d["docName"], d["pages"], d["used"]) for d in documents] == [("Alpha.md", [1], True), ("Beta.md", [4], False)]
    assert [n["kind"] for n in _notes(await _msg(db_conn, claim.turn_id))] == ["scope", "web_offer", "sources"]


async def test_web_sources_come_with_a_document_scope_chat_too(conn, open_doc, web, use_router, web_auto):
    """Any scope: a chat about one open document that reads the web saves the web sources, and no document sources
    are invented for it (nothing was read from the document)."""
    web.searches["port"] = [hit("a"), hit("b")]
    web.pages[url("a")] = web.pages[url("b")] = PAGE
    use_router(by_label({"[web 1]": ("The port is 8080", "set port = 8080")}))
    events, claim = await _run(conn, FakeRouter(steps=_web_turn_steps("It is 8080 (a.test).", results=(1,))),
                               text="Which port?")
    kinds = types(events)
    pages = [{"n": 1, "title": "Title a", "url": url("a")}]
    assert _event(events, "data-web-sources")["pages"] == pages
    assert _last(kinds, "text-end") < kinds.index("data-web-sources") < kinds.index("finish")
    assert "data-sources" not in kinds
    assert _notes(await _msg(conn, claim.turn_id)) == [{"kind": "web_sources", "pages": pages}]
