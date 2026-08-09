import httpx
import pytest
import respx
from server.services import web_search as ws


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/x",
    "http://10.0.0.5/x",
    "http://172.16.0.1/x",
    "http://192.168.1.1/",
    "http://169.254.169.254/latest/meta-data",  # cloud metadata endpoint
    "http://[::1]/x",
    "ftp://example.com/x",                        # bad scheme
    "notaurl",
])
def test_ssrf_guard_blocks_private_loopback_and_bad_scheme(url):
    assert ws.is_url_fetchable(url) is False


@pytest.mark.parametrize("url", [
    "http://93.184.216.34/",
    "https://93.184.216.34/some/path",
])
def test_ssrf_guard_allows_public_ip(url):
    assert ws.is_url_fetchable(url) is True


@respx.mock
async def test_searxng_search_parses_and_caps(monkeypatch):
    monkeypatch.setattr(ws, "SEARXNG_URL", "http://searx.test")
    route = respx.get("http://searx.test/search").mock(
        return_value=httpx.Response(200, json={"results": [
            {"title": "A", "url": "http://a.test", "content": "sa"},
            {"title": "B", "url": "http://b.test", "content": "sb"},
            {"title": "C", "url": "http://c.test", "content": "sc"},
        ]})
    )
    await ws.start_client()
    try:
        out = await ws.searxng_search("q", 2)
    finally:
        await ws.stop_client()
    assert route.called
    assert [r["url"] for r in out] == ["http://a.test", "http://b.test"]
    assert out[0] == {"title": "A", "url": "http://a.test", "snippet": "sa"}
