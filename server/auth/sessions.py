"""DB-backed sessions. The cookie holds a random token; only its sha256 is
stored, so a DB leak doesn't yield usable session cookies."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from . import touch


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def session_id(raw: str) -> str:
    """The id a session is listed and signed out by (Settings → Profile): the
    stored hash, which can't be turned back into a usable cookie."""
    return _hash(raw)


async def list_sessions(conn, user_id: str, *, idle_hours: int = 0) -> list[dict]:
    """The person's live sessions, most recently used first. One past its
    expiry or idle limit is left out (resolve_session would refuse it)."""
    where = "user_id = %s AND expires_at > now()"
    params: list = [user_id]
    if idle_hours:
        where += " AND last_seen_at > now() - make_interval(hours => %s)"
        params.append(idle_hours)
    cur = await conn.execute(
        f"SELECT id, created_at, last_seen_at, expires_at, user_agent FROM sessions "
        f"WHERE {where} ORDER BY last_seen_at DESC", params)
    return [{"id": r[0], "created_at": r[1], "last_seen_at": r[2], "expires_at": r[3],
             "user_agent": r[4]} for r in await cur.fetchall()]


async def end_session(conn, user_id: str, sid: str) -> bool:
    """Sign one of the person's own sessions out; False when it isn't theirs."""
    cur = await conn.execute("DELETE FROM sessions WHERE id = %s AND user_id = %s", (sid, user_id))
    return cur.rowcount > 0


async def end_other_sessions(conn, user_id: str, keep: str | None) -> int:
    """Sign out every session of the person's except `keep` (this browser's)."""
    cur = await conn.execute("DELETE FROM sessions WHERE user_id = %s AND id IS DISTINCT FROM %s",
                             (user_id, keep))
    return cur.rowcount


async def create_session(
    conn, user_id: str, *, ttl_hours: int = 168, user_agent: str | None = None
) -> str:
    raw = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    await conn.execute(
        "INSERT INTO sessions (id, user_id, expires_at, user_agent) "
        "VALUES (%s, %s, %s, %s)",
        (_hash(raw), user_id, expires, user_agent),
    )
    return raw


async def resolve_session(conn, raw_token: str, *, idle_hours: int = 0) -> dict | None:
    """The session's user, or None when it is unknown, past its absolute
    expiry, or unused for more than `idle_hours` (0 = no idle limit;
    SESSION_IDLE_HOURS). last_seen_at is stamped at most once a minute
    (touch.TOUCH_EVERY), so idleness is measured to about a minute."""
    # Imported lazily to avoid a users<->sessions import cycle at module load.
    from . import users

    if not raw_token:
        return None
    token_hash = _hash(raw_token)
    cur = await conn.execute(
        "SELECT user_id, expires_at, last_seen_at FROM sessions WHERE id = %s", (token_hash,)
    )
    row = await cur.fetchone()
    if row is None:
        return None
    user_id, expires_at, last_seen_at = row
    now = datetime.now(timezone.utc)
    if expires_at <= now:
        return None
    if idle_hours and last_seen_at is not None and last_seen_at <= now - timedelta(hours=idle_hours):
        await touch.stamp("DELETE FROM sessions WHERE id = %s", (token_hash,))
        return None
    if touch.due(last_seen_at):
        await touch.stamp(
            "UPDATE sessions SET last_seen_at = now() WHERE id = %s", (token_hash,)
        )
    return await users.get_user(conn, str(user_id))


async def revoke_session(conn, raw_token: str) -> None:
    await conn.execute("DELETE FROM sessions WHERE id = %s", (_hash(raw_token),))
