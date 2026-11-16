"""Chat titles, Task 3 (helper spec §6): the title is asked for when a chat's first complete reply ends, marked
pending before the turn's stream closes, and on request from the chat's Generate title."""
import asyncio

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from psycopg.types.json import Jsonb

from server.auth import deps
from server.chat import context as ctx_mod
from server.chat import orchestrator, store, titles
from server.chat.tools import search_documents as sd_tool
from server.routers import chat_sessions as sessions_router
from server.routers import chat_turns
from server.services import helper, inference_budget
from server.tests.chat_harness import FakeRouter, member, shim_pool

ON = helper.HelperConfig(model="ollama:h", timeout_s=5.0, input_chars=6000, num_ctx=None, local=True)
CLOUD = helper.HelperConfig(model="ollama:h-cloud", timeout_s=5.0, input_chars=6000, num_ctx=None, local=False)
OFF = helper.HelperConfig(model="", timeout_s=5.0, input_chars=6000, num_ctx=None, local=False)
BODY = {"message": {"content": "How much for hardware?"}, "model": "ollama:m", "context": {"timezone": "UTC"}}


@pytest.fixture
def started(monkeypatch):
    """Record the title jobs started, instead of running them: a pending mark then stays as start() left it."""
    calls = []

    async def fake_write(session_id, owner_id, *, on_request):
        calls.append((session_id, owner_id, on_request))
    monkeypatch.setattr(titles, "write_title", fake_write)
    return calls


@pytest.fixture
def helper_cfg(monkeypatch):
    state = {"cfg": ON, "local_chat": True}

    async def load(conn, env=None):
        return state["cfg"]

    async def is_local(conn, model_id):
        return state["local_chat"]
    monkeypatch.setattr(helper, "load_config", load)
    monkeypatch.setattr(helper, "chat_model_is_local", is_local)
    monkeypatch.delenv("INFERENCE_DAILY_TOKEN_BUDGET", raising=False)
    return state


@pytest.fixture
def app(db_conn, monkeypatch):
    shim_pool(monkeypatch, db_conn, store, orchestrator, ctx_mod, sd_tool, chat_turns, titles, helper,
              sessions_router)
    fake = FakeRouter()
    monkeypatch.setattr(chat_turns, "get_router", lambda: fake)
    application = FastAPI()
    application.include_router(chat_turns.router)
    application.include_router(sessions_router.router)
    current: dict = {}
    application.dependency_overrides[deps.get_current_user] = lambda: current["p"]
    return application, current


def _client(app, principal):
    application, current = app
    current["p"] = principal
    return httpx.AsyncClient(transport=ASGITransport(app=application), base_url="http://t")


async def _turn(app, principal, sid="s-1", text="How much for hardware?"):
    async with _client(app, principal) as c:
        r = await c.post(f"/v1/chat/sessions/{sid}/turns", json={**BODY, "message": {"content": text}})
    assert r.status_code == 200 and r.text.rstrip().endswith("[DONE]"), r.text
    return r


async def _settle():
    """Let the title jobs start() created run (here: the recording fake)."""
    await asyncio.gather(*list(titles._BACKGROUND))


async def _pending(conn, sid="s-1"):
    cur = await conn.execute(f"SELECT {titles.PENDING_SQL} FROM chat_sessions s WHERE s.id = %s", (sid,))
    return (await cur.fetchone())[0]


async def _seed(conn, owner, msgs, *, source="cut", sid="s-1"):
    await conn.execute("INSERT INTO chat_sessions (id, title, title_source, model, user_id) VALUES (%s,'t',%s,'m',%s)",
                       (sid, source, owner))
    for i, (role, content, status) in enumerate(msgs):
        await conn.execute(
            "INSERT INTO chat_messages (id, session_id, role, content, attachments, timestamp, status) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (f"m{i}" if sid == "s-1" else f"{sid}-m{i}", sid, role, content, Jsonb([]), i + 1, status))


async def test_only_the_first_complete_reply_titles(db_conn, app, helper_cfg, started):
    alice = await member(db_conn, "alice")
    await _seed(db_conn, alice.user_id, [("user", "Q1", "complete"), ("assistant", "", "error"),
                                         ("user", "Q2", "complete"), ("assistant", "A2", "complete")])
    await titles.after_reply("s-1", alice.user_id, "m1", "m")              # the failed reply: nothing
    assert started == []
    await titles.after_reply("s-1", alice.user_id, "m3", "m")              # the first complete one
    await _settle()
    assert started == [("s-1", alice.user_id, False)] and await _pending(db_conn) is True
    await db_conn.execute("UPDATE chat_sessions SET title_pending_at = NULL")
    await db_conn.execute("INSERT INTO chat_messages (id, session_id, role, content, attachments, timestamp, status) "
                          "VALUES ('m5', 's-1', 'assistant', 'A3', '[]', 9, 'complete')")
    await titles.after_reply("s-1", alice.user_id, "m5", "m")              # a later one: no
    await _settle()
    assert len(started) == 1


async def test_the_pending_mark_is_written_before_the_turn_ends(db_conn, app, helper_cfg, started):
    """The stream's [DONE] comes after the end of the turn is written: the browser's refresh then sees it."""
    alice = await member(db_conn, "alice")
    await _turn(app, alice)
    assert started == [("s-1", alice.user_id, False)]
    async with _client(app, alice) as c:
        assert (await c.get("/v1/chat/sessions/s-1")).json()["titlePending"] is True
    await db_conn.execute("UPDATE chat_sessions SET title_pending_at = NULL")
    await _turn(app, alice, text="And for staff?")
    assert len(started) == 1                                               # the second reply isn't the first


async def test_after_reply_never_raises(db_conn, app, helper_cfg, started, monkeypatch):
    alice = await member(db_conn, "alice")

    reached = []

    async def broken(*a, **k):
        reached.append(1)
        raise RuntimeError("down")
    monkeypatch.setattr(titles, "refusal", broken)
    await _turn(app, alice)
    assert reached == [1]                                                  # the title check ran, and failed
    cur = await db_conn.execute("SELECT status FROM chat_messages WHERE role = 'assistant'")
    assert await cur.fetchall() == [("complete",)]                         # the turn's end is saved all the same
    assert await _pending(db_conn) is False and started == []


@pytest.mark.parametrize("case", ["off", "cloud_helper_local_chat", "named", "pending"])
async def test_no_auto_title_when_refused_or_named(db_conn, app, helper_cfg, started, case):
    alice = await member(db_conn, "alice")
    await _seed(db_conn, alice.user_id, [("user", "Q1", "complete"), ("assistant", "A1", "complete")],
                source="person" if case == "named" else "cut")
    if case == "off":
        helper_cfg["cfg"] = OFF
    if case == "cloud_helper_local_chat":
        helper_cfg["cfg"] = CLOUD
    if case == "pending":
        await db_conn.execute("UPDATE chat_sessions SET title_pending_at = now()")
    await titles.after_reply("s-1", alice.user_id, "m1", "m")
    await _settle()
    assert started == []


async def test_a_cloud_helper_titles_a_cloud_chat(db_conn, app, helper_cfg, started):
    alice = await member(db_conn, "alice")
    helper_cfg.update(cfg=CLOUD, local_chat=False)
    await _seed(db_conn, alice.user_id, [("user", "Q1", "complete"), ("assistant", "A1", "complete")])
    await titles.after_reply("s-1", alice.user_id, "m1", "m")
    await _settle()
    assert started == [("s-1", alice.user_id, False)]


async def test_generate_title_route(db_conn, app, helper_cfg, started, monkeypatch):
    alice = await member(db_conn, "alice")
    bob = await member(db_conn, "bob")
    await _seed(db_conn, alice.user_id, [("user", "Q1", "complete"), ("assistant", "A1", "complete")],
                source="person")
    async with _client(app, alice) as c:
        r = await c.post("/v1/chat/sessions/s-1/title")
    await _settle()
    assert (r.status_code, r.json()) == (202, {"titlePending": True})
    assert started == [("s-1", alice.user_id, True)] and await _pending(db_conn) is True
    async with _client(app, bob) as c:
        assert (await c.post("/v1/chat/sessions/s-1/title")).status_code == 404

    async def code(**state):
        helper_cfg.update(state)
        async with _client(app, alice) as c:
            r = await c.post("/v1/chat/sessions/s-1/title")
        return r.status_code, r.json()["detail"]["error"]
    assert await code(cfg=OFF) == (409, "helper_off")
    assert await code(cfg=CLOUD, local_chat=True) == (409, "helper_is_cloud")
    helper_cfg.update(cfg=ON)
    monkeypatch.setattr(helper, "_deployment_budget", lambda: 10)
    await inference_budget.record_usage(db_conn, alice.user_id, 50, 0, model="ollama:x", kind="chat", estimated=False)
    assert await code() == (429, "budget_exhausted")
    monkeypatch.setattr(helper, "_deployment_budget", lambda: None)
    await _seed(db_conn, alice.user_id, [("assistant", "only a reply", "complete")], sid="s-2")
    async with _client(app, alice) as c:
        r = await c.post("/v1/chat/sessions/s-2/title")
    assert (r.status_code, r.json()["detail"]["error"]) == (409, "nothing_to_title")
    assert len(started) == 1
