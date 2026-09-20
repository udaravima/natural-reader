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
