import httpx
from fastapi import Depends, FastAPI
from httpx import ASGITransport

from server.auth import deps
from server.auth.tokens import create_token
from server.auth.users import resolve_or_provision_user, set_status


def _app(db_conn):
    app = FastAPI()

    async def _conn_override():
        yield db_conn

    app.dependency_overrides[deps.get_conn] = _conn_override

    @app.get("/whoami")
    async def whoami(p: deps.Principal = Depends(deps.get_current_user)):
        return {"id": p.user_id, "role": p.role}

    @app.get("/admin-only")
    async def admin_only(p: deps.Principal = Depends(deps.require_admin)):
        return {"ok": True}

    return app


async def _get(app, path, headers=None):
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.get(path, headers=headers or {})


async def test_no_credential_is_401(db_conn):
    r = await _get(_app(db_conn), "/whoami")
    assert r.status_code == 401


async def test_pat_authenticates_admin(db_conn):
    u = await resolve_or_provision_user(db_conn, iss="i", sub="s", email="a@x.io")
    _, raw = await create_token(db_conn, u["id"], "cli")
    r = await _get(_app(db_conn), "/whoami", {"Authorization": f"Bearer {raw}"})
    assert r.status_code == 200 and r.json()["role"] == "admin"


async def test_pending_user_is_403_with_structured_status(db_conn):
    await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")
    u2 = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    _, raw = await create_token(db_conn, u2["id"], "cli")
    r = await _get(_app(db_conn), "/whoami", {"Authorization": f"Bearer {raw}"})
    assert r.status_code == 403
    assert r.json()["detail"]["status"] == "pending"


async def test_disabled_user_403_status_is_disabled(db_conn):
    await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")
    u2 = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    await set_status(db_conn, u2["id"], "active")
    await set_status(db_conn, u2["id"], "disabled")
    _, raw = await create_token(db_conn, u2["id"], "cli")
    r = await _get(_app(db_conn), "/whoami", {"Authorization": f"Bearer {raw}"})
    assert r.status_code == 403
    assert r.json()["detail"]["status"] == "disabled"


async def test_require_admin_blocks_member(db_conn):
    await resolve_or_provision_user(db_conn, iss="i", sub="s1", email="a@x.io")
    u2 = await resolve_or_provision_user(db_conn, iss="i", sub="s2", email="b@x.io")
    await set_status(db_conn, u2["id"], "active")
    _, raw = await create_token(db_conn, u2["id"], "cli")
    r = await _get(_app(db_conn), "/admin-only", {"Authorization": f"Bearer {raw}"})
    assert r.status_code == 403


def _cap_app(principal):
    app = FastAPI()
    app.dependency_overrides[deps.get_current_user] = lambda: principal

    @app.get("/needs-chat")
    async def needs_chat(p: deps.Principal = Depends(deps.require_capability("chat"))):
        return {"ok": p.email}

    return app


async def test_require_capability_allows_holder():
    p = deps.Principal(user_id="u", email="e@x.io", role="member",
                       capabilities=frozenset({"chat"}))
    async with httpx.AsyncClient(transport=ASGITransport(app=_cap_app(p)),
                                 base_url="http://t") as c:
        assert (await c.get("/needs-chat")).status_code == 200


async def test_require_capability_403s_without():
    p = deps.Principal(user_id="u", email="e@x.io", role="member",
                       capabilities=frozenset({"reader"}))
    async with httpx.AsyncClient(transport=ASGITransport(app=_cap_app(p)),
                                 base_url="http://t") as c:
        r = await c.get("/needs-chat")
        assert r.status_code == 403
        assert r.json()["detail"] == {"error": "missing_capability", "capability": "chat"}


async def test_an_idle_session_cookie_is_401(db_conn, monkeypatch):
    from server.auth.sessions import create_session
    monkeypatch.setenv("SESSION_IDLE_HOURS", "1")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    u = await resolve_or_provision_user(db_conn, iss="i", sub="s", email="a@x.io")
    tok = await create_session(db_conn, u["id"])
    await db_conn.execute("UPDATE sessions SET last_seen_at = now() - interval '2 hours'")
    r = await _get(_app(db_conn), "/whoami", {"Cookie": f"{deps.COOKIE_NAME}={tok}"})
    assert r.status_code == 401


# ---- The sign-in bypass is for this machine only, whatever sits in front of the backend ----

import pytest

from server.auth.config import bypass_request_is_local


@pytest.mark.parametrize("headers", [
    {"host": "localhost:5173"}, {"host": "127.0.0.1:8000"}, {"host": "[::1]:8000"}, {"host": "LOCALHOST"},
])
def test_a_request_addressed_to_this_machine_may_use_the_bypass(headers):
    assert bypass_request_is_local(headers) is True


@pytest.mark.parametrize("headers", [
    {"host": "chat.example.com"},                                  # a reverse proxy passing the public name
    {"host": "attacker.example:8000"},                             # DNS rebinding from a page in the browser
    {"host": "192.168.1.20:5173"},                                 # another machine on the network
    {"host": "localhost.evil.example"}, {"host": "127.0.0.1.evil.example"},
    {"host": "localhost", "x-forwarded-for": "203.0.113.9"},       # a proxy that rewrote Host
    {"host": "127.0.0.1:8000", "x-real-ip": "203.0.113.9"},
    {"host": "127.0.0.1:8000", "forwarded": "for=203.0.113.9"},
    {"host": "127.0.0.1:8000", "x-forwarded-host": "chat.example.com"},
    {},
])
def test_a_request_that_came_through_a_proxy_or_for_another_name_may_not(headers):
    assert bypass_request_is_local(headers) is False


async def _bypass_get(app, headers):
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost:8000") as c:
        return await c.get("/whoami", headers=headers)


async def test_with_sign_in_off_a_local_request_is_the_seed_admin(db_conn, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("HOST", "127.0.0.1")
    r = await _bypass_get(_app(db_conn), {})
    assert r.status_code == 200 and r.json()["role"] == "admin"


async def test_with_sign_in_off_a_proxied_request_is_refused_not_made_admin(db_conn, monkeypatch):
    # `./startup.sh` with no arguments turns sign-in off. Behind nginx the bind is still loopback, so the bind
    # check alone made every visitor the seed admin.
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("HOST", "127.0.0.1")
    for headers in ({"host": "chat.example.com", "x-forwarded-for": "203.0.113.9"}, {"host": "attacker.example"}):
        r = await _bypass_get(_app(db_conn), headers)
        assert r.status_code == 403 and r.json()["detail"]["error"] == "sign_in_off_local_only", headers
