"""Per-user daily inference budgets (gateway spec §4.3).

Usage rows are per user, day, model and kind (`chat` or `reader`; `''` for rows written before migration 023,
whose model and kind are unknown). The budget is still per user and day: `spent_today` sums every row of the day.

A day is the deployment's (services/deployment_settings.py: the console's timezone, else TZ, else UTC),
computed in Python — a SQL `DEFAULT CURRENT_DATE` would use the Postgres server's timezone and silently shift
the reset time. Rows already written keep their days: changing the zone moves the boundary from then on and
rewrites nothing. All functions take an explicit conn, which also reads the zone; callers own fail-open behavior (the
gateway logs and proceeds when the DB is down — chat never dies with Postgres).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .deployment_settings import deployment_zone


def _now() -> datetime:
    """The clock, aware UTC: the seam tests patch."""
    return datetime.now(timezone.utc)


async def today(conn) -> date:
    return _now().astimezone(await deployment_zone(conn)).date()


def next_reset_at(now: datetime, zone: ZoneInfo) -> datetime:
    """The next local midnight in `zone`, as UTC. Built from the local date, not by adding 24 hours, so the night
    the clocks change still resets at midnight."""
    tomorrow = now.astimezone(zone).date() + timedelta(days=1)
    return datetime.combine(tomorrow, time(0), tzinfo=zone).astimezone(timezone.utc)


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
    """(prompt, eval) tokens spent today, summed over every model and kind."""
    cur = await conn.execute(
        "SELECT COALESCE(SUM(prompt_tokens), 0), COALESCE(SUM(eval_tokens), 0) FROM inference_usage "
        "WHERE user_id = %s AND day = %s",
        (user_id, await today(conn)),
    )
    row = await cur.fetchone()
    return (int(row[0]), int(row[1]))


async def budget_state(conn, user_id: str, deployment_budget: int | None) -> dict:
    """{remaining_tokens, reset_at}; remaining_tokens is None when unlimited."""
    reset_at = next_reset_at(_now(), await deployment_zone(conn)).isoformat().replace("+00:00", "Z")
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


KINDS = ("chat", "reader")


async def record_usage(conn, user_id: str, prompt_tokens: int, eval_tokens: int, *,
                       model: str, kind: str, estimated: bool) -> None:
    """Add one request to today's row for (user, model, kind). `model` is the canonical provider:name; `estimated`
    marks tokens counted by the chars/4 estimate rather than reported by the provider."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, not {kind!r}")
    estimated_tokens = prompt_tokens + eval_tokens if estimated else 0
    await conn.execute(
        "INSERT INTO inference_usage (user_id, day, model, kind, prompt_tokens, eval_tokens, requests, "
        "estimated_tokens) VALUES (%s, %s, %s, %s, %s, %s, 1, %s) "
        "ON CONFLICT (user_id, day, model, kind) DO UPDATE SET "
        "prompt_tokens = inference_usage.prompt_tokens + EXCLUDED.prompt_tokens, "
        "eval_tokens = inference_usage.eval_tokens + EXCLUDED.eval_tokens, "
        "requests = inference_usage.requests + 1, "
        "estimated_tokens = inference_usage.estimated_tokens + EXCLUDED.estimated_tokens",
        (user_id, await today(conn), model, kind, prompt_tokens, eval_tokens, estimated_tokens),
    )


async def admin_usage(conn, days: int = 7) -> list[dict]:
    """One dict per user per day, summed over model and kind."""
    since = await today(conn) - timedelta(days=days - 1)
    cur = await conn.execute(
        "SELECT u.email, s.user_id, s.day, SUM(s.prompt_tokens)::bigint, SUM(s.eval_tokens)::bigint, "
        "SUM(s.requests)::bigint "
        "FROM inference_usage s JOIN users u ON u.id = s.user_id "
        "WHERE s.day >= %s GROUP BY u.email, s.user_id, s.day ORDER BY s.day DESC, u.email",
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
