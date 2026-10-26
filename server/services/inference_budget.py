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
    """One dict per user per day, summed over model and kind: the `user_day` grouping (the route's old shape)."""
    return await admin_usage_grouped(conn, days=days, group="user_day", user_id=None, model=None)


_GROUPS = ("user_day", "model", "user", "day")


async def admin_usage_grouped(conn, *, days: int, group: str, user_id: str | None, model: str | None) -> list[dict]:
    """The console's usage numbers over the last `days` deployment days (ending today, `today(conn)`), summed by
    `group`:

    - `user_day`: one row per person per day, `admin_usage`'s shape (email, user_id, day, prompt_tokens,
      eval_tokens, requests, tokens), newest day first;
    - `model`: one row per stored model id, biggest `tokens` first, then `model`;
    - `user`: one row per person (user_id, email), biggest `tokens` first, then `email`;
    - `day`: one row per day (YYYY-MM-DD), newest first.

    `user_id` and `model` filter before grouping and combine with any group; `model=""` matches the rows written
    before migration 023 (model unknown). The `model`, `user` and `day` rows also carry `chat_tokens` and
    `reader_tokens` (the per-kind split; rows of kind `''` count in `tokens` only) and `estimated_tokens` (tokens
    counted by the chars/4 estimate rather than reported by the provider). `tokens` is prompt plus eval. The local
    or cloud tag is not stored: the route adds it at read time from the provider's current mark."""
    if group not in _GROUPS:
        raise ValueError(f"group must be one of {_GROUPS}, not {group!r}")
    since = await today(conn) - timedelta(days=days - 1)
    where, params = ["s.day >= %s"], [since]
    if user_id is not None:
        where.append("s.user_id = %s")
        params.append(user_id)
    if model is not None:
        where.append("s.model = %s")
        params.append(model)
    dims = {
        "user_day": (["u.email", "s.user_id", "s.day"], "s.day DESC, u.email"),
        "model": (["s.model"], "SUM(s.prompt_tokens + s.eval_tokens) DESC, s.model"),
        "user": (["u.email", "s.user_id"], "SUM(s.prompt_tokens + s.eval_tokens) DESC, u.email"),
        "day": (["s.day"], "s.day DESC"),
    }
    cols, order = dims[group]
    sums = [
        ("prompt_tokens", "SUM(s.prompt_tokens)::bigint"),
        ("eval_tokens", "SUM(s.eval_tokens)::bigint"),
        ("requests", "SUM(s.requests)::bigint"),
    ]
    if group != "user_day":
        sums += [
            ("chat_tokens", "COALESCE(SUM(s.prompt_tokens + s.eval_tokens) FILTER (WHERE s.kind = 'chat'), 0)::bigint"),
            ("reader_tokens", "COALESCE(SUM(s.prompt_tokens + s.eval_tokens) FILTER (WHERE s.kind = 'reader'), 0)::bigint"),
            ("estimated_tokens", "SUM(s.estimated_tokens)::bigint"),
        ]
    join = "JOIN users u ON u.id = s.user_id " if any(c.startswith("u.") for c in cols) else ""
    cur = await conn.execute(
        f"SELECT {', '.join(cols)}, {', '.join(e for _, e in sums)} FROM inference_usage s {join}"
        f"WHERE {' AND '.join(where)} GROUP BY {', '.join(cols)} ORDER BY {order}",
        params,
    )
    keys = [c.split(".")[1] for c in cols] + [k for k, _ in sums]
    out = []
    for r in await cur.fetchall():
        d = dict(zip(keys, r))
        if "user_id" in d:
            d["user_id"] = str(d["user_id"])
        if "day" in d:
            d["day"] = d["day"].isoformat()
        d["tokens"] = d["prompt_tokens"] + d["eval_tokens"]
        out.append(d)
    return out
