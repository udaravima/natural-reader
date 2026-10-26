from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from server.auth.users import SEED_ADMIN_ID
from server.services import console_settings, deployment_settings
from server.services import inference_budget as ib

U = SEED_ADMIN_ID


@pytest.fixture(autouse=True)
def _no_tz_env(monkeypatch):
    monkeypatch.delenv("TZ", raising=False)   # no row and no TZ: the zone is UTC


async def _set_zone(conn, name):
    await console_settings.save(conn, deployment_settings.SECTION, {"timezone": name}, updated_by=U)


async def _rec(conn, user, p, e, *, model="ollama:test", kind="chat", estimated=False):
    await ib.record_usage(conn, user, p, e, model=model, kind=kind, estimated=estimated)


async def test_record_usage_increments(db_conn):
    await _rec(db_conn, U, 10, 5)
    await _rec(db_conn, U, 3, 2)
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
    await _rec(db_conn, U, 60, 40)
    state = await ib.budget_state(db_conn, U, 200)
    assert state["remaining_tokens"] == 100


async def test_over_budget_boundary(db_conn):
    await _rec(db_conn, U, 100, 0)
    assert await ib.over_budget(db_conn, U, 200) is False
    # Exhaust it: spent(200) == budget(200) → 0 left → over.
    await _rec(db_conn, U, 100, 0)
    assert await ib.over_budget(db_conn, U, 200) is True


async def test_per_user_column_overrides_default(db_conn):
    await db_conn.execute(
        "UPDATE users SET inference_daily_token_budget=1000 WHERE id=%s", (U,))
    await _rec(db_conn, U, 300, 300)
    # Deployment says 500, per-user says 1000 → 400 left.
    state = await ib.budget_state(db_conn, U, 500)
    assert state["remaining_tokens"] == 400


async def test_zero_column_means_unlimited(db_conn):
    await db_conn.execute(
        "UPDATE users SET inference_daily_token_budget=0 WHERE id=%s", (U,))
    await _rec(db_conn, U, 10**9, 10**9)
    assert await ib.over_budget(db_conn, U, 100) is False


async def test_reset_at_is_next_utc_midnight(db_conn):
    state = await ib.budget_state(db_conn, U, 100)
    reset = datetime.fromisoformat(state["reset_at"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    assert now < reset <= now + timedelta(days=1)
    assert (reset.hour, reset.minute, reset.second) == (0, 0, 0)


async def test_admin_usage_joins_email(db_conn):
    await _rec(db_conn, U, 10, 5)
    rows = await ib.admin_usage(db_conn, days=1)
    assert len(rows) == 1
    assert rows[0]["tokens"] == 15
    assert rows[0]["email"]


async def test_admin_usage_respects_days_window(db_conn):
    # Today's row is inside a 1-day window; a 40-day-old row is not.
    await _rec(db_conn, U, 10, 5)
    await db_conn.execute(
        "INSERT INTO inference_usage (user_id, day, model, kind, prompt_tokens, eval_tokens, requests) "
        "VALUES (%s, CURRENT_DATE - 40, '', '', 1, 1, 1)",
        (U,),
    )
    assert len(await ib.admin_usage(db_conn, days=1)) == 1
    assert len(await ib.admin_usage(db_conn, days=90)) == 2


async def test_usage_counts_toward_the_deployment_day(db_conn, monkeypatch):
    await _set_zone(db_conn, "Asia/Colombo")
    monkeypatch.setattr(ib, "_now", lambda: datetime(2026, 10, 25, 20, 0, tzinfo=timezone.utc))
    await _rec(db_conn, U, 10, 5)
    cur = await db_conn.execute("SELECT day FROM inference_usage WHERE user_id = %s", (U,))
    assert (await cur.fetchone())[0] == date(2026, 10, 26)     # 01:30 on the 26th in Colombo
    assert await ib.spent_today(db_conn, U) == (10, 5)


def test_reset_is_the_next_local_midnight():
    now = datetime(2026, 10, 25, 20, 0, tzinfo=timezone.utc)
    assert ib.next_reset_at(now, ZoneInfo("Asia/Colombo")) == datetime(2026, 10, 26, 18, 30, tzinfo=timezone.utc)


def test_reset_on_the_night_the_clocks_change():
    now = datetime(2026, 11, 1, 15, 0, tzinfo=timezone.utc)    # New York falls back at 06:00 UTC that day
    assert ib.next_reset_at(now, ZoneInfo("America/New_York")) == datetime(2026, 11, 2, 5, 0, tzinfo=timezone.utc)


async def test_budget_state_reports_the_local_reset(db_conn, monkeypatch):
    await _set_zone(db_conn, "Asia/Colombo")
    monkeypatch.setattr(ib, "_now", lambda: datetime(2026, 10, 25, 20, 0, tzinfo=timezone.utc))
    assert (await ib.budget_state(db_conn, U, None))["reset_at"] == "2026-10-26T18:30:00Z"


async def test_admin_usage_window_starts_on_the_deployment_day(db_conn, monkeypatch):
    await _set_zone(db_conn, "Asia/Colombo")
    monkeypatch.setattr(ib, "_now", lambda: datetime(2026, 10, 25, 20, 0, tzinfo=timezone.utc))
    for day in (date(2026, 10, 26), date(2026, 10, 25)):
        await db_conn.execute("INSERT INTO inference_usage (user_id, day, model, kind, prompt_tokens, eval_tokens, "
                              "requests) VALUES (%s, %s, '', '', 1, 1, 1)", (U, day))
    assert [r["day"] for r in await ib.admin_usage(db_conn, days=1)] == [date(2026, 10, 26)]


async def test_record_usage_splits_by_model_and_kind(db_conn):
    await _rec(db_conn, U, 10, 5, model="ollama:a", kind="chat")
    await _rec(db_conn, U, 3, 2, model="ollama:b", kind="reader")
    cur = await db_conn.execute(
        "SELECT model, kind, prompt_tokens, eval_tokens, requests FROM inference_usage "
        "WHERE user_id = %s ORDER BY model", (U,))
    assert await cur.fetchall() == [("ollama:a", "chat", 10, 5, 1), ("ollama:b", "reader", 3, 2, 1)]


async def test_record_usage_same_key_accumulates(db_conn):
    await _rec(db_conn, U, 10, 5, model="ollama:a", kind="chat")
    await _rec(db_conn, U, 3, 2, model="ollama:a", kind="chat")
    cur = await db_conn.execute(
        "SELECT prompt_tokens, eval_tokens, requests FROM inference_usage WHERE user_id = %s", (U,))
    assert await cur.fetchall() == [(13, 7, 2)]


async def test_record_usage_estimated_tokens(db_conn):
    await _rec(db_conn, U, 10, 5, estimated=True)
    await _rec(db_conn, U, 100, 50, estimated=False)
    await _rec(db_conn, U, 1, 1, estimated=True)
    cur = await db_conn.execute(
        "SELECT estimated_tokens, prompt_tokens + eval_tokens FROM inference_usage WHERE user_id = %s", (U,))
    assert await cur.fetchall() == [(17, 167)]


async def test_record_usage_rejects_unknown_kind(db_conn):
    for bad in ("other", "", "CHAT"):
        with pytest.raises(ValueError):
            await _rec(db_conn, U, 1, 1, kind=bad)
    cur = await db_conn.execute("SELECT count(*) FROM inference_usage WHERE user_id = %s", (U,))
    assert (await cur.fetchone())[0] == 0


async def test_spent_today_sums_all_rows(db_conn):
    # A row written before migration 023 (model/kind '') counts with the new ones.
    await db_conn.execute(
        "INSERT INTO inference_usage (user_id, day, model, kind, prompt_tokens, eval_tokens, requests) "
        "VALUES (%s, %s, '', '', 100, 50, 3)", (U, await ib.today(db_conn)))
    await _rec(db_conn, U, 10, 5, model="ollama:a", kind="chat")
    await _rec(db_conn, U, 1, 2, model="ollama:b", kind="reader")
    assert await ib.spent_today(db_conn, U) == (111, 57)
    state = await ib.budget_state(db_conn, U, 1000)
    assert state["remaining_tokens"] == 1000 - 168


async def test_admin_usage_default_shape_sums_models(db_conn):
    await _rec(db_conn, U, 10, 5, model="ollama:a", kind="chat")
    await _rec(db_conn, U, 3, 2, model="ollama:b", kind="reader")
    rows = await ib.admin_usage(db_conn, days=1)
    assert len(rows) == 1
    assert set(rows[0]) == {"email", "user_id", "day", "prompt_tokens", "eval_tokens", "requests", "tokens"}
    assert (rows[0]["prompt_tokens"], rows[0]["eval_tokens"], rows[0]["requests"], rows[0]["tokens"]) == (13, 7, 2, 20)
    assert isinstance(rows[0]["user_id"], str) and isinstance(rows[0]["day"], date)
