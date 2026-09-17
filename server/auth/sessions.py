"""DB-backed sessions. The cookie holds a random token; only its sha256 is
stored, so a DB leak doesn't yield usable session cookies."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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


async def resolve_session(conn, raw_token: str) -> dict | None:
    # Imported lazily to avoid a users<->sessions import cycle at module load.
    from . import users

    if not raw_token:
        return None
    token_hash = _hash(raw_token)
    cur = await conn.execute(
        "SELECT user_id, expires_at FROM sessions WHERE id = %s", (token_hash,)
    )
    row = await cur.fetchone()
    if row is None:
        return None
    user_id, expires_at = row
    if expires_at <= datetime.now(timezone.utc):
        return None
    await conn.execute(
        "UPDATE sessions SET last_seen_at = now() WHERE id = %s", (token_hash,)
    )
    return await users.get_user(conn, str(user_id))


async def revoke_session(conn, raw_token: str) -> None:
    await conn.execute("DELETE FROM sessions WHERE id = %s", (_hash(raw_token),))
