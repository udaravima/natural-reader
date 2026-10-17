"""Auth dependencies: resolve a Principal from a Bearer PAT or session cookie,
and hand out DB connections. Deny-by-default — no valid credential is a 401."""
from __future__ import annotations

import os
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request

from ..db import get_pool
from . import scopes as token_scopes
from . import sessions, tokens, users
from .config import dev_bypass_allowed, load_auth_config

COOKIE_NAME = "nr_session"


@dataclass
class Principal:
    """Who is calling. `capabilities` and `role` are already narrowed to the
    token's scopes, so every require_capability / require_admin check honours
    them. `scopes` is None for a session or a full-access token;
    `account_capabilities` is the account's own set, before narrowing."""
    user_id: str
    email: str
    role: str
    capabilities: frozenset[str] = frozenset()
    scopes: frozenset[str] | None = None
    account_capabilities: frozenset[str] | None = None

    @property
    def can_read_aloud(self) -> bool:
        account = self.account_capabilities if self.account_capabilities is not None else self.capabilities
        return token_scopes.allows_read_aloud(account, self.scopes)


async def get_conn():
    """Yield a pooled connection; it commits when the route function returns.
    Overridden in tests to inject a transactional test connection.

    Always take it as Depends(get_conn, scope="function"). With FastAPI's
    default "request" scope this block exits only after the response is sent
    and its background tasks have run: a client re-reading at once missed its
    own write, and an upload kept its connection checked out for the whole
    background index. tests/test_conn_commits_before_response.py enforces it."""
    pool = get_pool()
    async with pool.connection() as conn:
        yield conn


def _principal(row: dict) -> Principal:
    account = frozenset(row.get("capabilities") or [])
    raw_scopes = row.get("token_scopes")
    scopes = frozenset(raw_scopes) if raw_scopes is not None else None
    caps = token_scopes.narrow(account, scopes)
    role = "admin" if "admin" in caps else "member"
    return Principal(user_id=row["id"], email=row["email"], role=role, capabilities=caps,
                     scopes=scopes, account_capabilities=account)


async def get_current_user(request: Request, conn=Depends(get_conn, scope="function")) -> Principal:
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


def require_capability(name: str):
    """Deny-by-default gate for a feature capability (reader/chat/admin)."""
    async def _dep(principal: Principal = Depends(get_current_user)) -> Principal:
        if name not in principal.capabilities:
            raise HTTPException(
                status_code=403,
                detail={"error": "missing_capability", "capability": name},
            )
        return principal
    return _dep


async def require_read_aloud(principal: Principal = Depends(get_current_user)) -> Principal:
    """The TTS routes: the account holds `reader`, and the credential is a
    session, a full-access token, or a token scoped read_aloud or documents."""
    if not principal.can_read_aloud:
        raise HTTPException(
            status_code=403,
            detail={"error": "missing_capability", "capability": "reader"},
        )
    return principal


async def require_full_credential(principal: Principal = Depends(get_current_user)) -> Principal:
    """Token management: a session or a full-access token. A scoped token can't
    mint itself a replacement or list the others."""
    if principal.scopes is not None:
        raise HTTPException(
            status_code=403,
            detail={"error": "token_scope",
                    "message": "A scoped access token can't manage access tokens."},
        )
    return principal


_kc_admin = None


async def get_kc_admin():
    """Yield a shared KeycloakAdmin, or None when the service account is
    unconfigured (callers then use the app-only degraded path)."""
    global _kc_admin
    cfg = load_auth_config(os.environ)
    from .kc_admin import build_kc_admin
    if _kc_admin is None:
        _kc_admin = build_kc_admin(cfg)
    return _kc_admin
