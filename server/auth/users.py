"""User records + JIT provisioning (spec §4). Every function takes an explicit
connection so it composes inside a request transaction and is unit-testable
against a rolled-back test connection."""
from __future__ import annotations

from typing import Any

from psycopg import errors as pg_errors

SEED_ADMIN_ID = "00000000-0000-0000-0000-000000000001"

_KEYS = [
    "id", "email", "display_name", "role", "status", "oidc_iss", "oidc_sub",
    "inference_daily_token_budget", "created_at",
]
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
    if status != "active":
        # Hard-revoke: a disabled/pending user's live sessions die immediately.
        # get_current_user also re-checks status on every request as a backstop.
        await conn.execute("DELETE FROM sessions WHERE user_id=%s", (user_id,))


async def set_role(conn, user_id: str, role: str) -> None:
    await conn.execute(
        "UPDATE users SET role=%s, updated_at=now() WHERE id=%s", (role, user_id)
    )


async def set_inference_budget(conn, user_id: str, budget: int | None) -> None:
    """Daily inference token budget. NULL = deployment default; 0 = unlimited."""
    await conn.execute(
        "UPDATE users SET inference_daily_token_budget=%s, updated_at=now() "
        "WHERE id=%s",
        (budget, user_id),
    )


async def _fetch_by(conn, where: str, params) -> dict[str, Any] | None:
    cur = await conn.execute(f"SELECT {_COLS} FROM users WHERE {where}", params)
    return _row(await cur.fetchone())


async def resolve_or_provision_user(
    conn,
    *,
    iss: str,
    sub: str,
    email: str,
    display_name: str | None = None,
    email_verified: bool = False,
) -> dict[str, Any]:
    """Resolve an OIDC identity to a local user, provisioning on first sight.

    `email_verified` must reflect the OIDC `email_verified` claim: an email is
    only trusted to CLAIM a pre-provisioned account when the IdP verified it.
    The first-user-admin path (branch 3) is not email-based and so is unaffected.
    """
    # 1) Known identity — refresh email/display_name, return it.
    found = await _fetch_by(conn, "oidc_iss=%s AND oidc_sub=%s", (iss, sub))
    if found:
        await conn.execute(
            "UPDATE users SET email=%s, display_name=COALESCE(%s, display_name), "
            "updated_at=now() WHERE id=%s",
            (email, display_name, found["id"]),
        )
        return await get_user(conn, found["id"])

    # 2) A row already carries this email.
    by_email = await _fetch_by(conn, "email=%s", (email,))
    if by_email:
        if by_email["oidc_sub"] is not None:
            raise ValueError("email already linked to another identity")
        if not email_verified:
            # An unverified email must never claim a pre-provisioned account —
            # otherwise anyone who can mint a token with the admin's email wins.
            raise ValueError("email not verified; cannot claim pre-provisioned account")
        await conn.execute(
            "UPDATE users SET oidc_iss=%s, oidc_sub=%s, "
            "display_name=COALESCE(%s, display_name), updated_at=now() WHERE id=%s",
            (iss, sub, display_name, by_email["id"]),
        )
        return await get_user(conn, by_email["id"])

    # 3) Brand-new identity. Atomically claim the still-unlinked seed admin
    #    (first-user-admin). The conditional UPDATE serializes concurrent first
    #    logins via a row lock: exactly one flips oidc_sub from NULL, the rest
    #    see rowcount 0 and fall through to a pending member.
    cur = await conn.execute(
        "UPDATE users SET oidc_iss=%s, oidc_sub=%s, email=%s, "
        "display_name=COALESCE(%s, display_name), updated_at=now() "
        "WHERE id=%s AND oidc_sub IS NULL",
        (iss, sub, email, display_name, SEED_ADMIN_ID),
    )
    if cur.rowcount == 1:
        return await get_user(conn, SEED_ADMIN_ID)

    cur = await conn.execute(
        "INSERT INTO users (oidc_iss, oidc_sub, email, display_name, role, status) "
        "VALUES (%s, %s, %s, %s, 'member', 'pending') RETURNING id",
        (iss, sub, email, display_name),
    )
    new_id = (await cur.fetchone())[0]
    return await get_user(conn, str(new_id))


async def enroll_user(
    conn,
    *,
    email: str,
    display_name: str | None = None,
    role: str = "member",
    status: str = "pending",
    inference_daily_token_budget: int | None = None,
) -> dict[str, Any]:
    """Admin-side pre-provisioning: a row with oidc_sub NULL that waits for
    its owner's first verified-email login (resolver branch 2 claims it,
    preserving role/status/budget). Raises ValueError on a taken email."""
    try:
        cur = await conn.execute(
            "INSERT INTO users (email, display_name, role, status, "
            "inference_daily_token_budget) VALUES (%s, %s, %s, %s, %s) "
            "RETURNING id",
            (email, display_name, role, status, inference_daily_token_budget),
        )
    except pg_errors.UniqueViolation:
        raise ValueError("email already exists")
    return await get_user(conn, str((await cur.fetchone())[0]))


async def delete_user(conn, user_id: str) -> None:
    """Hard delete. Every referencing table cascades (sessions, PATs,
    inference_usage, documents + chunks, chat_sessions + messages/events) —
    callers must sweep user files (e.g. stored PDFs) BEFORE/around this."""
    await conn.execute("DELETE FROM users WHERE id=%s", (user_id,))
