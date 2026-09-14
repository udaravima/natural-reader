async def _cols(db_conn, table):
    cur = await db_conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name=%s",
        (table,),
    )
    return {r[0] for r in await cur.fetchall()}


async def test_sessions_table_shape(db_conn):
    cols = await _cols(db_conn, "sessions")
    assert {"id", "user_id", "expires_at", "last_seen_at"} <= cols


async def test_pat_table_shape(db_conn):
    cols = await _cols(db_conn, "personal_access_tokens")
    assert {"id", "user_id", "name", "token_hash", "expires_at"} <= cols
