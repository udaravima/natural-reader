from server.auth.tokens import create_token, resolve_token, list_tokens, revoke_token
from server.auth.users import resolve_or_provision_user


async def _user(db_conn, sub="s"):
    return await resolve_or_provision_user(
        db_conn, iss="i", sub=sub, email=f"{sub}@x.io"
    )


async def test_create_returns_raw_once_and_resolves(db_conn):
    u = await _user(db_conn)
    tid, raw = await create_token(db_conn, u["id"], "cli")
    assert raw.startswith("nrp_")
    assert (await resolve_token(db_conn, raw))["id"] == u["id"]


async def test_raw_not_stored(db_conn):
    u = await _user(db_conn)
    _, raw = await create_token(db_conn, u["id"], "cli")
    cur = await db_conn.execute("SELECT token_hash FROM personal_access_tokens")
    assert (await cur.fetchone())[0] != raw


async def test_list_hides_value_and_revoke(db_conn):
    u = await _user(db_conn)
    tid, raw = await create_token(db_conn, u["id"], "cli")
    listed = await list_tokens(db_conn, u["id"])
    assert listed[0]["name"] == "cli" and "token_hash" not in listed[0]
    assert await revoke_token(db_conn, u["id"], tid) is True
    assert await resolve_token(db_conn, raw) is None


async def test_cannot_revoke_others_token(db_conn):
    a = await _user(db_conn, "a")
    b = await _user(db_conn, "b")
    tid, _ = await create_token(db_conn, a["id"], "cli")
    assert await revoke_token(db_conn, b["id"], tid) is False


async def test_non_pat_string_resolves_none(db_conn):
    assert await resolve_token(db_conn, "not-a-pat") is None
