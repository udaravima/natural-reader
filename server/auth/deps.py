"""Auth dependencies: resolve a Principal from a Bearer PAT or session cookie,
and hand out DB connections. Deny-by-default — no valid credential is a 401."""
from __future__ import annotations

import os
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request

from ..db import get_pool
from . import sessions, tokens, users
from .config import dev_bypass_allowed, load_auth_config

COOKIE_NAME = "nr_session"


@dataclass
class Principal:
    user_id: str
    email: str
    role: str


async def get_conn():
    """Yield a pooled connection. Overridden in tests to inject a transactional
    test connection."""
    pool = get_pool()
    async with pool.connection() as conn:
        yield conn


def _principal(row: dict) -> Principal:
    return Principal(user_id=row["id"], email=row["email"], role=row["role"])


async def get_current_user(request: Request, conn=Depends(get_conn)) -> Principal:
    cfg = load_auth_config(os.environ)
    host = os.environ.get("HOST", "127.0.0.1")
    if dev_bypass_allowed(cfg, host):
        row = await users.get_user(conn, users.SEED_ADMIN_ID)
        return _principal(row)

    row = None
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        row = await tokens.resolve_token(conn, auth[7:].strip())
    if row is None:
        cookie = request.cookies.get(COOKIE_NAME)
        if cookie:
            row = await sessions.resolve_session(conn, cookie)
    if row is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if row["status"] != "active":
        raise HTTPException(
            status_code=403,
            detail={"status": row["status"], "message": f"Account {row['status']}"},
        )
    return _principal(row)


async def require_admin(principal: Principal = Depends(get_current_user)) -> Principal:
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return principal
