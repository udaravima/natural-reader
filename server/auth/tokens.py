"""Personal Access Tokens: Bearer credentials for the extension + scripts.
Raw token (nrp_...) shown once; sha256 stored."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from . import touch, users


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def create_token(conn, user_id: str, name: str, *, expires_at=None, scopes=None):
    """scopes: a list of scopes.SCOPES names, or None for full access."""
    raw = "nrp_" + secrets.token_urlsafe(32)
    cur = await conn.execute(
        "INSERT INTO personal_access_tokens (user_id, name, token_hash, expires_at, scopes) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (user_id, name, _hash(raw), expires_at, list(scopes) if scopes is not None else None),
    )
    tid = str((await cur.fetchone())[0])
    return tid, raw


async def resolve_token(conn, raw_token: str) -> dict | None:
    """The token's user row, with the token's scopes under `token_scopes`
    (None = full access)."""
    if not raw_token or not raw_token.startswith("nrp_"):
        return None
    cur = await conn.execute(
        "SELECT id, user_id, expires_at, last_used_at, scopes FROM personal_access_tokens "
        "WHERE token_hash=%s",
        (_hash(raw_token),),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    tid, user_id, expires_at, last_used_at, scopes = row
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        return None
    if touch.due(last_used_at):
        await touch.stamp(
            "UPDATE personal_access_tokens SET last_used_at=now() WHERE id=%s", (tid,)
        )
    user = await users.get_user(conn, str(user_id))
    if user is not None:
        user = {**user, "token_scopes": scopes}
    return user


async def list_tokens(conn, user_id: str) -> list[dict]:
    cur = await conn.execute(
        "SELECT id, name, created_at, last_used_at, expires_at, scopes "
        "FROM personal_access_tokens WHERE user_id=%s ORDER BY created_at",
        (user_id,),
    )
    keys = ["id", "name", "created_at", "last_used_at", "expires_at", "scopes"]
    out = []
    for r in await cur.fetchall():
        d = dict(zip(keys, r))
        d["id"] = str(d["id"])
        out.append(d)
    return out


async def revoke_token(conn, user_id: str, token_id: str) -> bool:
    cur = await conn.execute(
        "DELETE FROM personal_access_tokens WHERE id=%s AND user_id=%s RETURNING id",
        (token_id, user_id),
    )
    return await cur.fetchone() is not None
