"""/v1/auth/* — OIDC login/callback, logout, and the current-user probe."""
from __future__ import annotations

import logging
import os
from urllib.parse import urlencode, urlsplit

from authlib.integrations.starlette_client import OAuthError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from ..auth import deps, sessions, tokens as pat, users
from ..auth.capabilities import caps_from_claims
from ..auth.config import load_auth_config
from ..auth.oidc import build_oauth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/auth", tags=["auth"])

_oauth = None


def _frontend_root() -> str:
    """The SPA origin's root, derived from OIDC_REDIRECT_URL (same-origin with
    the backend). Where a failed or abandoned login flow is bounced back to —
    the SPA re-probes /auth/me, sees 401, and shows the login screen."""
    cfg = load_auth_config(os.environ)
    if cfg.oidc_redirect_url:
        parts = urlsplit(cfg.oidc_redirect_url)
        return f"{parts.scheme}://{parts.netloc}/"
    return "/"


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
async def callback(request: Request, conn=Depends(deps.get_conn),
                   kc=Depends(deps.get_kc_admin)):
    # Keycloak can redirect back with an error instead of a code — an expired
    # auth flow (temporarily_unavailable / authentication_expired), a cancelled
    # login, or a consent denial. authorize_access_token would raise OAuthError,
    # which was surfacing as an unhandled 500. Treat any of these as a failed
    # login: bounce to the SPA, which re-probes /auth/me → 401 → login screen.
    if request.query_params.get("error"):
        logger.info(
            "OIDC callback returned error=%s (%s); redirecting to login",
            request.query_params.get("error"),
            request.query_params.get("error_description"),
        )
        return RedirectResponse(url=_frontend_root(), status_code=303)
    try:
        token = await _client().authorize_access_token(request)
    except OAuthError as e:
        # State/PKCE mismatch, an error param we didn't catch above, or an IdP
        # hiccup during the code exchange — same UX, never a 500.
        logger.info("OIDC token exchange failed (%s); redirecting to login", e)
        return RedirectResponse(url=_frontend_root(), status_code=303)
    claims = token.get("userinfo") or {}
    sub, iss, email = claims.get("sub"), claims.get("iss"), claims.get("email")
    if not (sub and email):
        raise HTTPException(status_code=400, detail="OIDC token missing sub/email")
    caps = caps_from_claims(claims)
    try:
        user = await users.resolve_or_provision_user(
            conn,
            iss=iss,
            sub=sub,
            email=email,
            display_name=claims.get("name"),
            # Only a verified email may claim a pre-provisioned account.
            email_verified=bool(claims.get("email_verified")),
            capabilities=caps,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    # Bootstrap: the founder who claimed the seed admin must also hold the
    # realm roles in Keycloak, or the next login's sync would demote them.
    if kc is not None and user["id"] == users.SEED_ADMIN_ID:
        try:
            await kc.assign_realm_roles(sub, ["admin", "reader", "chat"])
        except Exception:  # noqa: BLE001 — best-effort; app row is authoritative
            pass
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
    frontend = _frontend_root()
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
    return {"id": principal.user_id, "email": principal.email,
            "role": principal.role, "capabilities": sorted(principal.capabilities)}


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
