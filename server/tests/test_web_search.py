import socket

import httpx
import pytest
import respx
from server.services import web_search as ws


@pytest.fixture
def resolve_pub_test(monkeypatch):
    """Make the mock host `pub.test` resolve to a public IP so the SSRF guard
    lets respx-served requests through. Real IP literals (e.g. 169.254.169.254)
    still pass to the real resolver, so the guard is genuinely exercised."""
    real = socket.getaddrinfo

    def fake(host, *args, **kwargs):
        if host == "pub.test":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        return real(host, *args, **kwargs)

    monkeypatch.setattr(ws.socket, "getaddrinfo", fake)


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


@respx.mock
async def test_fetch_and_extract_returns_clean_text(resolve_pub_test):
    html = (
        "<html><head><title>T</title></head><body><nav>home about</nav>"
        "<article><h1>Findings</h1>"
        "<p>The core measured value in this study is 42 across all trials. "
        "The experiment was repeated ten times with consistent results. "
        "Researchers concluded the effect is stable and reproducible.</p>"
        "</article></body></html>"
    )
    respx.get("http://pub.test/a").mock(return_value=httpx.Response(200, html=html))
    await ws.start_client()
    try:
        text = await ws.fetch_and_extract("http://pub.test/a")
    finally:
        await ws.stop_client()
    assert text is not None
    assert "42" in text
    assert "home about" not in text  # nav/boilerplate stripped


@respx.mock
async def test_fetch_and_extract_truncates_to_max_page_chars(monkeypatch, resolve_pub_test):
    monkeypatch.setattr(ws, "MAX_PAGE_CHARS", 50)
    para = "This sentence provides ample readable content for extraction. " * 40
    html = f"<html><body><article><h1>Doc</h1><p>{para}</p></article></body></html>"
    respx.get("http://pub.test/big").mock(return_value=httpx.Response(200, html=html))
    await ws.start_client()
    try:
        text = await ws.fetch_and_extract("http://pub.test/big")
    finally:
        await ws.stop_client()
    assert text is not None
    assert len(text) == 50


@respx.mock
async def test_fetch_and_extract_blocks_redirect_to_private(resolve_pub_test):
    respx.get("http://pub.test/redir").mock(
        return_value=httpx.Response(302, headers={"location": "http://169.254.169.254/"})
    )
    await ws.start_client()
    try:
        text = await ws.fetch_and_extract("http://pub.test/redir")
    finally:
        await ws.stop_client()
    assert text is None


async def test_fetch_and_extract_blocks_private_url_without_request():
    # No respx route registered — if it tried to fetch, respx would raise.
    await ws.start_client()
    try:
        text = await ws.fetch_and_extract("http://127.0.0.1/secret")
    finally:
        await ws.stop_client()
    assert text is None


@respx.mock
async def test_summarize_one_calls_ollama_generate(monkeypatch):
    monkeypatch.setattr(ws, "OLLAMA_URL", "http://ollama.test")
    route = respx.post("http://ollama.test/api/generate").mock(
        return_value=httpx.Response(200, json={"response": "  A tidy summary.  "})
    )
    await ws.start_client()
    try:
        out = await ws.summarize_one("what is x", "long page text about x")
    finally:
        await ws.stop_client()
    assert route.called
    body = route.calls.last.request.content.decode()
    assert '"stream": false' in body or '"stream":false' in body
    assert out.strip() == "A tidy summary."


async def test_web_search_fans_out_with_snippet_fallback(monkeypatch):
    async def fake_search(query, count):
        return [
            {"title": "T1", "url": "http://one.test", "snippet": "snip1"},
            {"title": "T2", "url": "http://two.test", "snippet": "snip2"},
        ]

    async def fake_fetch(url):
        return "page text" if url == "http://one.test" else None

    async def fake_summarize(query, text):
        return "SUMMARY"

    monkeypatch.setattr(ws, "searxng_search", fake_search)
    monkeypatch.setattr(ws, "fetch_and_extract", fake_fetch)
    monkeypatch.setattr(ws, "summarize_one", fake_summarize)

    out = await ws.web_search("q", 5)
    assert out["query"] == "q"
    assert len(out["results"]) == 2
    first, second = out["results"]
    assert first == {"title": "T1", "url": "http://one.test",
                     "summary": "SUMMARY", "source": "page"}
    assert second == {"title": "T2", "url": "http://two.test",
                      "summary": "snip2", "source": "snippet"}
