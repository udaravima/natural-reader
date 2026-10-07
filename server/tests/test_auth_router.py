import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.routers import auth as auth_router


def _app(db_conn, principal=None):
    app = FastAPI()

    async def _conn_override():
        yield db_conn

    app.dependency_overrides[deps.get_conn] = _conn_override
    if principal is not None:
        app.dependency_overrides[deps.get_current_user] = lambda: principal
    app.include_router(auth_router.router)
    return app


async def _get(app, path, headers=None):
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.get(path, headers=headers or {})


async def test_me_returns_principal(db_conn):
    p = deps.Principal(user_id="u1", email="a@x.io", role="admin")
    r = await _get(_app(db_conn, principal=p), "/v1/auth/me")
    assert r.status_code == 200 and r.json()["email"] == "a@x.io"


async def test_me_401_without_principal(db_conn):
    r = await _get(_app(db_conn), "/v1/auth/me")
    assert r.status_code == 401


async def test_me_returns_capabilities(db_conn):
    p = deps.Principal(user_id="u", email="e@x.io", role="member",
                       capabilities=frozenset({"reader"}))
    r = await _get(_app(db_conn, principal=p), "/v1/auth/me")
    body = r.json()
    assert body["capabilities"] == ["reader"] and body["role"] == "member"


async def test_callback_idp_error_redirects_not_500(db_conn):
    # Keycloak can bounce the callback with error=... instead of a code — an
    # expired auth flow (temporarily_unavailable / authentication_expired), a
    # cancelled login, a denied consent. That must redirect back to login, not
    # surface OAuthError as an unhandled 500.
    r = await _get(
        _app(db_conn),
        "/v1/auth/callback?error=temporarily_unavailable"
        "&error_description=authentication_expired&state=abc",
    )
    assert r.status_code == 303


async def test_logout_sends_the_id_token_hint_to_the_idp(db_conn, monkeypatch):
    # The hint is what lets Keycloak end the SSO session without a "Do you
    # want to log out?" page. It lives in the OIDC-flow session cookie, so it
    # must be read before that session is cleared.
    from urllib.parse import parse_qs, urlsplit

    from starlette.middleware.sessions import SessionMiddleware
    from starlette.requests import Request

    class _Idp:
        async def load_server_metadata(self):
            return {"end_session_endpoint": "http://idp.test/logout"}

    monkeypatch.setattr(auth_router, "_client", lambda: _Idp())
    monkeypatch.setenv("OIDC_REDIRECT_URL", "http://app.test/v1/auth/callback")
    app = _app(db_conn)

    @app.get("/seed")
    async def seed(request: Request):
        request.session["id_token"] = "the-id-token"
        return {}

    app.add_middleware(SessionMiddleware, secret_key="test-secret")
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://app.test") as client:
        await client.get("/seed")
        r = await client.get("/v1/auth/logout")

    assert r.status_code == 303
    loc = urlsplit(r.headers["location"])
    q = parse_qs(loc.query)
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == "http://idp.test/logout"
    assert q["id_token_hint"] == ["the-id-token"]
    assert "client_id" not in q
    assert q["post_logout_redirect_uri"] == ["http://app.test/"]
