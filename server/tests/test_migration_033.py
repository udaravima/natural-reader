"""Migration 033 (phone lines, spec 5.5): users.kind and the three phone tables."""
import pytest
from psycopg.errors import CheckViolation, ForeignKeyViolation

pytestmark = pytest.mark.asyncio


async def _one(conn, sql, params=()):
    cur = await conn.execute(sql, params)
    return await cur.fetchone()


async def _line_user(conn, tag="a"):
    return (await _one(conn, "INSERT INTO users (email, role, status, kind) "
                             "VALUES (%s, 'member', 'active', 'line') RETURNING id",
                       (f"line-{tag}@phone-line.invalid",)))[0]


async def _line(conn, user_id, **over):
    cols = {"name": "Front desk", "model_id": "m", "voice": '{"engine": "kokoro", "voice": "af"}',
            "greeting": "Hello", "service_user_id": user_id, **over}
    names = ", ".join(cols)
    marks = ", ".join(["%s"] * len(cols))
    return (await _one(conn, f"INSERT INTO phone_lines ({names}) VALUES ({marks}) RETURNING id",
                       list(cols.values())))[0]


async def test_users_kind_defaults_to_person(db_conn):
    kind = (await _one(db_conn, "INSERT INTO users (email, role, status) "
                                "VALUES ('p@x.io', 'member', 'active') RETURNING kind"))[0]
    assert kind == "person"


async def test_kind_rejects_other_values(db_conn):
    with pytest.raises(CheckViolation):
        await db_conn.execute("INSERT INTO users (email, role, status, kind) "
                              "VALUES ('r@x.io', 'member', 'active', 'robot')")


async def test_phone_lines_language_is_en_only(db_conn):
    uid = await _line_user(db_conn)
    with pytest.raises(CheckViolation):
        await _line(db_conn, uid, language="si")


async def test_clip_kinds_are_the_eight(db_conn):
    uid = await _line_user(db_conn)
    line = await _line(db_conn, uid)
    for kind in ("greeting", "busy", "closed", "goodbye", "still", "sorry", "filler", "checking"):
        await db_conn.execute(
            "INSERT INTO phone_line_clips (line_id, kind, text, voice_key, pcm) "
            "VALUES (%s, %s, 't', 'k', %s)", (line, kind, b"\x00\x00"))
    with pytest.raises(CheckViolation):
        await db_conn.execute(
            "INSERT INTO phone_line_clips (line_id, kind, text, voice_key, pcm) "
            "VALUES (%s, 'music', 't', 'k', %s)", (line, b"\x00\x00"))


async def test_service_user_delete_is_restricted(db_conn):
    uid = await _line_user(db_conn)
    await _line(db_conn, uid)
    with pytest.raises(ForeignKeyViolation):
        await db_conn.execute("DELETE FROM users WHERE id = %s", (uid,))
