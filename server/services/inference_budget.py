"""Per-user daily inference budgets (gateway spec §4.3).

Day boundaries are UTC and computed in Python — a SQL `DEFAULT CURRENT_DATE`
would use the Postgres server's timezone and silently shift the reset time.
All functions take an explicit conn; callers own fail-open behavior (the
gateway logs and proceeds when the DB is down — chat never dies with Postgres).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _utc_today():
    return datetime.now(timezone.utc).date()


def _next_reset_at() -> datetime:
    now = datetime.now(timezone.utc)
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def _normalize_budget(value: int | None) -> int | None:
    # 0 and None both mean unlimited (spec §4.3).
    return value if value else None


async def effective_budget(conn, user_id: str, deployment_budget: int | None) -> int | None:
    cur = await conn.execute(
        "SELECT inference_daily_token_budget FROM users WHERE id = %s", (user_id,)
    )
    row = await cur.fetchone()
    per_user = row[0] if row else None
    if per_user is not None:
        return _normalize_budget(per_user)
    return _normalize_budget(deployment_budget)


async def spent_today(conn, user_id: str) -> tuple[int, int]:
    cur = await conn.execute(
        "SELECT prompt_tokens, eval_tokens FROM inference_usage "
        "WHERE user_id = %s AND day = %s",
        (user_id, _utc_today()),
    )
    row = await cur.fetchone()
    return (row[0] or 0, row[1] or 0) if row else (0, 0)


async def budget_state(conn, user_id: str, deployment_budget: int | None) -> dict:
    """{remaining_tokens, reset_at}; remaining_tokens is None when unlimited."""
    reset_at = _next_reset_at().isoformat().replace("+00:00", "Z")
    budget = await effective_budget(conn, user_id, deployment_budget)
    if budget is None:
        return {"remaining_tokens": None, "reset_at": reset_at}
    prompt, evals = await spent_today(conn, user_id)
    return {
        "remaining_tokens": max(0, budget - (prompt + evals)),
        "reset_at": reset_at,
    }


async def over_budget(conn, user_id: str, deployment_budget: int | None) -> bool:
    state = await budget_state(conn, user_id, deployment_budget)
    remaining = state["remaining_tokens"]
    return remaining is not None and remaining <= 0


async def record_usage(conn, user_id: str, prompt_tokens: int, eval_tokens: int) -> None:
    await conn.execute(
        "INSERT INTO inference_usage (user_id, day, prompt_tokens, eval_tokens, requests) "
        "VALUES (%s, %s, %s, %s, 1) "
        "ON CONFLICT (user_id, day) DO UPDATE SET "
        "prompt_tokens = inference_usage.prompt_tokens + EXCLUDED.prompt_tokens, "
        "eval_tokens = inference_usage.eval_tokens + EXCLUDED.eval_tokens, "
        "requests = inference_usage.requests + 1",
        (user_id, _utc_today(), prompt_tokens, eval_tokens),
    )


async def admin_usage(conn, days: int = 7) -> list[dict]:
    since = _utc_today() - timedelta(days=days - 1)
    cur = await conn.execute(
        "SELECT u.email, s.user_id, s.day, s.prompt_tokens, s.eval_tokens, s.requests "
        "FROM inference_usage s JOIN users u ON u.id = s.user_id "
        "WHERE s.day >= %s ORDER BY s.day DESC, u.email",
        (since,),
    )
    keys = ["email", "user_id", "day", "prompt_tokens", "eval_tokens", "requests"]
    out = []
    for r in await cur.fetchall():
        d = dict(zip(keys, r))
        d["user_id"] = str(d["user_id"])
        d["tokens"] = d["prompt_tokens"] + d["eval_tokens"]
        out.append(d)
    return out
