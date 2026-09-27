import base64

import pytest

from server.chat import store
from server.llm.types import Attachment
from server.tests.chat_harness import member, shim_pool

IMG_A = store.NewAttachment(kind="image", mime="image/png", name="a.png", data=b"A", client_id="att-a")
IMG_B = store.NewAttachment(kind="image", mime="image/png", name="b.png", data=b"B", client_id="att-b")


@pytest.fixture
def conn(db_conn, monkeypatch):
    shim_pool(monkeypatch, db_conn, store)
    return db_conn


async def _one(conn, sql, params=()):
    cur = await conn.execute(sql, params)
    return await cur.fetchone()


async def _begin(user, sid="s-1", text="Hello there, world", atts=(), pins=None):
    return await store.begin_turn(session_id=sid, user_id=user.user_id, model_id="ollama:m",
                                  text=text, attachments=list(atts), new_session_pins=pins)


async def _finish(claim, content="", status="complete", reason="stop"):
    await store.finish_turn(claim, status=status, finish_reason=reason, content=content,
                            thinking="", tool_calls=[], doc_context=None, stats={})


def test_title_from_prompt():
    assert store.title_from_prompt("  a \n b ") == "a b"
    assert store.title_from_prompt("") == "New chat"
    assert store.title_from_prompt("x" * 80) == "x" * 59 + "…"


async def test_begin_turn_creates_session_messages_bytes_and_claim(conn):
    alice = await member(conn, "alice")
    claim = await _begin(alice, atts=[IMG_A], pins=[{"id": "pin-1", "text": "x"}])
    assert claim.created_session and claim.turn_id == claim.assistant_message_id
    assert await _one(conn, "SELECT title, model, pins, active_turn_id FROM chat_sessions WHERE id='s-1'") == (
        "Hello there, world", "ollama:m", [{"id": "pin-1", "text": "x"}], claim.turn_id)
    assert (await _one(conn, "SELECT attachments FROM chat_messages WHERE id=%s", (claim.user_message_id,)))[0] == [
        {"id": "att-a", "kind": "image", "name": "a.png", "mimeType": "image/png", "size": 1, "ordinal": 0}]
    assert (await _one(conn, "SELECT data FROM chat_attachments WHERE message_id=%s",
                       (claim.user_message_id,)))[0] == b"A"
    assert (await _one(conn, "SELECT status, role FROM chat_messages WHERE id=%s",
                       (claim.assistant_message_id,))) == ("streaming", "assistant")


async def test_existing_session_keeps_its_pins_and_title(conn):
    alice = await member(conn, "alice")
    await _finish(await _begin(alice, pins=[{"id": "p1"}]))
    await _begin(alice, text="another question", pins=[{"id": "p2"}])
    assert await _one(conn, "SELECT title, pins FROM chat_sessions WHERE id='s-1'") == (
        "Hello there, world", [{"id": "p1"}])


async def test_a_second_turn_is_refused_while_one_is_active(conn):
    alice = await member(conn, "alice")
    first = await _begin(alice)
    with pytest.raises(store.TurnInProgress):
        await _begin(alice, text="again")
    assert (await _one(conn, "SELECT active_turn_id FROM chat_sessions WHERE id='s-1'"))[0] == first.turn_id
    assert (await _one(conn, "SELECT count(*) FROM chat_messages WHERE session_id='s-1'"))[0] == 2


async def test_someone_elses_session_is_not_owner(conn):
    alice, bob = await member(conn, "alice"), await member(conn, "bob")
    await _begin(alice)
    with pytest.raises(store.NotOwner):
        await _begin(bob)
    assert (await _one(conn, "SELECT count(*) FROM chat_messages WHERE session_id='s-1'"))[0] == 2


async def test_a_stale_claim_is_taken_over_and_its_message_aborted(conn):
    alice = await member(conn, "alice")
    old = await _begin(alice)
    await conn.execute("UPDATE chat_sessions SET active_turn_heartbeat_at = now() - interval '5 minutes'")
    new = await _begin(alice, text="again")
    assert await _one(conn, "SELECT status, finish_reason FROM chat_messages WHERE id=%s",
                      (old.turn_id,)) == ("aborted", "aborted")
    assert (await _one(conn, "SELECT active_turn_id FROM chat_sessions WHERE id='s-1'"))[0] == new.turn_id


async def test_startup_recovery_only_touches_stale_claims(conn):
    alice = await member(conn, "alice")
    fresh, stale = await _begin(alice, sid="s-fresh"), await _begin(alice, sid="s-stale")
    await conn.execute("UPDATE chat_sessions SET active_turn_heartbeat_at = now() - interval '2 minutes' "
                       "WHERE id='s-stale'")
    assert await store.recover_stale() == 1
    assert (await _one(conn, "SELECT status FROM chat_messages WHERE id=%s", (fresh.turn_id,)))[0] == "streaming"
    assert (await _one(conn, "SELECT active_turn_id FROM chat_sessions WHERE id='s-fresh'"))[0] == fresh.turn_id
    assert (await _one(conn, "SELECT status FROM chat_messages WHERE id=%s", (stale.turn_id,)))[0] == "aborted"
    assert (await _one(conn, "SELECT active_turn_id FROM chat_sessions WHERE id='s-stale'"))[0] is None


async def test_heartbeat_refreshes_only_its_own_claim(conn):
    alice = await member(conn, "alice")
    claim = await _begin(alice)
    assert await store.heartbeat("s-1", claim.turn_id) is True
    assert await store.heartbeat("s-1", "a-someone-else") is False


async def test_finish_turn_saves_and_releases_its_claim(conn):
    alice = await member(conn, "alice")
    claim = await _begin(alice)
    await store.finish_turn(claim, status="complete", finish_reason="stop", content="Hi", thinking="",
                            tool_calls=[{"name": "x"}], doc_context={"notes": []}, stats={"model": "ollama:m"})
    assert await _one(conn, "SELECT status, finish_reason, content, thinking, tool_calls, doc_context, stats "
                            "FROM chat_messages WHERE id=%s", (claim.turn_id,)) == (
        "complete", "stop", "Hi", None, [{"name": "x"}], {"notes": []}, {"model": "ollama:m"})
    assert (await _one(conn, "SELECT active_turn_id FROM chat_sessions WHERE id='s-1'"))[0] is None


async def test_finishing_a_taken_over_turn_leaves_the_new_claim_alone(conn):
    alice = await member(conn, "alice")
    old = await _begin(alice)
    await conn.execute("UPDATE chat_sessions SET active_turn_heartbeat_at = now() - interval '5 minutes'")
    new = await _begin(alice, text="again")
    await _finish(old, content="late")
    assert (await _one(conn, "SELECT active_turn_id FROM chat_sessions WHERE id='s-1'"))[0] == new.turn_id


async def test_save_progress_writes_partial_content(conn):
    alice = await member(conn, "alice")
    claim = await _begin(alice)
    await store.save_progress(claim.turn_id, content="par", thinking="t", tool_calls=None,
                              doc_context={"notes": [{"kind": "trimmed"}]})
    assert await _one(conn, "SELECT content, thinking, doc_context, status FROM chat_messages WHERE id=%s",
                      (claim.turn_id,)) == ("par", "t", {"notes": [{"kind": "trimmed"}]}, "streaming")


async def test_history_excludes_current_turn_streaming_and_empty_replies(conn):
    alice = await member(conn, "alice")
    await conn.execute("INSERT INTO chat_sessions (id, user_id) VALUES ('s-1', %s)", (alice.user_id,))
    await conn.execute("INSERT INTO chat_messages (id, session_id, role, content, attachments, timestamp) "
                       "VALUES ('u-legacy', 's-1', 'user', 'legacy', '[{\"id\":\"x\",\"kind\":\"image\"}]', 1)")
    t1 = await _begin(alice, text="first", atts=[IMG_A])
    await _finish(t1, content="answer one")
    t2 = await _begin(alice, text="second")
    await _finish(t2, content="", status="error", reason="error")
    t3 = await _begin(alice, text="third")
    pins, history = await store.load_turn_context("s-1", exclude=(t3.user_message_id, t3.assistant_message_id))
    assert pins == []
    assert [(m.role, m.content) for m in history] == [
        ("user", "legacy"), ("user", "first"), ("assistant", "answer one"), ("user", "second")]
    assert history[0].attachments == []                  # pre-C1 metadata: bytes were never stored
    assert history[1].attachments[0]["ordinal"] == 0


async def test_attachment_bytes_load_only_what_is_asked(conn):
    alice = await member(conn, "alice")
    claim = await _begin(alice, atts=[IMG_A, IMG_B])
    got = await store.load_attachment_bytes([(claim.user_message_id, 1)])
    assert got == {(claim.user_message_id, 1): Attachment("image", "image/png",
                                                          base64.b64encode(b"B").decode(), "b.png")}
    assert await store.load_attachment_bytes([]) == {}


async def test_add_event_and_session_info(conn):
    alice = await member(conn, "alice")
    await _begin(alice, pins=[{"id": "p"}])
    await store.add_event("s-1", "sent", "prompt: hi")
    assert await _one(conn, "SELECT kind, message FROM chat_events WHERE session_id='s-1'") == ("sent", "prompt: hi")
    info = await store.session_info("s-1")
    assert (info.owner_id, info.pins) == (alice.user_id, [{"id": "p"}])
    assert await store.session_info("s-none") is None


async def test_writes_after_session_delete_are_noops(conn):
    alice = await member(conn, "alice")
    claim = await _begin(alice, atts=[IMG_A])
    await conn.execute("DELETE FROM chat_sessions WHERE id='s-1'")
    assert (await _one(conn, "SELECT count(*) FROM chat_attachments"))[0] == 0
    await store.save_progress(claim.turn_id, content="x", thinking="", tool_calls=None, doc_context=None)
    assert await store.heartbeat("s-1", claim.turn_id) is False
    await store.add_event("s-1", "sent", "x")
    await _finish(claim, content="x")
    assert (await _one(conn, "SELECT count(*) FROM chat_messages WHERE session_id='s-1'"))[0] == 0
    assert (await _one(conn, "SELECT count(*) FROM chat_events WHERE session_id='s-1'"))[0] == 0
