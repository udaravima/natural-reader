"""The phone store (spec 4.3, 5.1, 5.5): a line, its reader service user, clips and calls."""
import logging
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from server.auth import deps
from server.auth.authz import project_role
from server.auth.users import get_user
from server.phone import store
from server.phone.store import Clip, LineVoice
from server.tests import seed
from server.tests.test_project_members import _p, _user

pytestmark = pytest.mark.asyncio

VOICE = LineVoice("kokoro", "af_heart", None)
GREETING = "Hello, this is an automated assistant."


@pytest.fixture
async def world(db_conn):
    """An owner of a project, a plain reader of it, and an admin who is not a member."""
    owner, reader = await _user(db_conn, "owner"), await _user(db_conn, "reader")
    admin = await _user(db_conn, "admin")
    project = await seed.make_project(db_conn, owner, members=[(reader, "reader")])
    other = await seed.make_project(db_conn, owner, "Other")
    return {"owner": _p(owner), "reader": _p(reader), "admin": _p(admin, admin=True), "project": project,
            "other": other}


async def _line(db_conn, w, **kw):
    args = dict(name="Front desk", profile_id=None, project_id=w["project"], model_id="m1", voice=VOICE,
                greeting=GREETING)
    return await store.create_line(db_conn, w["owner"], **{**args, **kw})


def _clip(kind="greeting", ordinal=0, key="kokoro:af_heart:", at=None, pcm=b"\x01\x02" * 4):
    return Clip(kind, ordinal, "text", key, pcm, at or datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc))


async def _count(db_conn, sql, *params):
    cur = await db_conn.execute(sql, params)
    return (await cur.fetchone())[0]


async def test_create_line_makes_a_reader_service_user(db_conn, world):
    line = await _line(db_conn, world, daily_tokens=5000)
    assert await project_role(db_conn, line.service_user_id, world["project"]) == "reader"
    user = await get_user(db_conn, line.service_user_id)
    assert user["display_name"] == "Phone line: Front desk"
    assert user["email"] == f"line-{line.id}@phone-line.invalid"
    assert (user["status"], user["capabilities"], user["inference_daily_token_budget"]) == (
        "active", ["chat"], 5000)
    assert await _count(db_conn, "SELECT kind FROM users WHERE id = %s", line.service_user_id) == "line"
    assert line.daily_tokens == 5000 and line.enabled is False
    assert (line.max_call_s, line.max_calls, line.language, line.voice) == (600, 2, "en", VOICE)
    assert await store.get_line(db_conn, line.id) == line
    assert await store.list_lines(db_conn) == [line]
    assert await store.get_line(db_conn, "not-a-uuid") is None


async def _nothing_left_behind(db_conn):
    assert await store.list_lines(db_conn) == []
    assert await _count(db_conn, "SELECT count(*) FROM users WHERE kind = 'line'") == 0


async def test_only_a_maintainer_can_attach_a_project(db_conn, world):
    # a plain reader: 403; an admin who is not a member: 404 (chat keeps such an admin out of the documents,
    # so a line must not hand them to callers); neither leaves a line, a user or a membership behind
    for who, status in (("reader", 403), ("admin", 404)):
        with pytest.raises(HTTPException) as e:
            await store.create_line(db_conn, world[who], name="X", profile_id=None, project_id=world["project"],
                                    model_id="m1", voice=VOICE, greeting=GREETING)
        assert e.value.status_code == status
        await _nothing_left_behind(db_conn)


async def test_a_non_member_admin_cannot_move_a_line_to_a_project(db_conn, world):
    line = await _line(db_conn, world)
    with pytest.raises(HTTPException) as e:
        await store.update_line(db_conn, world["admin"], line.id, project_id=world["other"])
    assert e.value.status_code == 404
    assert await project_role(db_conn, line.service_user_id, world["project"]) == "reader"
    assert await project_role(db_conn, line.service_user_id, world["other"]) is None
    assert (await store.get_line(db_conn, line.id)).project_id == world["project"]


async def test_an_admin_who_is_a_maintainer_can_attach_and_move(db_conn, world):
    admin_id = world["admin"].user_id
    await seed.add_member(db_conn, world["project"], admin_id, "maintainer")
    await seed.add_member(db_conn, world["other"], admin_id, "maintainer")
    line = await _line(db_conn, world | {"owner": world["admin"]})
    assert await project_role(db_conn, line.service_user_id, world["project"]) == "reader"
    await store.update_line(db_conn, world["admin"], line.id, project_id=world["other"])
    assert await project_role(db_conn, line.service_user_id, world["other"]) == "reader"


async def test_a_line_without_a_project_is_made_and_cannot_be_switched_on(db_conn, world):
    line = await _line(db_conn, world, project_id=None)
    assert line.project_id is None
    with pytest.raises(ValueError):
        await store.update_line(db_conn, world["owner"], line.id, enabled=True)
    assert (await store.get_line(db_conn, line.id)).enabled is False


async def test_update_changes_fields_budget_and_renames_the_user(db_conn, world):
    line = await _line(db_conn, world)
    new = await store.update_line(db_conn, world["owner"], line.id, name="Night desk", greeting="Hi",
                                  voice=LineVoice("chirp", "Aoede", "en-GB"), max_calls=3, daily_tokens=None,
                                  enabled=True)
    assert (new.name, new.greeting, new.max_calls, new.enabled, new.daily_tokens) == (
        "Night desk", "Hi", 3, True, None)
    assert new.voice == LineVoice("chirp", "Aoede", "en-GB")
    assert (await get_user(db_conn, line.service_user_id))["display_name"] == "Phone line: Night desk"
    with pytest.raises(TypeError):
        await store.update_line(db_conn, world["owner"], line.id, language="si")


async def test_changing_the_project_moves_the_membership(db_conn, world):
    line = await _line(db_conn, world)
    await store.update_line(db_conn, world["owner"], line.id, project_id=world["other"])
    assert await project_role(db_conn, line.service_user_id, world["project"]) is None
    assert await project_role(db_conn, line.service_user_id, world["other"]) == "reader"
    assert (await store.get_line(db_conn, line.id)).project_id == world["other"]


async def test_a_non_maintainer_cannot_move_a_line_to_a_project_and_nothing_changes(db_conn, world):
    line = await _line(db_conn, world)
    with pytest.raises(HTTPException):
        await store.update_line(db_conn, world["reader"], line.id, project_id=world["other"])
    assert await project_role(db_conn, line.service_user_id, world["project"]) == "reader"
    assert (await store.get_line(db_conn, line.id)).project_id == world["project"]


async def test_clearing_the_project_removes_the_membership_and_switches_off(db_conn, world):
    line = await _line(db_conn, world)
    await store.update_line(db_conn, world["owner"], line.id, enabled=True)
    # a reader (no maintainer rights) may still shrink who can hear a project
    new = await store.update_line(db_conn, world["reader"], line.id, project_id=None)
    assert (new.project_id, new.enabled) == (None, False)
    assert await project_role(db_conn, line.service_user_id, world["project"]) is None


async def test_delete_line_removes_user_chats_and_membership(db_conn, world):
    line = await _line(db_conn, world)
    for i in range(2):
        await store.open_call(db_conn, line, session_id=f"s{i}", now_ms=1_700_000_000_000 + i)
    await store.save_clips(db_conn, line.id, [_clip()])
    assert await store.delete_line(db_conn, world["owner"], line.id) == 2
    assert await store.get_line(db_conn, line.id) is None
    assert await get_user(db_conn, line.service_user_id) is None
    assert await _count(db_conn, "SELECT count(*) FROM chat_sessions WHERE id IN ('s0','s1')") == 0
    assert await _count(db_conn, "SELECT count(*) FROM phone_calls") == 0
    assert await _count(db_conn, "SELECT count(*) FROM phone_line_clips") == 0
    assert await project_role(db_conn, line.service_user_id, world["project"]) is None


async def test_clips_replace_the_whole_set_and_move_the_stamp(db_conn, world):
    line = await _line(db_conn, world)
    assert await store.clips_stamp(db_conn, line.id) is None
    assert await store.load_clips(db_conn, line.id) == []
    t1, t2 = datetime(2026, 11, 20, 10, tzinfo=timezone.utc), datetime(2026, 11, 20, 11, tzinfo=timezone.utc)
    await store.save_clips(db_conn, line.id, [_clip("filler", 1, at=t1), _clip("filler", 0, at=t1),
                                              _clip("busy", at=t1)])
    assert [(c.kind, c.ordinal) for c in await store.load_clips(db_conn, line.id)] == [
        ("busy", 0), ("filler", 0), ("filler", 1)]
    assert await store.clips_stamp(db_conn, line.id) == t1
    await store.save_clips(db_conn, line.id, [_clip("greeting", at=t2)])
    loaded = await store.load_clips(db_conn, line.id)
    assert loaded == [_clip("greeting", at=t2)] and isinstance(loaded[0].pcm, bytes)
    assert await store.clips_stamp(db_conn, line.id) == t2


async def test_open_call_writes_the_greeting_first(db_conn, world):
    line = await _line(db_conn, world)
    call_id = await store.open_call(db_conn, line, session_id="call-1", now_ms=1_700_000_000_000)
    call = await store.get_call(db_conn, call_id)
    assert call["session_id"] == "call-1" and call["ended_at"] is None and call["turns"] == 0
    [first] = call["messages"]
    assert (first["role"], first["content"], first["status"]) == ("assistant", GREETING, "complete")
    assert first["id"].startswith("a-")
    cur = await db_conn.execute(
        "SELECT user_id, title, model, scope_project_id, active_turn_id FROM chat_sessions WHERE id = 'call-1'")
    owner, title, model, scope, active = await cur.fetchone()
    assert str(owner) == line.service_user_id and model == "m1" and str(scope) == world["project"]
    assert active is None
    assert title.startswith("Call 2023-11-")


async def test_call_title_uses_the_deployment_zone(db_conn, world, monkeypatch):
    from zoneinfo import ZoneInfo

    async def _zone(conn, env):
        return ZoneInfo("Asia/Colombo")
    monkeypatch.setattr(store, "deployment_zone", _zone)
    line = await _line(db_conn, world)
    # 2023-11-14 22:13:20 UTC is 03:43 the next day in Colombo (UTC+5:30)
    await store.open_call(db_conn, line, session_id="c", now_ms=1_700_000_000_000)
    assert await _count(db_conn, "SELECT title FROM chat_sessions WHERE id = 'c'") == "Call 2023-11-15 03:43"


async def test_close_call_records_the_end_and_keeps_the_first_reason(db_conn, world):
    line = await _line(db_conn, world)
    call_id = await store.open_call(db_conn, line, session_id="c", now_ms=1)
    stats = [{"turn": 1, "first_sound_ms": 900}]
    await store.close_call(db_conn, call_id, end_reason="caller", turns=1, stats=stats)
    await store.close_call(db_conn, call_id, end_reason="error", turns=9, stats=[])
    call = await store.get_call(db_conn, call_id)
    assert (call["end_reason"], call["turns"], call["stats"]) == ("caller", 1, stats)
    assert call["ended_at"] is not None


async def test_close_stale_calls_ends_only_old_open_calls(db_conn, world):
    line = await _line(db_conn, world)
    old = await store.open_call(db_conn, line, session_id="old", now_ms=1)
    new = await store.open_call(db_conn, line, session_id="new", now_ms=2)
    done = await store.open_call(db_conn, line, session_id="done", now_ms=3)
    hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    await db_conn.execute("UPDATE phone_calls SET started_at = %s WHERE id IN (%s, %s)", (hour_ago, old, done))
    await store.close_call(db_conn, done, end_reason="caller", turns=0, stats=[])
    assert await store.close_stale_calls(db_conn, 600) == 1
    assert (await store.get_call(db_conn, old))["end_reason"] == "shutdown"
    assert (await store.get_call(db_conn, new))["ended_at"] is None
    assert (await store.get_call(db_conn, done))["end_reason"] == "caller"
    assert await store.close_stale_calls(db_conn, 600) == 0


async def test_a_refused_call_has_no_chat_and_ended_at_start(db_conn, world):
    line = await _line(db_conn, world)
    call_id = await store.record_refused_call(db_conn, line.id, "busy")
    call = await store.get_call(db_conn, call_id)
    assert (call["session_id"], call["end_reason"], call["turns"], call["messages"]) == (None, "busy", 0, [])
    assert call["ended_at"] == call["started_at"]
    await store.delete_call(db_conn, call_id)
    assert await store.get_call(db_conn, call_id) is None


async def test_list_calls_newest_first_and_limited(db_conn, world):
    line = await _line(db_conn, world)
    ids = [await store.open_call(db_conn, line, session_id=f"s{i}", now_ms=i) for i in range(3)]
    for i, cid in enumerate(ids):
        await db_conn.execute("UPDATE phone_calls SET started_at = %s WHERE id = %s",
                              (datetime(2026, 11, 20, 10, i, tzinfo=timezone.utc), cid))
    assert [c["id"] for c in await store.list_calls(db_conn, line.id)] == ids[::-1]
    assert [c["id"] for c in await store.list_calls(db_conn, line.id, limit=2)] == ids[:0:-1]
    assert await store.get_call(db_conn, "nope") is None


async def test_delete_call_removes_its_chat_and_messages(db_conn, world):
    line = await _line(db_conn, world)
    call_id = await store.open_call(db_conn, line, session_id="gone", now_ms=1)
    await store.delete_call(db_conn, call_id)
    assert await store.get_call(db_conn, call_id) is None
    assert await _count(db_conn, "SELECT count(*) FROM chat_sessions WHERE id = 'gone'") == 0
    assert await _count(db_conn, "SELECT count(*) FROM chat_messages WHERE session_id = 'gone'") == 0


async def test_logs_hold_ids_not_the_greeting(db_conn, world, caplog):
    caplog.set_level(logging.INFO, logger="server.phone.store")
    line = await _line(db_conn, world)
    await store.open_call(db_conn, line, session_id="c", now_ms=1)
    assert caplog.records and GREETING not in caplog.text


async def test_open_line_models_are_the_models_a_call_could_reach(db_conn, world):
    # R17.1: the models the refresher keeps loaded. A line counts when it is on and its user is still a member of its
    # project (the gate's own first checks, so a line the gate closes holds no memory); each model once, in order.
    async def on(model, project=world["project"]):
        line = await _line(db_conn, world, model_id=model, project_id=project)
        return await store.update_line(db_conn, world["owner"], line.id, enabled=True)
    await on("ollama:qwen3:8b")
    await on("ollama:gemma4:e4b")
    await on("ollama:gemma4:e4b")
    await _line(db_conn, world, model_id="ollama:never-on")
    revoked = await on("ollama:revoked")
    await db_conn.execute("DELETE FROM project_members WHERE user_id = %s", (revoked.service_user_id,))
    await on("ollama:project-gone", project=world["other"])
    await db_conn.execute("DELETE FROM projects WHERE id = %s", (world["other"],))
    assert await store.open_line_models(db_conn) == ["ollama:gemma4:e4b", "ollama:qwen3:8b"]
