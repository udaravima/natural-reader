SEED = "00000000-0000-0000-0000-000000000001"


async def test_seed_admin_exists(db_conn):
    cur = await db_conn.execute(
        "SELECT role, status FROM users WHERE id = %s", (SEED,)
    )
    row = await cur.fetchone()
    assert row == ("admin", "active")


async def test_ownership_columns_not_null(db_conn):
    for table in ("documents", "chat_sessions"):
        cur = await db_conn.execute(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = %s AND column_name = 'user_id'",
            (table,),
        )
        assert (await cur.fetchone())[0] == "NO"


async def test_new_doc_carries_owner(db_conn):
    await db_conn.execute(
        "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, user_id) "
        "VALUES ('a', 'f', 'text', 1, %s)",
        (SEED,),
    )
    cur = await db_conn.execute("SELECT user_id FROM documents WHERE doc_id='a'")
    assert str((await cur.fetchone())[0]) == SEED
