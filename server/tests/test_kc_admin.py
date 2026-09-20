import time
import httpx
import pytest
from server.auth.kc_admin import KeycloakAdmin

pytestmark = pytest.mark.asyncio
ISSUER = "http://kc.test/realms/nr"


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_token_is_fetched_and_cached():
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/realms/nr/protocol/openid-connect/token"
        assert b"grant_type=client_credentials" in req.content
        calls["n"] += 1
        return httpx.Response(200, json={"access_token": "T", "expires_in": 300})

    kc = KeycloakAdmin(ISSUER, "svc", "sec", http=_client(handler))
    assert await kc._token() == "T"
    assert await kc._token() == "T"        # second call served from cache
    assert calls["n"] == 1


async def test_token_error_raises_kcadminerror():
    from server.auth.kc_admin import KCAdminError

    def handler(req):
        return httpx.Response(401, json={"error": "invalid_client"})

    kc = KeycloakAdmin(ISSUER, "svc", "bad", http=_client(handler))
    with pytest.raises(KCAdminError):
        await kc._token()


def _authed(handler):
    def wrapped(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/openid-connect/token"):
            return httpx.Response(200, json={"access_token": "T", "expires_in": 300})
        return handler(req)
    return httpx.AsyncClient(transport=httpx.MockTransport(wrapped))


async def test_create_user_returns_sub_from_location():
    def handler(req):
        assert req.method == "POST" and req.url.path.endswith("/users")
        return httpx.Response(201, headers={
            "Location": "http://kc.test/admin/realms/nr/users/abc-123"})
    kc = KeycloakAdmin(ISSUER, "svc", "sec", http=_authed(handler))
    assert await kc.create_user(email="b@x.io", email_verified=True) == "abc-123"


async def test_find_user_by_email_exact():
    def handler(req):
        assert req.url.params.get("email") == "b@x.io"
        assert req.url.params.get("exact") == "true"
        return httpx.Response(200, json=[{"id": "u9", "email": "b@x.io"}])
    kc = KeycloakAdmin(ISSUER, "svc", "sec", http=_authed(handler))
    assert await kc.find_user_by_email("b@x.io") == "u9"


async def test_find_user_by_email_none():
    kc = KeycloakAdmin(ISSUER, "svc", "sec",
                       http=_authed(lambda req: httpx.Response(200, json=[])))
    assert await kc.find_user_by_email("nobody@x.io") is None


async def test_assign_realm_roles_looks_up_then_posts():
    seen = {}
    def handler(req):
        if req.url.path.endswith("/roles/chat"):
            return httpx.Response(200, json={"id": "r-chat", "name": "chat"})
        if req.url.path.endswith("/roles/reader"):
            return httpx.Response(200, json={"id": "r-read", "name": "reader"})
        if req.method == "POST" and "role-mappings/realm" in req.url.path:
            seen["body"] = req.content
            return httpx.Response(204)
        raise AssertionError(req.url.path)
    kc = KeycloakAdmin(ISSUER, "svc", "sec", http=_authed(handler))
    await kc.assign_realm_roles("u1", ["chat", "reader"])
    assert b"r-chat" in seen["body"] and b"r-read" in seen["body"]


async def test_realm_smtp_configured():
    def on(req): return httpx.Response(200, json={"smtpServer": {"host": "smtp.x"}})
    def off(req): return httpx.Response(200, json={"smtpServer": {}})
    assert await KeycloakAdmin(ISSUER, "s", "s", http=_authed(on)).realm_smtp_configured() is True
    assert await KeycloakAdmin(ISSUER, "s", "s", http=_authed(off)).realm_smtp_configured() is False
