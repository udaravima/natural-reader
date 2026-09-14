async def test_db_conn_round_trips(db_conn):
    cur = await db_conn.execute("SELECT 1")
    assert (await cur.fetchone())[0] == 1
