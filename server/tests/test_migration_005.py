from server.tests import seed

SEED = "00000000-0000-0000-0000-000000000001"


async def test_seed_admin_exists(db_conn):
    cur = await db_conn.execute(
        "SELECT role, status FROM users WHERE id = %s", (SEED,)
    )
    row = await cur.fetchone()
    assert row == ("admin", "active")


async def test_ownership_columns_not_null(db_conn):
    # 005 made documents.user_id and chat_sessions.user_id NOT NULL; 011 moved
    # document holding to library_entries, whose user_id is NOT NULL too.
    for table in ("library_entries", "chat_sessions"):
        cur = await db_conn.execute(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = %s AND column_name = 'user_id'",
            (table,),
        )
        assert (await cur.fetchone())[0] == "NO"


async def test_new_doc_holder_is_recorded_on_its_entry(db_conn):
    # Since 011 content carries no owner; its holder is the entry's user.
    await seed.seed_doc(db_conn, "a", SEED, file_name="f", file_type="text")
    cur = await db_conn.execute("SELECT user_id FROM library_entries WHERE doc_id='a'")
    assert str((await cur.fetchone())[0]) == SEED
