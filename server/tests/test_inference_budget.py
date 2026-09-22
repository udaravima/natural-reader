from datetime import datetime, timedelta, timezone

from server.auth.users import SEED_ADMIN_ID
from server.services import inference_budget as ib

U = SEED_ADMIN_ID


async def test_record_usage_increments(db_conn):
    await ib.record_usage(db_conn, U, 10, 5)
    await ib.record_usage(db_conn, U, 3, 2)
    assert await ib.spent_today(db_conn, U) == (13, 7)
    cur = await db_conn.execute(
        "SELECT requests FROM inference_usage WHERE user_id=%s", (U,))
    assert (await cur.fetchone())[0] == 2


async def test_spent_today_zero_when_no_rows(db_conn):
    assert await ib.spent_today(db_conn, U) == (0, 0)


async def test_budget_state_unlimited_when_no_budget(db_conn):
    state = await ib.budget_state(db_conn, U, None)
    assert state["remaining_tokens"] is None
    assert state["reset_at"].endswith("Z")


async def test_budget_state_remaining_math(db_conn):
    await ib.record_usage(db_conn, U, 60, 40)
    state = await ib.budget_state(db_conn, U, 200)
    assert state["remaining_tokens"] == 100


async def test_over_budget_boundary(db_conn):
    await ib.record_usage(db_conn, U, 100, 0)
    assert await ib.over_budget(db_conn, U, 200) is False
    # Exhaust it: spent(200) == budget(200) → 0 left → over.
    await ib.record_usage(db_conn, U, 100, 0)
    assert await ib.over_budget(db_conn, U, 200) is True


async def test_per_user_column_overrides_default(db_conn):
    await db_conn.execute(
        "UPDATE users SET inference_daily_token_budget=1000 WHERE id=%s", (U,))
    await ib.record_usage(db_conn, U, 300, 300)
    # Deployment says 500, per-user says 1000 → 400 left.
    state = await ib.budget_state(db_conn, U, 500)
    assert state["remaining_tokens"] == 400


async def test_zero_column_means_unlimited(db_conn):
    await db_conn.execute(
        "UPDATE users SET inference_daily_token_budget=0 WHERE id=%s", (U,))
    await ib.record_usage(db_conn, U, 10**9, 10**9)
    assert await ib.over_budget(db_conn, U, 100) is False


async def test_reset_at_is_next_utc_midnight(db_conn):
    state = await ib.budget_state(db_conn, U, 100)
    reset = datetime.fromisoformat(state["reset_at"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    assert now < reset <= now + timedelta(days=1)
    assert (reset.hour, reset.minute, reset.second) == (0, 0, 0)


async def test_admin_usage_joins_email(db_conn):
    await ib.record_usage(db_conn, U, 10, 5)
    rows = await ib.admin_usage(db_conn, days=1)
    assert len(rows) == 1
    assert rows[0]["tokens"] == 15
    assert rows[0]["email"]


async def test_admin_usage_respects_days_window(db_conn):
    # Today's row is inside a 1-day window; a 40-day-old row is not.
    await ib.record_usage(db_conn, U, 10, 5)
    await db_conn.execute(
        "INSERT INTO inference_usage (user_id, day, prompt_tokens, eval_tokens, requests) "
        "VALUES (%s, CURRENT_DATE - 40, 1, 1, 1)",
        (U,),
    )
    assert len(await ib.admin_usage(db_conn, days=1)) == 1
    assert len(await ib.admin_usage(db_conn, days=90)) == 2
