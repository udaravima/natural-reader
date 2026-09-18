"""/v1/auth/* — OIDC login/callback, logout, and the current-user probe."""
from __future__ import annotations

import os
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from ..auth import deps, sessions, tokens as pat, users
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
    # Keep the raw ID token in the signed OIDC-flow cookie as the RP-initiated
    # logout hint (see GET /logout).
    id_token = token.get("id_token")
    if id_token:
        request.session["id_token"] = id_token
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
    resp.delete_cookie(deps.COOKIE_NAME, path="/", domain=load_auth_config(os.environ).cookie_domain)
    return resp


async def _post_logout_url(request: Request) -> str:
    """Where the browser should land after RP-initiated logout.

    Kills only the local session when OIDC is absent. With OIDC, point the
    browser at the IdP's end-session endpoint (discovered, not hardcoded —
    Keycloak and any OIDC IdP expose ``end_session_endpoint``) so the SSO
    session dies too. Without that step the next login is a silent redirect
    through the still-live IdP session and "logout" appears to do nothing.
    """
    cfg = load_auth_config(os.environ)
    frontend = "/"
    if cfg.oidc_redirect_url:
        parts = urlsplit(cfg.oidc_redirect_url)
        frontend = f"{parts.scheme}://{parts.netloc}/"
    end_session = None
    try:
        client = _client()
        meta = await client.load_server_metadata()
        end_session = meta.get("end_session_endpoint")
    except Exception:
        pass  # OIDC unconfigured, IdP unreachable, or no logout support.
    if not end_session:
        return frontend
    # id_token_hint is the portable proof of the session being ended; KC also
    # accepts client_id for sessions logged in before the hint was stashed.
    params = {"post_logout_redirect_uri": frontend}
    id_token = request.session.get("id_token")
    if id_token:
        params["id_token_hint"] = id_token
    else:
        params["client_id"] = cfg.oidc_client_id
    return f"{end_session}?{urlencode(params)}"


@router.get("/logout")
async def logout_redirect(request: Request, conn=Depends(deps.get_conn)):
    # Navigation form of POST /logout: the browser must actually visit the
    # IdP so the IdP can clear its own cookie on its own origin. A fetch would
    # follow the redirect server-side, where cross-origin Set-Cookie never
    # lands in the user's cookie jar.
    cookie = request.cookies.get(deps.COOKIE_NAME)
    if cookie:
        await sessions.revoke_session(conn, cookie)
    # Clear the OIDC-flow cookie too (it carries the id_token hint). Starlette's
    # SessionMiddleware sees an empty session and drops its cookie.
    request.session.clear()
    cfg = load_auth_config(os.environ)
    resp = RedirectResponse(url=await _post_logout_url(request), status_code=303)
    resp.delete_cookie(deps.COOKIE_NAME, path="/", domain=cfg.cookie_domain)
    return resp


@router.get("/me")
async def me(principal: deps.Principal = Depends(deps.get_current_user)):
    return {"id": principal.user_id, "email": principal.email, "role": principal.role}


# ---------- Personal access tokens ----------

class TokenCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    expires_at: str | None = None  # ISO-8601; None = no expiry


@router.post("/tokens")
async def create_pat(
    body: TokenCreateIn,
    principal: deps.Principal = Depends(deps.get_current_user),
    conn=Depends(deps.get_conn),
):
    tid, raw = await pat.create_token(
        conn, principal.user_id, body.name, expires_at=body.expires_at
    )
    return {"id": tid, "token": raw}


@router.get("/tokens")
async def list_pats(
    principal: deps.Principal = Depends(deps.get_current_user),
    conn=Depends(deps.get_conn),
):
    return await pat.list_tokens(conn, principal.user_id)


@router.delete("/tokens/{token_id}", status_code=204)
async def revoke_pat(
    token_id: str,
    principal: deps.Principal = Depends(deps.get_current_user),
    conn=Depends(deps.get_conn),
):
    if not await pat.revoke_token(conn, principal.user_id, token_id):
        raise HTTPException(status_code=404, detail="Token not found")
    return Response(status_code=204)
