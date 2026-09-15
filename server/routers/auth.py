"""/v1/auth/* — OIDC login/callback, logout, and the current-user probe."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from ..auth import deps, sessions, users
from ..auth.config import load_auth_config
from ..auth.oidc import build_oauth

router = APIRouter(prefix="/v1/auth", tags=["auth"])

_oauth = None


def _client():
    """The registered OIDC client, or 503 if OIDC isn't configured."""
    global _oauth
    cfg = load_auth_config(os.environ)
    if not (cfg.oidc_issuer and cfg.oidc_client_id):
        raise HTTPException(status_code=503, detail="OIDC is not configured")
    if _oauth is None:
        _oauth = build_oauth(cfg)
    return _oauth.oidc


def _safe_next(raw: str | None) -> str:
    # Only same-site absolute paths — never an absolute URL (open-redirect guard).
    if raw and raw.startswith("/") and not raw.startswith("//"):
        return raw
    return "/"


def _set_cookie(resp: Response, token: str) -> None:
    cfg = load_auth_config(os.environ)
    resp.set_cookie(
        deps.COOKIE_NAME,
        token,
        max_age=cfg.session_ttl_hours * 3600,
        httponly=True,
        secure=cfg.cookie_secure,
        samesite="lax",
        domain=cfg.cookie_domain,
        path="/",
    )


@router.get("/login")
async def login(request: Request, next: str = "/"):
    request.session["post_login"] = _safe_next(next)
    cfg = load_auth_config(os.environ)
    return await _client().authorize_redirect(request, cfg.oidc_redirect_url)


@router.get("/callback")
async def callback(request: Request, conn=Depends(deps.get_conn)):
    token = await _client().authorize_access_token(request)
    claims = token.get("userinfo") or {}
    sub, iss, email = claims.get("sub"), claims.get("iss"), claims.get("email")
    if not (sub and email):
        raise HTTPException(status_code=400, detail="OIDC token missing sub/email")
    try:
        user = await users.resolve_or_provision_user(
            conn,
            iss=iss,
            sub=sub,
            email=email,
            display_name=claims.get("name"),
            # Only a verified email may claim a pre-provisioned account.
            email_verified=bool(claims.get("email_verified")),
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    raw = await sessions.create_session(
        conn, user["id"], user_agent=request.headers.get("user-agent")
    )
    dest = request.session.pop("post_login", "/")
    resp = RedirectResponse(url=dest, status_code=303)
    _set_cookie(resp, raw)
    return resp


@router.post("/logout")
async def logout(request: Request, conn=Depends(deps.get_conn)):
    cookie = request.cookies.get(deps.COOKIE_NAME)
    if cookie:
        await sessions.revoke_session(conn, cookie)
    resp = Response(status_code=204)
    resp.delete_cookie(deps.COOKIE_NAME, path="/")
    return resp


@router.get("/me")
async def me(principal: deps.Principal = Depends(deps.get_current_user)):
    return {"id": principal.user_id, "email": principal.email, "role": principal.role}
