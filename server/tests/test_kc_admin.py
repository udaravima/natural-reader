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
