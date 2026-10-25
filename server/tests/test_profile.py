"""Settings → Profile: a profile picture, /me's profile fields, and the list
of signed-in browsers with sign-out."""
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps, sessions
from server.auth.users import resolve_or_provision_user
from server.routers import auth as auth_router
from server.routers import profile as profile_router

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 64


def _app(db_conn, principal):
    app = FastAPI()

    async def _conn():
        yield db_conn
    app.dependency_overrides[deps.get_conn] = _conn
    app.dependency_overrides[deps.get_current_user] = lambda: principal
    app.include_router(auth_router.router)
    app.include_router(profile_router.router)
    return app


async def _req(app, method, path, cookies=None, **kw):
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                                 cookies=cookies or {}) as c:
        return await c.request(method, path, **kw)


async def _user(conn, sub="me", **names):
    u = await resolve_or_provision_user(conn, iss="i", sub=sub, email=f"{sub}@x.io", **names)
    return deps.Principal(user_id=str(u["id"]), email=u["email"], role="member",
                          capabilities=frozenset({"reader"}))


# ---------- picture ----------

@pytest.mark.parametrize("body,kind", [(PNG, "image/png"), (JPEG, "image/jpeg"), (WEBP, "image/webp")])
async def test_a_picture_uploads_and_is_served_back_as_its_own_type(db_conn, body, kind):
    me = await _user(db_conn)
    app = _app(db_conn, me)
    r = await _req(app, "PUT", "/v1/auth/me/avatar", content=body)
    assert r.status_code == 200
    url = r.json()["avatar_url"]
    assert url.startswith(f"/v1/users/{me.user_id}/avatar?v=")
    got = await _req(app, "GET", url)
    assert got.status_code == 200 and got.content == body
    assert got.headers["content-type"] == kind
    assert got.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in got.headers["content-security-policy"]


@pytest.mark.parametrize("body", [
    b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
    b"<html><script>alert(1)</script></html>",
    b"GIF89a" + b"\x00" * 32,
    b"",
])
async def test_anything_but_png_jpeg_or_webp_is_refused(db_conn, body):
    r = await _req(_app(db_conn, await _user(db_conn)), "PUT", "/v1/auth/me/avatar", content=body)
    assert r.status_code == 415


async def test_a_picture_over_256_kb_is_refused(db_conn):
    r = await _req(_app(db_conn, await _user(db_conn)), "PUT", "/v1/auth/me/avatar",
                   content=PNG + b"\x00" * (256 * 1024))
    assert r.status_code == 413


async def test_removing_the_picture_and_no_picture_is_a_404(db_conn):
    me = await _user(db_conn)
    app = _app(db_conn, me)
    await _req(app, "PUT", "/v1/auth/me/avatar", content=PNG)
    assert (await _req(app, "DELETE", "/v1/auth/me/avatar")).status_code == 204
    assert (await _req(app, "GET", f"/v1/users/{me.user_id}/avatar")).status_code == 404
    assert (await _req(app, "GET", "/v1/users/not-a-uuid/avatar")).status_code == 404


async def test_someone_else_sees_my_picture(db_conn):
    me = await _user(db_conn, "me")
    other = await _user(db_conn, "other")
    await _req(_app(db_conn, me), "PUT", "/v1/auth/me/avatar", content=PNG)
    r = await _req(_app(db_conn, other), "GET", f"/v1/users/{me.user_id}/avatar")
    assert r.status_code == 200


async def test_a_scoped_token_cant_change_the_picture(db_conn):
    me = await _user(db_conn)
    scoped = deps.Principal(user_id=me.user_id, email=me.email, role="member",
                            capabilities=frozenset({"reader"}), scopes=frozenset({"read_aloud"}))
    r = await _req(_app(db_conn, scoped), "PUT", "/v1/auth/me/avatar", content=PNG)
    assert r.status_code == 403


# ---------- /me ----------

async def test_me_carries_name_picture_and_account_page(db_conn, monkeypatch):
    me = await _user(db_conn, first_name="Asha", last_name="Perera")
    monkeypatch.setenv("OIDC_ISSUER", "https://auth.example.com/realms/reader")
    monkeypatch.delenv("OIDC_ACCOUNT_URL", raising=False)
    app = _app(db_conn, me)
    body = (await _req(app, "GET", "/v1/auth/me")).json()
    assert body["name"] == "Asha Perera" and body["avatar_url"] is None
    assert body["account_url"] == "https://auth.example.com/realms/reader/account"
    await _req(app, "PUT", "/v1/auth/me/avatar", content=PNG)
    assert (await _req(app, "GET", "/v1/auth/me")).json()["avatar_url"].startswith("/v1/users/")


@pytest.mark.parametrize("env,expected", [
    ({"OIDC_ISSUER": "https://auth.example.com/realms/r/"}, "https://auth.example.com/realms/r/account"),
    ({"OIDC_ISSUER": "https://accounts.example.com"}, None),       # not Keycloak: no known page
    ({"OIDC_ISSUER": "https://accounts.example.com", "OIDC_ACCOUNT_URL": "https://id.example.com/me"},
     "https://id.example.com/me"),
    ({"OIDC_ISSUER": "https://auth.example.com/realms/r", "OIDC_ACCOUNT_URL": "off"}, None),
    ({}, None),
])
def test_account_url(env, expected):
    assert auth_router.account_url(env) == expected


# ---------- signed-in browsers ----------

async def test_browsers_list_marks_this_one_and_signs_out_the_others(db_conn):
    me = await _user(db_conn)
    here = await sessions.create_session(db_conn, me.user_id, user_agent="Mozilla/5.0 (X11; Linux x86_64) Chrome/140")
    await sessions.create_session(db_conn, me.user_id, user_agent="Mozilla/5.0 (iPhone) Safari/605")
    theirs = await _user(db_conn, "other")
    await sessions.create_session(db_conn, theirs.user_id)
    app = _app(db_conn, me)
    jar = {deps.COOKIE_NAME: here}
    listed = (await _req(app, "GET", "/v1/auth/sessions", cookies=jar)).json()
    assert len(listed) == 2
    assert [s["current"] for s in listed].count(True) == 1
    assert {s["user_agent"] for s in listed} >= {"Mozilla/5.0 (iPhone) Safari/605"}
    r = await _req(app, "POST", "/v1/auth/sessions/sign-out-others", cookies=jar)
    assert r.json() == {"signed_out": 1}
    left = (await _req(app, "GET", "/v1/auth/sessions", cookies=jar)).json()
    assert [s["current"] for s in left] == [True]
    assert await sessions.resolve_session(db_conn, here) is not None
    # Someone else's browser is untouched.
    assert len(await sessions.list_sessions(db_conn, theirs.user_id)) == 1


async def test_signing_out_one_browser_only_works_on_my_own(db_conn):
    me = await _user(db_conn)
    other = await _user(db_conn, "other")
    mine = sessions.session_id(await sessions.create_session(db_conn, me.user_id))
    theirs = sessions.session_id(await sessions.create_session(db_conn, other.user_id))
    app = _app(db_conn, me)
    assert (await _req(app, "DELETE", f"/v1/auth/sessions/{theirs}")).status_code == 404
    assert (await _req(app, "DELETE", f"/v1/auth/sessions/{mine}")).status_code == 204
    assert (await _req(app, "DELETE", f"/v1/auth/sessions/{mine}")).status_code == 404
    assert len(await sessions.list_sessions(db_conn, other.user_id)) == 1


async def test_an_idle_browser_is_not_listed(db_conn, monkeypatch):
    me = await _user(db_conn)
    raw = await sessions.create_session(db_conn, me.user_id)
    await db_conn.execute("UPDATE sessions SET last_seen_at = now() - interval '13 hours' WHERE id = %s",
                          (sessions.session_id(raw),))
    monkeypatch.setenv("SESSION_IDLE_HOURS", "12")
    assert (await _req(_app(db_conn, me), "GET", "/v1/auth/sessions")).json() == []
