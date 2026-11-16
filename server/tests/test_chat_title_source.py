"""Chat titles, Task 1 (helper spec §6): every write of a chat's title says who wrote it, and session rows say so and
whether a title is being written."""
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.chat import store
from server.routers import chat_sessions as chat_router
from server.tests.chat_harness import member, shim_pool


@pytest.fixture
def app(db_conn, monkeypatch):
    shim_pool(monkeypatch, db_conn, store, chat_router)
    application = FastAPI()
    application.include_router(chat_router.router)
    current: dict = {}
    application.dependency_overrides[deps.get_current_user] = lambda: current["p"]
    return application, current


def _client(app, principal):
    application, current = app
    current["p"] = principal
    return httpx.AsyncClient(transport=ASGITransport(app=application), base_url="http://t")


async def _source(conn, sid):
    cur = await conn.execute("SELECT title, title_source FROM chat_sessions WHERE id = %s", (sid,))
    return await cur.fetchone()


async def _turn(user, text, sid="s-1"):
    return await store.begin_turn(session_id=sid, user_id=user.user_id, model_id="ollama:m", text=text,
                                  attachments=[], new_session_pins=None)


async def test_a_first_turn_marks_its_cut_title(db_conn, app):
    alice = await member(db_conn, "alice")
    await _turn(alice, "Tell me about budgets")
    assert await _source(db_conn, "s-1") == ("Tell me about budgets", "cut")


async def test_an_attachment_only_first_turn_stays_default(db_conn, app):
    alice = await member(db_conn, "alice")
    await _turn(alice, "")
    assert await _source(db_conn, "s-1") == ("New chat", "default")


async def test_a_later_turn_leaves_the_source(db_conn, app):
    alice = await member(db_conn, "alice")
    claim = await _turn(alice, "First")
    await db_conn.execute("UPDATE chat_sessions SET title_source = 'model', active_turn_id = NULL")
    await _turn(alice, "Second")
    assert await _source(db_conn, "s-1") == ("First", "model")
    assert claim.created_session


async def test_a_rename_marks_person(db_conn, app):
    alice = await member(db_conn, "alice")
    await _turn(alice, "Tell me about budgets")
    async with _client(app, alice) as c:
        assert (await c.patch("/v1/chat/sessions/s-1", json={"title": "Mine"})).status_code == 200
    assert await _source(db_conn, "s-1") == ("Mine", "person")


async def test_a_model_or_pins_patch_leaves_the_source(db_conn, app):
    alice = await member(db_conn, "alice")
    await _turn(alice, "Tell me about budgets")
    async with _client(app, alice) as c:
        await c.patch("/v1/chat/sessions/s-1", json={"model": "ollama:x"})
        await c.patch("/v1/chat/sessions/s-1", json={"pins": []})
    assert await _source(db_conn, "s-1") == ("Tell me about budgets", "cut")


async def test_an_imported_chat_with_a_title_is_person(db_conn, app):
    alice = await member(db_conn, "alice")
    async with _client(app, alice) as c:
        named = (await c.post("/v1/chat/sessions/import", json={"title": "Old chat"})).json()["id"]
        unnamed = (await c.post("/v1/chat/sessions/import", json={"title": "New chat"})).json()["id"]
    assert await _source(db_conn, named) == ("Old chat", "person")
    assert await _source(db_conn, unnamed) == ("New chat", "default")


async def test_session_rows_say_who_wrote_the_title_and_whether_one_is_coming(db_conn, app):
    alice = await member(db_conn, "alice")
    await _turn(alice, "Tell me about budgets")

    async def seen():
        async with _client(app, alice) as c:
            row = (await c.get("/v1/chat/sessions")).json()[0]
            one = (await c.get("/v1/chat/sessions/s-1")).json()
        assert (row["titleSource"], row["titlePending"]) == (one["titleSource"], one["titlePending"])
        return row["titleSource"], row["titlePending"]

    assert await seen() == ("cut", False)
    await db_conn.execute("UPDATE chat_sessions SET title_pending_at = now()")
    assert await seen() == ("cut", True)
    await db_conn.execute("UPDATE chat_sessions SET title_pending_at = now() - interval '4 minutes'")
    assert await seen() == ("cut", False)                       # a restart mid-call: given up after 3 minutes
