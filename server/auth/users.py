"""User records + JIT provisioning (spec §4). Every function takes an explicit
connection so it composes inside a request transaction and is unit-testable
against a rolled-back test connection."""
from __future__ import annotations

from typing import Any

SEED_ADMIN_ID = "00000000-0000-0000-0000-000000000001"

_KEYS = ["id", "email", "display_name", "role", "status", "oidc_iss", "oidc_sub"]
_COLS = ", ".join(_KEYS)


def _row(record) -> dict[str, Any] | None:
    if record is None:
        return None
    d = dict(zip(_KEYS, record))
    d["id"] = str(d["id"])
    return d


async def get_user(conn, user_id: str) -> dict[str, Any] | None:
    cur = await conn.execute(f"SELECT {_COLS} FROM users WHERE id = %s", (user_id,))
    return _row(await cur.fetchone())


async def list_users(conn) -> list[dict[str, Any]]:
    cur = await conn.execute(f"SELECT {_COLS} FROM users ORDER BY created_at")
    return [_row(r) for r in await cur.fetchall()]


async def set_status(conn, user_id: str, status: str) -> None:
    await conn.execute(
        "UPDATE users SET status=%s, updated_at=now() WHERE id=%s", (status, user_id)
    )


async def set_role(conn, user_id: str, role: str) -> None:
    await conn.execute(
        "UPDATE users SET role=%s, updated_at=now() WHERE id=%s", (role, user_id)
    )


async def _fetch_by(conn, where: str, params) -> dict[str, Any] | None:
    cur = await conn.execute(f"SELECT {_COLS} FROM users WHERE {where}", params)
    return _row(await cur.fetchone())


async def _only_unlinked_seed(conn) -> bool:
    cur = await conn.execute("SELECT count(*), count(oidc_sub) FROM users")
    total, linked = await cur.fetchone()
    return total == 1 and linked == 0


async def resolve_or_provision_user(
    conn, *, iss: str, sub: str, email: str, display_name: str | None = None
) -> dict[str, Any]:
    # 1) Known identity — refresh email/display_name, return it.
    found = await _fetch_by(conn, "oidc_iss=%s AND oidc_sub=%s", (iss, sub))
    if found:
        await conn.execute(
            "UPDATE users SET email=%s, display_name=COALESCE(%s, display_name), "
            "updated_at=now() WHERE id=%s",
            (email, display_name, found["id"]),
        )
        return await get_user(conn, found["id"])

    # 2) Pre-provisioned/seed row by email, not yet linked -> link it.
    by_email = await _fetch_by(conn, "email=%s", (email,))
    if by_email and by_email["oidc_sub"] is None:
        await conn.execute(
            "UPDATE users SET oidc_iss=%s, oidc_sub=%s, "
            "display_name=COALESCE(%s, display_name), updated_at=now() WHERE id=%s",
            (iss, sub, display_name, by_email["id"]),
        )
        return await get_user(conn, by_email["id"])
    if by_email:
        raise ValueError("email already linked to another identity")

    # 3) Brand-new identity. If only the unlinked seed exists, this first real
    #    login claims it (first-user-admin); otherwise a pending member.
    if await _only_unlinked_seed(conn):
        await conn.execute(
            "UPDATE users SET oidc_iss=%s, oidc_sub=%s, email=%s, "
            "display_name=COALESCE(%s, display_name), updated_at=now() WHERE id=%s",
            (iss, sub, email, display_name, SEED_ADMIN_ID),
        )
        return await get_user(conn, SEED_ADMIN_ID)

    cur = await conn.execute(
        "INSERT INTO users (oidc_iss, oidc_sub, email, display_name, role, status) "
        "VALUES (%s, %s, %s, %s, 'member', 'pending') RETURNING id",
        (iss, sub, email, display_name),
    )
    new_id = (await cur.fetchone())[0]
    return await get_user(conn, str(new_id))
