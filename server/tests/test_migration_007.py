SEED = "00000000-0000-0000-0000-000000000001"


async def _cols(db_conn, table):
    cur = await db_conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name=%s",
        (table,),
    )
    return {r[0] for r in await cur.fetchall()}


async def test_inference_usage_shape(db_conn):
    cols = await _cols(db_conn, "inference_usage")
    assert {"user_id", "day", "prompt_tokens", "eval_tokens", "requests"} <= cols


async def test_users_budget_column_exists(db_conn):
    assert "inference_daily_token_budget" in await _cols(db_conn, "users")


async def test_usage_round_trip(db_conn):
    await db_conn.execute(
        "INSERT INTO inference_usage (user_id, day, prompt_tokens, eval_tokens, requests) "
        "VALUES (%s, CURRENT_DATE, 10, 5, 1)",
        (SEED,),
    )
    cur = await db_conn.execute(
        "SELECT prompt_tokens, eval_tokens, requests FROM inference_usage "
        "WHERE user_id=%s AND day=CURRENT_DATE",
        (SEED,),
    )
    assert await cur.fetchone() == (10, 5, 1)
