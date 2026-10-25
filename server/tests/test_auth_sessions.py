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


# ---- idle timeout (SESSION_IDLE_HOURS) ----
# A session used to live its full 7 days whether or not anyone used it, so a
# browser left for a day was still signed in. last_seen_at (stamped at most
# once a minute) now ends a session left unused for longer than the idle limit.

async def _aged(db_conn, hours_idle):
    u = await _user(db_conn)
    tok = await create_session(db_conn, u["id"])
    await db_conn.execute("UPDATE sessions SET last_seen_at = now() - make_interval(hours => %s)",
                          (hours_idle,))
    return u, tok


async def test_a_session_unused_past_the_idle_limit_ends(db_conn):
    _, tok = await _aged(db_conn, 13)
    assert await resolve_session(db_conn, tok, idle_hours=12) is None


async def test_a_session_used_within_the_idle_limit_continues(db_conn):
    u, tok = await _aged(db_conn, 11)
    assert (await resolve_session(db_conn, tok, idle_hours=12))["id"] == u["id"]


async def test_idle_limit_zero_means_no_idle_timeout(db_conn):
    u, tok = await _aged(db_conn, 24 * 6)
    assert (await resolve_session(db_conn, tok, idle_hours=0))["id"] == u["id"]


async def test_the_absolute_lifetime_still_applies_with_an_idle_limit(db_conn):
    u = await _user(db_conn)
    tok = await create_session(db_conn, u["id"], ttl_hours=-1)
    assert await resolve_session(db_conn, tok, idle_hours=12) is None


def test_idle_hours_config_defaults_to_12_and_takes_0_for_off():
    from server.auth.config import load_auth_config
    assert load_auth_config({}).session_idle_hours == 12
    assert load_auth_config({"SESSION_IDLE_HOURS": "0"}).session_idle_hours == 0
    assert load_auth_config({"SESSION_IDLE_HOURS": "48"}).session_idle_hours == 48
