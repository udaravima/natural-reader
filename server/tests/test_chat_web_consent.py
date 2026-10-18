"""C2.1 Task 13: web search asks first (spec section 5.8).

Three modes, chosen by the admin (services/chat_options.py): "ask" (the default), "auto" and "off". In ask mode
the model's only web tool is offer_web_search: calling it ends the turn with an offer card, and the user's yes
comes back as the next turn's `context.web`, which makes the APP run that search before the model's first step.
The rule is enforced by which tools are offered, not by the prompt.

No network and no model: the search engine is scripted, the model is a FakeRouter."""
from __future__ import annotations

import json
import logging
import re

import httpx
import pytest

from server.chat import context as ctx_mod
from server.chat import orchestrator, store
from server.chat.config import ChatConfig
from server.chat.orchestrator import TurnRequest, run_turn
from server.chat.tools import ToolContext, run_tool
from server.chat.tools import search_documents as sd_tool
from server.chat.tools import web_search as ws_tool
from server.chat.web_consent import app_web_search
from server.llm.types import CallSettings, Finish, TextDelta, ToolCall, ToolCallReady, Usage
from server.services import chat_options
from server.services import web_search as ws
from server.tests.chat_harness import FakeRouter, member, reply, shim_pool
from server.tests.test_chat_orchestrator import _msg, open_doc  # noqa: F401 — open_doc is a fixture
from server.routers import chat_turns
from server.tests.test_chat_project_scope import _two_doc_project
from server.tests.test_chat_turns import BODY, _frames, _post, app  # noqa: F401 — app is a fixture

OFFER = {"query": "RHEL 9 python 3.13 packages", "reason": "The documents don't cover RHEL."}
WEB_TOOLS = ["web_search", "read_web_pages"]
DOC_TOOLS = ["search_documents", "read_document_pages", "extract_from_documents"]


class Engine:
    """A stand-in SearXNG: every query finds one page of its own, and the queries it was sent are kept."""

    def __init__(self) -> None:
        self.queries: list[str] = []
        self.fail: Exception | None = None

    async def __call__(self, query: str, count: int) -> dict:
        self.queries.append(query)
        if self.fail:
            raise self.fail
        slug = re.sub(r"\W+", "-", query).strip("-")[:40]
        return {"query": query, "results": [{"title": f"About {query}", "url": f"https://site{len(self.queries)}.test/{slug}",
                                             "snippet": "A snippet."}]}


@pytest.fixture
def conn(db_conn, monkeypatch):
    shim_pool(monkeypatch, db_conn, store, orchestrator, ctx_mod, sd_tool)
    monkeypatch.delenv("CHAT_WEB_SEARCH", raising=False)
    return db_conn


@pytest.fixture
def engine(monkeypatch):
    e = Engine()
    monkeypatch.setattr(ws, "SEARXNG_URL", "http://searx.test")
    monkeypatch.setattr(ws_tool, "web_search", e)
    return e


async def set_mode(conn, mode, user):
    await chat_options.save_chat_options(conn, mode, updated_by=user.user_id)


async def turn(conn, router, text="Hi", *, web=None, user=None, doc_id=None, cfg=ChatConfig(), pins=None,
               project_id=None, sid="s-1"):
    user = user or await member(conn, "alice")
    claim = await store.begin_turn(session_id=sid, user_id=user.user_id, model_id="ollama:m", text=text,
                                   attachments=[], new_session_pins=pins, scope_project_id=project_id)
    req = TurnRequest(user_id=user.user_id, session_id=sid, model_id="ollama:m", text=text, attachments=(),
                      settings=CallSettings(), doc_id=doc_id, timezone="UTC", project_id=project_id, web_query=web)
    events = [e async for e in run_turn(req, claim, router=router, cfg=cfg, deployment_budget=None)]
    return events, claim


def offer_call(**args):
    return [ToolCallReady(ToolCall("o1", "offer_web_search", args or OFFER)), Usage(5, 5), Finish("tool_calls")]


def types(events):
    return [e["type"] for e in events]


def app_exchange(call):
    """The tool messages of the app's own web search (call id app-web-1), as the model was sent them."""
    return [m for m in call["messages"] if getattr(m, "tool_call_id", None) == "app-web-1"
            or any(c.id == "app-web-1" for c in (m.tool_calls or ()))]


# ---- ask: the default ----

async def test_ask_mode_offers_only_offer_web_search(conn, engine):
    router = FakeRouter()
    await turn(conn, router)
    assert router.calls[0]["tools"] == ["offer_web_search"]       # not web_search, not read_web_pages


async def test_ask_mode_keeps_the_document_tools_and_adds_the_offer(conn, engine, open_doc):
    router = FakeRouter()
    await turn(conn, router, doc_id="d" * 64)
    assert router.calls[0]["tools"] == [*DOC_TOOLS, "offer_web_search"]


async def test_the_offer_is_not_offered_without_a_search_engine(conn, engine, monkeypatch):
    monkeypatch.setattr(ws, "SEARXNG_URL", "")
    for mode in ("ask", "auto"):
        router = FakeRouter()
        await set_mode(conn, mode, await member(conn, "alice"))
        await turn(conn, router, sid=f"s-{mode}")
        assert router.calls[0]["tools"] == [], mode


async def test_offer_ends_the_turn_and_saves_the_note(conn, engine):
    router = FakeRouter(steps=[[TextDelta("The documents don't cover RHEL. "), *offer_call()],
                               reply("SHOULD NEVER BE SENT")])
    events, claim = await turn(conn, router)
    assert len(router.calls) == 1                                  # no further model step
    assert types(events) == ["start", "start-step", "text-start", "text-delta", "text-end", "finish-step",
                             "tool-input-available", "tool-output-available", "data-web-offer", "finish"]
    offer = next(e for e in events if e["type"] == "data-web-offer")
    assert (offer["query"], offer["reason"]) == (OFFER["query"], OFFER["reason"])
    assert events[-1]["finishReason"] == "stop"
    status, reason, content, _, tool_calls, doc_context, _ = await _msg(conn, claim.turn_id)
    assert (status, reason) == ("complete", "stop")
    assert content == "The documents don't cover RHEL. "          # the model's own text, nothing added
    assert doc_context == {"notes": [{"kind": "web_offer", **OFFER}]}
    assert tool_calls == [{"name": "offer_web_search", "arguments": OFFER, "result_summary": {
        "ok": True, "chunk_count": None, "query": OFFER["query"], "summary_text": "Offered to the user; they decide."}}]
    assert engine.queries == []                                    # nothing went out


async def test_an_offer_with_no_text_adds_none(conn, engine):
    router = FakeRouter(steps=[offer_call()])
    events, claim = await turn(conn, router)
    assert "text-delta" not in types(events)
    assert (await _msg(conn, claim.turn_id))[2] == ""


async def test_other_calls_in_the_offer_round_still_run(conn, engine, open_doc):
    router = FakeRouter(steps=[[ToolCallReady(ToolCall("c1", "search_documents", {"query": "rhel"})),
                                ToolCallReady(ToolCall("o1", "offer_web_search", OFFER)),
                                Usage(5, 5), Finish("tool_calls")]])
    events, claim = await turn(conn, router, doc_id="d" * 64)
    assert len(router.calls) == 1
    outputs = [e["toolCallId"] for e in events if e["type"] == "tool-output-available"]
    assert outputs == ["c1", "o1"]
    assert types(events)[-2:] == ["data-web-offer", "finish"]
    saved = (await _msg(conn, claim.turn_id))[4]
    assert [c["name"] for c in saved] == ["search_documents", "offer_web_search"]


async def test_an_offer_the_guard_refuses_does_not_end_the_turn(conn, engine, open_doc):
    """A query copied from the document is an error the model reads, not an offer the user sees."""
    copied = "the quick brown fox jumps over the lazy dog near the old river bank"
    router = FakeRouter(steps=[offer_call(query=copied, reason="Because."), reply("Done.")])
    events, _ = await turn(conn, router, pins=[{"kind": "selection", "text": copied, "page": 1, "fileName": "a.pdf"}])
    assert "data-web-offer" not in types(events) and len(router.calls) == 2
    assert json.loads(router.calls[1]["messages"][-1].content) == {"error": ws_tool.COPIED}


# ---- the yes: context.web ----

async def test_approved_turn_runs_the_users_query_first_and_offers_web_tools(conn, engine):
    router = FakeRouter(steps=[[ToolCallReady(ToolCall("c1", "web_search", {"query": "second look"})),
                                Usage(5, 5), Finish("tool_calls")], reply("Here you go.")])
    events, claim = await turn(conn, router, text="Search the web for: RHEL 9 packages", web="RHEL 9 packages")
    assert engine.queries == ["RHEL 9 packages", "second look"]    # the app's search came first
    assert types(events)[:4] == ["start", "tool-input-available", "tool-output-available", "start-step"]
    app_in, app_out = events[1], events[2]
    assert (app_in["toolCallId"], app_in["toolName"], app_in["input"]) == ("app-web-1", "web_search",
                                                                          {"query": "RHEL 9 packages"})
    assert app_out["toolCallId"] == "app-web-1"
    assert app_out["output"]["summary_text"] == 'Web search for "RHEL 9 packages" returned 1 result(s).'
    # The model's first step already has the exchange: a call, then its result.
    first = router.calls[0]
    assert first["tools"] == WEB_TOOLS                             # web tools for this turn; no offer
    call_msg, result_msg = first["messages"][-2:]
    assert call_msg.role == "assistant" and call_msg.tool_calls[0].id == "app-web-1"
    assert call_msg.tool_calls[0].name == "web_search" and call_msg.tool_calls[0].arguments == {"query": "RHEL 9 packages"}
    assert result_msg.role == "tool" and result_msg.tool_call_id == "app-web-1" and result_msg.name == "web_search"
    result = json.loads(result_msg.content)
    assert [r["n"] for r in result["results"]] == [1] and result["results"][0]["url"].startswith("https://site1.test/")
    # Its results are numbered into the turn's: the model's own search carries on from 2.
    second = json.loads(router.calls[1]["messages"][-1].content)
    assert [r["n"] for r in second["results"]] == [2]
    # Saved like any tool call, the app's first.
    saved = (await _msg(conn, claim.turn_id))[4]
    assert [c["name"] for c in saved] == ["web_search", "web_search"] and saved[0]["arguments"] == {"query": "RHEL 9 packages"}


async def test_the_exchange_follows_the_users_message(conn, engine):
    router = FakeRouter()
    await turn(conn, router, text="Search the web for: x", web="x")
    roles = [m.role for m in router.calls[0]["messages"]]
    assert roles == ["system", "user", "assistant", "tool"]


async def test_approval_lasts_one_turn(conn, engine):
    router = FakeRouter()
    await turn(conn, router, web="RHEL 9 packages")
    await turn(conn, router, text="And another thing")
    assert router.calls[0]["tools"] == WEB_TOOLS
    assert router.calls[1]["tools"] == ["offer_web_search"]        # asks again
    assert engine.queries == ["RHEL 9 packages"]


async def test_approved_query_skips_the_copy_guard_but_model_queries_keep_it(conn, engine):
    copied = "the quick brown fox jumps over the lazy dog near the old river bank"
    pins = [{"kind": "selection", "text": copied, "page": 1, "fileName": "a.pdf"}]
    router = FakeRouter(steps=[[ToolCallReady(ToolCall("c1", "web_search", {"query": copied})),
                                Usage(5, 5), Finish("tool_calls")], reply("Done.")])
    events, _ = await turn(conn, router, web=copied, pins=pins)
    assert engine.queries == [copied]                              # the user's query went out
    assert not any(e["type"] == "tool-output-error" and e["toolCallId"] == "app-web-1" for e in events)
    # ... and the same words from the model were refused.
    assert json.loads(router.calls[1]["messages"][-1].content) == {"error": ws_tool.COPIED}


async def test_the_apps_search_skips_the_guard_whatever_the_turn_has_seen(engine):
    """At the moment the orchestrator runs it, the turn has seen no document text yet; this holds the helper to
    the rule on its own: the user's query is theirs, a model's is not, and the numbering is the turn's."""
    copied = "the quick brown fox jumps over the lazy dog near the old river bank"
    ctx = ToolContext("u", None, web_mode="ask", web_approved=True)
    ctx.seen_text.append(copied)
    run = await app_web_search(ctx, copied)
    assert run.ok and run.call_id == "app-web-1" and engine.queries == [copied]
    assert sorted(ctx.web_results) == [1] and ctx.web_results[1]["query"] == copied
    model = await run_tool(ToolCall("c1", "web_search", {"query": copied}), ctx, [ws_tool.TOOL])
    assert model.result == {"error": ws_tool.COPIED} and engine.queries == [copied]


async def test_a_failing_search_engine_leaves_an_error_exchange_and_the_turn_goes_on(conn, engine, caplog):
    engine.fail = httpx.ConnectError("secret-host.internal refused")
    router = FakeRouter()
    with caplog.at_level(logging.DEBUG):
        events, claim = await turn(conn, router, web="RHEL 9 packages")
    out = next(e for e in events if e["type"] == "tool-output-error")
    assert out["toolCallId"] == "app-web-1" and out["errorText"] == "web_search failed: ConnectError"
    assert json.loads(router.calls[0]["messages"][-1].content) == {"error": "web_search failed: ConnectError"}
    assert router.calls[0]["tools"] == WEB_TOOLS                   # the model may try again
    assert types(events)[-1] == "finish" and "error" not in types(events)
    assert (await _msg(conn, claim.turn_id))[0] == "complete"
    assert "RHEL 9 packages" not in " ".join(r.getMessage() for r in caplog.records if r.levelno >= logging.INFO)


async def test_an_approved_turn_without_a_search_engine_does_not_search(conn, engine, monkeypatch):
    monkeypatch.setattr(ws, "SEARXNG_URL", "")
    router = FakeRouter()
    events, _ = await turn(conn, router, web="anything")
    assert engine.queries == [] and router.calls[0]["tools"] == []
    assert "tool-input-available" not in types(events) and "error" not in types(events)


# ---- auto and off ----

async def test_auto_mode_offers_web_tools_without_offer(conn, engine):
    await set_mode(conn, "auto", await member(conn, "alice"))
    router = FakeRouter()
    await turn(conn, router)
    assert router.calls[0]["tools"] == WEB_TOOLS
    assert engine.queries == []                                    # nothing runs until the model asks


async def test_auto_mode_still_runs_an_approved_query_itself(conn, engine):
    await set_mode(conn, "auto", await member(conn, "alice"))
    router = FakeRouter()
    await turn(conn, router, web="typed by the user")
    assert engine.queries == ["typed by the user"] and len(app_exchange(router.calls[0])) == 2


async def test_off_mode_offers_no_web_tool_even_with_context_web(conn, engine, caplog):
    await set_mode(conn, "off", await member(conn, "alice"))
    router = FakeRouter()
    with caplog.at_level(logging.INFO):
        events, _ = await turn(conn, router, web="PRIVATE-QUERY-TEXT")
    assert router.calls[0]["tools"] == []
    assert engine.queries == []                                    # no search ...
    assert "tool-input-available" not in types(events) and "error" not in types(events)   # ... and no error
    assert app_exchange(router.calls[0]) == []
    notes = [r for r in caplog.records if r.levelno == logging.INFO and "web" in r.getMessage().lower()
             and "ignored" in r.getMessage()]
    assert notes and "PRIVATE-QUERY-TEXT" not in caplog.text       # logged, without the query


async def test_mode_change_applies_on_the_next_turn(conn, engine):
    alice = await member(conn, "alice")
    router = FakeRouter()
    await turn(conn, router)                                       # default
    await set_mode(conn, "auto", alice)
    await turn(conn, router)
    await set_mode(conn, "off", alice)
    await turn(conn, router)
    await set_mode(conn, None, alice)                              # reset: the default again
    await turn(conn, router)
    assert [c["tools"] for c in router.calls] == [["offer_web_search"], WEB_TOOLS, [], ["offer_web_search"]]


async def test_the_environment_value_applies_when_the_admin_has_not_chosen(conn, engine, monkeypatch):
    monkeypatch.setenv("CHAT_WEB_SEARCH", "auto")
    router = FakeRouter()
    await turn(conn, router)
    assert router.calls[0]["tools"] == WEB_TOOLS
    await set_mode(conn, "off", await member(conn, "alice"))       # the admin's choice wins
    await turn(conn, router)
    assert router.calls[1]["tools"] == []


# ---- the options row is never a reason to fail a turn ----

async def test_an_options_row_error_falls_back_to_env_then_default(conn, engine, monkeypatch, caplog):
    async def broken(conn, env=None):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(chat_options, "load_chat_options", broken)
    router = FakeRouter()
    with caplog.at_level(logging.WARNING):
        events, claim = await turn(conn, router)
    assert router.calls[0]["tools"] == ["offer_web_search"]         # the default
    assert (await _msg(conn, claim.turn_id))[0] == "complete" and "error" not in types(events)
    assert any(r.levelno == logging.WARNING and "chat options" in r.getMessage().lower() for r in caplog.records)
    monkeypatch.setenv("CHAT_WEB_SEARCH", "auto")
    await turn(conn, router)
    assert router.calls[1]["tools"] == WEB_TOOLS                    # the environment, when there is one


# ---- every chat, document or project ----

async def test_a_document_chat_asks_too(conn, engine, open_doc):
    await set_mode(conn, "auto", await member(conn, "alice"))
    router = FakeRouter()
    await turn(conn, router, doc_id="d" * 64)
    assert router.calls[0]["tools"] == [*DOC_TOOLS, *WEB_TOOLS]


async def test_a_project_chat_asks_and_runs_an_approved_search(db_conn, monkeypatch, engine):
    shim_pool(monkeypatch, db_conn, store, orchestrator, ctx_mod, sd_tool)
    monkeypatch.delenv("CHAT_WEB_SEARCH", raising=False)
    alice, pid = await _two_doc_project(db_conn)
    router = FakeRouter()
    await turn(db_conn, router, user=alice, project_id=pid, sid="s-ask")
    await turn(db_conn, router, user=alice, project_id=pid, web="RHEL 9 packages", sid="s-ask")
    asked, approved = router.calls
    assert "offer_web_search" in asked["tools"] and "web_search" not in asked["tools"]
    assert "search_documents" in asked["tools"]
    assert "web_search" in approved["tools"] and "offer_web_search" not in approved["tools"]
    assert engine.queries == ["RHEL 9 packages"] and len(app_exchange(approved)) == 2


# ---- the route: context.web ----

@pytest.mark.parametrize("web", [{}, {"query": ""}, {"query": "   "}, {"query": "x" * 301}, {"query": 5},
                                 {"query": "ok", "extra": 1}, "RHEL"])
async def test_a_malformed_context_web_is_a_422(db_conn, app, web):
    r = await _post(app, await member(db_conn, "alice"), body={**BODY, "context": {"web": web}})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "invalid_request"


async def test_context_web_reaches_the_turn_and_the_app_searches_first(db_conn, app, engine, monkeypatch):
    monkeypatch.delenv("CHAT_WEB_SEARCH", raising=False)
    seen = {}
    real = chat_turns.run_turn

    def spy(req, *a, **kw):
        seen["web_query"] = req.web_query
        return real(req, *a, **kw)

    monkeypatch.setattr(chat_turns, "run_turn", spy)
    body = {**BODY, "message": {"content": "Search the web for: RHEL 9"},
            "context": {"timezone": "UTC", "web": {"query": "  RHEL 9 packages "}}}
    r = await _post(app, await member(db_conn, "alice"), body=body)
    frames = _frames(r.text)
    assert r.status_code == 200 and seen["web_query"] == "RHEL 9 packages"      # trimmed
    assert [f["type"] for f in frames[1:3]] == ["tool-input-available", "tool-output-available"]
    assert engine.queries == ["RHEL 9 packages"]
    assert app[1].calls[0]["tools"] == WEB_TOOLS


async def test_without_context_web_the_turn_has_no_query(db_conn, app, engine, monkeypatch):
    monkeypatch.delenv("CHAT_WEB_SEARCH", raising=False)
    r = await _post(app, await member(db_conn, "alice"))
    assert r.status_code == 200 and engine.queries == [] and app[1].calls[0]["tools"] == ["offer_web_search"]
