from server.auth.sessions import create_session, resolve_session, revoke_session
from server.auth.users import resolve_or_provision_user


async def _user(db_conn):
    return await resolve_or_provision_user(db_conn, iss="i", sub="s", email="a@x.io")


async def test_create_then_resolve(db_conn):
    u = await _user(db_conn)
    tok = await create_session(db_conn, u["id"])
    assert tok and len(tok) > 20
    resolved = await resolve_session(db_conn, tok)
    assert resolved["id"] == u["id"]


async def test_raw_token_not_stored(db_conn):
    u = await _user(db_conn)
    tok = await create_session(db_conn, u["id"])
    cur = await db_conn.execute("SELECT id FROM sessions")
    assert (await cur.fetchone())[0] != tok  # stored value is a hash


async def test_expired_session_resolves_none(db_conn):
    u = await _user(db_conn)
    tok = await create_session(db_conn, u["id"], ttl_hours=-1)
    assert await resolve_session(db_conn, tok) is None


async def test_revoke(db_conn):
    u = await _user(db_conn)
    tok = await create_session(db_conn, u["id"])
    await revoke_session(db_conn, tok)
    assert await resolve_session(db_conn, tok) is None


async def test_unknown_token_resolves_none(db_conn):
    assert await resolve_session(db_conn, "nope") is None
