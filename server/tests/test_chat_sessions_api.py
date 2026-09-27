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


async def test_get_reports_status_and_recovers_a_dead_turn(db_conn, app):
    alice = await member(db_conn, "alice")
    claim = await store.begin_turn(session_id="s-1", user_id=alice.user_id, model_id="ollama:m", text="hi",
                                   attachments=[], new_session_pins=None)
    async with _client(app, alice) as c:
        live = (await c.get("/v1/chat/sessions/s-1")).json()["messages"]
    assert [m["status"] for m in live] == ["complete", "streaming"]
    assert live[1]["model"] == "ollama:m"
    await db_conn.execute("UPDATE chat_sessions SET active_turn_heartbeat_at = now() - interval '5 minutes'")
    async with _client(app, alice) as c:
        after = (await c.get("/v1/chat/sessions/s-1")).json()["messages"]
    assert (after[1]["status"], after[1]["finishReason"]) == ("aborted", "aborted")
    assert after[1]["id"] == claim.turn_id


async def test_import_copies_a_legacy_chat_under_a_new_id(db_conn, app):
    alice = await member(db_conn, "alice")
    legacy = {"title": "Old chat", "model": "qwen2.5", "createdAt": 1700000000000,
              "messages": [{"id": "u-1", "role": "user", "content": "q", "timestamp": 1,
                            "attachments": [{"id": "a", "kind": "image", "name": "x.png", "mimeType": "image/png",
                                             "size": 3, "base64": "AAA", "dataUrl": "data:image/png;base64,AAA"}]},
                           {"id": "a-2", "role": "assistant", "content": "ans", "timestamp": 2}],
              "events": [{"ts": 1, "kind": "sent", "message": "prompt: q"}],
              "pins": [{"id": "p1", "text": "x"}]}
    async with _client(app, alice) as c:
        r = await c.post("/v1/chat/sessions/import", json=legacy)
        assert r.status_code == 200
        new_id = r.json()["id"]
        assert new_id.startswith("s-")
        got = (await c.get(f"/v1/chat/sessions/{new_id}")).json()
    assert (got["title"], got["model"], got["pins"]) == ("Old chat", "qwen2.5", [{"id": "p1", "text": "x"}])
    assert [(m["role"], m["content"], m["status"]) for m in got["messages"]] == [
        ("user", "q", "complete"), ("assistant", "ans", "complete")]
    assert got["messages"][0]["id"] != "u-1"                              # ids are global: always new
    assert got["messages"][0]["attachments"] == [
        {"id": "a", "kind": "image", "name": "x.png", "mimeType": "image/png", "size": 3}]   # bytes never stored
    assert [e["kind"] for e in got["events"]] == ["sent"]
    cur = await db_conn.execute("SELECT user_id FROM chat_sessions WHERE id = %s", (new_id,))
    assert str((await cur.fetchone())[0]) == alice.user_id
