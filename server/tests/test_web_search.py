import asyncio
import socket
import time

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


def _mock_transport_client(handler) -> httpx.AsyncClient:
    """An AsyncClient wired straight to a MockTransport handler, for tests that
    need to control the response body's timing/shape (endless/slow streams)
    beyond what respx's canned responses can do."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_fetch_and_extract_streams_and_stops_at_the_cap(monkeypatch, resolve_pub_test):
    """An endless body must not be buffered in full — the fix streams the
    final hop and stops once MAX_RESPONSE_BYTES is reached. Pre-fix, this
    calls resp.content on a fully-buffered response, which never returns for
    a body that never ends: bounded by wait_for so the test fails fast
    instead of hanging."""
    monkeypatch.setattr(ws, "MAX_RESPONSE_BYTES", 2000)
    chunks_yielded = 0

    async def endless_body():
        nonlocal chunks_yielded
        chunk = b"<p>" + b"filler content " * 20 + b"</p>"
        while True:
            chunks_yielded += 1
            await asyncio.sleep(0)  # real checkpoint: lets wait_for's cancel land
            yield chunk

    async def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html"}, content=endless_body())

    client = _mock_transport_client(handler)
    monkeypatch.setattr(ws, "_client", client)
    try:
        text = await asyncio.wait_for(ws.fetch_and_extract("http://pub.test/endless"), timeout=2)
    finally:
        await client.aclose()
    assert text is not None
    assert "filler content" in text
    # ~2000 bytes / ~324-byte chunk: capped reading takes single-digit chunks,
    # not the thousands an uncapped read of an endless body would rack up.
    assert chunks_yielded < 20


async def test_fetch_and_extract_body_trickling_past_deadline_returns_none(monkeypatch, resolve_pub_test):
    """A body that trickles in slowly — each chunk arriving well inside the
    per-request timeout — must still be cut off by the total fetch deadline
    (WEB_SEARCH_FETCH_TOTAL_S), not allowed to run indefinitely."""
    monkeypatch.setattr(ws, "FETCH_TOTAL_S", 0.3)

    async def trickle_body():
        for _ in range(50):  # 50 * 0.05s = 2.5s total if never cut off
            await asyncio.sleep(0.05)
            yield b"data chunk "

    async def handler(request):
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=trickle_body())

    client = _mock_transport_client(handler)
    monkeypatch.setattr(ws, "_client", client)
    try:
        start = time.monotonic()
        text = await asyncio.wait_for(ws.fetch_and_extract("http://pub.test/trickle"), timeout=2)
        elapsed = time.monotonic() - start
    finally:
        await client.aclose()
    assert text is None
    assert elapsed < 1.0  # deadline (0.3s) plus a generous margin


async def test_fetch_and_extract_skips_non_text_content_type_without_reading_body(monkeypatch, resolve_pub_test):
    """A non-text Content-Type (e.g. application/pdf) must short-circuit
    before the body is read at all — not just before it's decoded."""
    body_was_read = False

    async def pdf_body():
        nonlocal body_was_read
        body_was_read = True
        yield b"%PDF-1.4 ...binary..."

    async def handler(request):
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=pdf_body())

    client = _mock_transport_client(handler)
    monkeypatch.setattr(ws, "_client", client)
    try:
        text = await ws.fetch_and_extract("http://pub.test/doc.pdf")
    finally:
        await client.aclose()
    assert text is None
    assert body_was_read is False


async def test_fetch_and_extract_still_extracts_normal_html_via_mock_transport(monkeypatch, resolve_pub_test):
    """Sanity check that the streamed final hop still produces normal output
    for an ordinary, fully-buffered HTML page."""
    html = (
        "<html><head><title>T</title></head><body><nav>home about</nav>"
        "<article><h1>Findings</h1>"
        "<p>The core measured value in this study is 42 across all trials. "
        "The experiment was repeated ten times with consistent results. "
        "Researchers concluded the effect is stable and reproducible.</p>"
        "</article></body></html>"
    )

    async def handler(request):
        return httpx.Response(
            200, headers={"content-type": "text/html; charset=utf-8"}, content=html.encode(),
        )

    client = _mock_transport_client(handler)
    monkeypatch.setattr(ws, "_client", client)
    try:
        text = await ws.fetch_and_extract("http://pub.test/normal")
    finally:
        await client.aclose()
    assert text is not None
    assert "42" in text
    assert "home about" not in text


async def test_summarize_one_goes_through_the_router(monkeypatch):
    from server.llm.types import CallSettings

    calls = {}

    class _FakeRouter:
        async def complete(self, model_id, messages, settings=CallSettings()):
            calls["model"] = model_id
            calls["prompt"] = messages[0].content
            return "  A short summary.  "

    monkeypatch.setenv("SUMMARIZE_MODEL", "llama3.2:3b")
    monkeypatch.setattr(ws, "get_router", lambda: _FakeRouter())
    out = await ws.summarize_one("what is x", "long page text about x")
    assert out == "A short summary."
    assert calls["model"] == "llama3.2:3b"            # bare name: the router sends it to the first ollama provider
    assert "what is x" in calls["prompt"] and "long page text about x" in calls["prompt"]


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
