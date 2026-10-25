"""Scoped personal access tokens. A token's scopes narrow what its owner's
account may do; they never add to it. A token with no scopes (every token made
before migration 019) keeps full access."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport

from server import endpoints
from server.auth import deps
from server.auth.tokens import create_token
from server.auth.users import resolve_or_provision_user, set_capabilities
from server.routers import auth as auth_router


def _app(db_conn):
    app = FastAPI()

    async def _conn_override():
        yield db_conn

    app.dependency_overrides[deps.get_conn] = _conn_override
    app.include_router(auth_router.router)
    app.include_router(endpoints.router)

    @app.get("/gate/read-aloud")
    async def read_aloud(_: deps.Principal = Depends(deps.require_read_aloud)):
        return {"ok": True}

    @app.get("/gate/reader")
    async def reader(_: deps.Principal = Depends(deps.require_capability("reader"))):
        return {"ok": True}

    @app.get("/gate/chat")
    async def chat(_: deps.Principal = Depends(deps.require_capability("chat"))):
        return {"ok": True}

    @app.get("/gate/admin")
    async def admin(_: deps.Principal = Depends(deps.require_admin)):
        return {"ok": True}

    return app


async def _call(db_conn, method, path, raw, **kw):
    transport = ASGITransport(app=_app(db_conn))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.request(method, path, headers={"Authorization": f"Bearer {raw}"}, **kw)


async def _gates(db_conn, raw):
    out = {}
    for gate in ("read-aloud", "reader", "chat", "admin"):
        out[gate] = (await _call(db_conn, "GET", f"/gate/{gate}", raw)).status_code
    return out


@pytest.fixture
async def admin_user(db_conn):
    # The first user provisioned is the bootstrap admin: admin + chat + reader.
    return await resolve_or_provision_user(db_conn, iss="i", sub="admin", email="admin@x.io")


@pytest.fixture
async def member(db_conn, admin_user):
    u = await resolve_or_provision_user(db_conn, iss="i", sub="m", email="m@x.io")
    await db_conn.execute("UPDATE users SET status='active' WHERE id=%s", (u["id"],))
    await set_capabilities(db_conn, u["id"], ["reader", "chat"])
    return u


async def test_a_token_without_scopes_keeps_full_access(db_conn, admin_user):
    _, raw = await create_token(db_conn, admin_user["id"], "old")
    assert await _gates(db_conn, raw) == {"read-aloud": 200, "reader": 200, "chat": 200, "admin": 200}


async def test_read_aloud_scope_opens_only_read_aloud(db_conn, admin_user):
    _, raw = await create_token(db_conn, admin_user["id"], "ext", scopes=["read_aloud"])
    assert await _gates(db_conn, raw) == {"read-aloud": 200, "reader": 403, "chat": 403, "admin": 403}


async def test_documents_scope_includes_read_aloud(db_conn, admin_user):
    _, raw = await create_token(db_conn, admin_user["id"], "docs", scopes=["documents"])
    assert await _gates(db_conn, raw) == {"read-aloud": 200, "reader": 200, "chat": 403, "admin": 403}


async def test_chat_scope_opens_only_chat(db_conn, admin_user):
    _, raw = await create_token(db_conn, admin_user["id"], "bot", scopes=["chat"])
    assert await _gates(db_conn, raw) == {"read-aloud": 403, "reader": 403, "chat": 200, "admin": 403}


async def test_admin_scope_opens_only_admin(db_conn, admin_user):
    _, raw = await create_token(db_conn, admin_user["id"], "ops", scopes=["admin"])
    assert await _gates(db_conn, raw) == {"read-aloud": 403, "reader": 403, "chat": 403, "admin": 200}


async def test_scopes_never_add_to_the_account(db_conn, member):
    # Stored directly: the create route would refuse a scope the owner lacks.
    _, raw = await create_token(db_conn, member["id"], "x", scopes=["admin", "read_aloud"])
    gates = await _gates(db_conn, raw)
    assert gates["admin"] == 403 and gates["read-aloud"] == 200


async def test_losing_reader_takes_read_aloud_from_the_token(db_conn, member):
    _, raw = await create_token(db_conn, member["id"], "ext", scopes=["read_aloud"])
    await set_capabilities(db_conn, member["id"], ["chat"])
    assert (await _gates(db_conn, raw))["read-aloud"] == 403


async def test_tts_routes_use_the_read_aloud_gate(db_conn, admin_user):
    _, chat_only = await create_token(db_conn, admin_user["id"], "bot", scopes=["chat"])
    _, ext = await create_token(db_conn, admin_user["id"], "ext", scopes=["read_aloud"])
    body = {"sentences": ["Hi."], "voice": "af_heart", "speed": 1}
    assert (await _call(db_conn, "POST", "/v1/batch_synthesize", chat_only, json=body)).status_code == 403
    assert (await _call(db_conn, "POST", "/v1/synthesize", chat_only,
                        json={"text": "Hi.", "voice": "af_heart", "speed": 1})).status_code == 403
    # Past the gate: whatever Kokoro answers here, it is not an auth refusal.
    assert (await _call(db_conn, "POST", "/v1/batch_synthesize", ext, json=body)).status_code not in (401, 403)


async def test_a_scoped_token_cannot_manage_tokens(db_conn, admin_user):
    tid, raw = await create_token(db_conn, admin_user["id"], "ext", scopes=["read_aloud", "documents", "chat", "admin"])
    for method, path, kw in [("POST", "/v1/auth/tokens", {"json": {"name": "again"}}),
                             ("GET", "/v1/auth/tokens", {}),
                             ("DELETE", f"/v1/auth/tokens/{tid}", {})]:
        r = await _call(db_conn, method, path, raw, **kw)
        assert r.status_code == 403, (method, path)
        assert r.json()["detail"]["error"] == "token_scope"


async def test_me_reports_scopes_and_read_aloud(db_conn, admin_user):
    _, ext = await create_token(db_conn, admin_user["id"], "ext", scopes=["read_aloud"])
    me = (await _call(db_conn, "GET", "/v1/auth/me", ext)).json()
    assert me["scopes"] == ["read_aloud"] and me["read_aloud"] is True and me["capabilities"] == []
    assert me["role"] == "member"
    _, old = await create_token(db_conn, admin_user["id"], "old")
    me = (await _call(db_conn, "GET", "/v1/auth/me", old)).json()
    assert me["scopes"] is None and me["read_aloud"] is True and "admin" in me["capabilities"]


# ---------- creating scoped tokens (as a signed-in session) ----------

def _session_app(db_conn, user):
    app = FastAPI()

    async def _conn_override():
        yield db_conn

    caps = frozenset(user["capabilities"])
    principal = deps.Principal(user_id=user["id"], email=user["email"],
                               role="admin" if "admin" in caps else "member", capabilities=caps)
    app.dependency_overrides[deps.get_conn] = _conn_override
    app.dependency_overrides[deps.get_current_user] = lambda: principal
    app.include_router(auth_router.router)
    return app


async def _session(db_conn, user, method, path, **kw):
    transport = ASGITransport(app=_session_app(db_conn, user))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.request(method, path, **kw)


async def test_create_with_scopes_and_expiry_lists_both(db_conn, admin_user):
    when = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    r = await _session(db_conn, admin_user, "POST", "/v1/auth/tokens",
                       json={"name": "ext", "scopes": ["read_aloud"], "expires_at": when})
    assert r.status_code == 200
    listed = (await _session(db_conn, admin_user, "GET", "/v1/auth/tokens")).json()
    assert listed[0]["scopes"] == ["read_aloud"] and listed[0]["expires_at"] is not None


async def test_create_without_scopes_stays_full_access(db_conn, admin_user):
    r = await _session(db_conn, admin_user, "POST", "/v1/auth/tokens", json={"name": "script"})
    assert r.status_code == 200
    assert (await _session(db_conn, admin_user, "GET", "/v1/auth/tokens")).json()[0]["scopes"] is None


@pytest.mark.parametrize("scopes", [[], ["nope"], ["read_aloud", "read_aloud"]])
async def test_create_rejects_bad_scopes(db_conn, admin_user, scopes):
    r = await _session(db_conn, admin_user, "POST", "/v1/auth/tokens", json={"name": "x", "scopes": scopes})
    assert r.status_code == 422


async def test_create_rejects_a_scope_the_account_lacks(db_conn, member):
    r = await _session(db_conn, member, "POST", "/v1/auth/tokens", json={"name": "x", "scopes": ["admin"]})
    assert r.status_code == 422
    assert "admin" in str(r.json()["detail"])


async def test_create_rejects_an_expiry_in_the_past(db_conn, admin_user):
    when = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    r = await _session(db_conn, admin_user, "POST", "/v1/auth/tokens", json={"name": "x", "expires_at": when})
    assert r.status_code == 422


async def test_an_expired_scoped_token_is_refused(db_conn, admin_user):
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    _, raw = await create_token(db_conn, admin_user["id"], "ext", scopes=["read_aloud"], expires_at=past)
    assert (await _call(db_conn, "GET", "/gate/read-aloud", raw)).status_code == 401


def test_the_documents_scope_lets_the_docling_role_through_and_read_aloud_does_not():
    from server.auth.scopes import narrow
    account = frozenset({"reader", "docling", "chat"})
    assert narrow(account, frozenset({"documents"})) == {"reader", "docling"}
    assert narrow(account, frozenset({"read_aloud"})) == frozenset()
    assert narrow(frozenset({"reader", "docling"}), frozenset({"read_aloud"})) == frozenset()
    assert narrow(account, frozenset({"chat"})) == {"chat"}
    assert narrow(account, None) == account
