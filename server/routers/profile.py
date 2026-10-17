"""Settings → Profile: a profile picture, and the browsers signed in.

Name, email, password and two-step sign-in belong to the sign-in provider
(Keycloak), which refreshes the name and email at every sign-in; /v1/auth/me
says where its account page is (account_url)."""
from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, Depends, Request, Response

from ..auth import deps, sessions
from ..auth.config import load_auth_config
from ..http_errors import refusal
from ..services import avatars

router = APIRouter(tags=["profile"])


async def _read_capped(request: Request, cap: int) -> bytes | None:
    """The request body, or None once it passes `cap` bytes (read no further)."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > cap:
        return None
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > cap:
            return None
    return bytes(body)


@router.put("/v1/auth/me/avatar")
async def put_avatar(request: Request,
                     principal: deps.Principal = Depends(deps.require_full_credential),
                     conn=Depends(deps.get_conn, scope="function")):
    """The body is the image itself (PNG, JPEG or WebP, at most 256 KB)."""
    data = await _read_capped(request, avatars.MAX_BYTES)
    if data is None:
        raise refusal(413, "too_large", "That picture is over 256 KB. Pick a smaller one.")
    kind = avatars.sniff(data)
    if kind is None:
        raise refusal(415, "not_an_image", "That isn't a PNG, JPEG or WebP picture.")
    updated = await avatars.save(conn, principal.user_id, data, kind)
    return {"avatar_url": avatars.url(principal.user_id, updated)}


@router.delete("/v1/auth/me/avatar", status_code=204)
async def delete_avatar(principal: deps.Principal = Depends(deps.require_full_credential),
                        conn=Depends(deps.get_conn, scope="function")):
    await avatars.remove(conn, principal.user_id)
    return Response(status_code=204)


@router.get("/v1/users/{user_id}/avatar")
async def get_avatar(user_id: str, _principal: deps.Principal = Depends(deps.get_current_user),
                     conn=Depends(deps.get_conn, scope="function")):
    """Anyone signed in may see a person's picture (as in a member list)."""
    found = await avatars.load(conn, user_id) if _is_uuid(user_id) else None
    if found is None:
        raise refusal(404, "not_found", "No picture")
    content_type, data, _ = found
    return Response(content=data, media_type=content_type, headers={
        # The URL carries a version (?v=), so a long cache is safe.
        "Cache-Control": "private, max-age=86400",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "Content-Disposition": "inline",
    })


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


# ---------- Signed-in browsers ----------

def _current(request: Request) -> str | None:
    raw = request.cookies.get(deps.COOKIE_NAME)
    return sessions.session_id(raw) if raw else None


@router.get("/v1/auth/sessions")
async def list_my_sessions(request: Request,
                           principal: deps.Principal = Depends(deps.require_full_credential),
                           conn=Depends(deps.get_conn, scope="function")):
    """`current` marks this browser's own session."""
    cfg = load_auth_config(os.environ)
    mine = _current(request)
    rows = await sessions.list_sessions(conn, principal.user_id, idle_hours=cfg.session_idle_hours)
    return [{**r, "current": r["id"] == mine} for r in rows]


@router.delete("/v1/auth/sessions/{sid}", status_code=204)
async def end_my_session(sid: str, principal: deps.Principal = Depends(deps.require_full_credential),
                         conn=Depends(deps.get_conn, scope="function")):
    if not await sessions.end_session(conn, principal.user_id, sid):
        raise refusal(404, "not_found", "That browser is already signed out.")
    return Response(status_code=204)


@router.post("/v1/auth/sessions/sign-out-others")
async def end_my_other_sessions(request: Request,
                                principal: deps.Principal = Depends(deps.require_full_credential),
                                conn=Depends(deps.get_conn, scope="function")):
    """Every browser but this one. From an access token (no browser of its
    own), that is every browser."""
    n = await sessions.end_other_sessions(conn, principal.user_id, _current(request))
    return {"signed_out": n}
