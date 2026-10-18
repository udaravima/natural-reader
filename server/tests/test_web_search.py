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
async def test_fetch_page_text_returns_clean_text(resolve_pub_test):
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
        text = await ws.fetch_page_text("http://pub.test/a")
    finally:
        await ws.stop_client()
    assert text is not None
    assert "42" in text
    assert "home about" not in text  # nav/boilerplate stripped


@respx.mock
async def test_fetch_page_text_returns_the_whole_page_with_no_character_cut(resolve_pub_test):
    # 40 paragraphs, 7,000+ characters: the old fetch cut at 6,000 (WEB_SEARCH_MAX_PAGE_CHARS).
    paras = "".join(f"<p>Paragraph number {i} gives ample readable content for extraction here.</p>"
                    for i in range(40))
    tail = "<p>The very last paragraph says the answer is forty-two.</p>"
    html = f"<html><body><article><h1>Doc</h1>{paras * 3}{tail}</article></body></html>"
    respx.get("http://pub.test/big").mock(return_value=httpx.Response(200, html=html))
    await ws.start_client()
    try:
        text = await ws.fetch_page_text("http://pub.test/big")
    finally:
        await ws.stop_client()
    assert text is not None
    assert len(text) > 6000
    assert "the answer is forty-two" in text
    assert not hasattr(ws, "MAX_PAGE_CHARS")


@respx.mock
async def test_fetch_page_text_blocks_redirect_to_private(resolve_pub_test):
    respx.get("http://pub.test/redir").mock(
        return_value=httpx.Response(302, headers={"location": "http://169.254.169.254/"})
    )
    await ws.start_client()
    try:
        text = await ws.fetch_page_text("http://pub.test/redir")
    finally:
        await ws.stop_client()
    assert text is None


async def test_fetch_page_text_blocks_private_url_without_request():
    # No respx route registered — if it tried to fetch, respx would raise.
    await ws.start_client()
    try:
        text = await ws.fetch_page_text("http://127.0.0.1/secret")
    finally:
        await ws.stop_client()
    assert text is None


def _mock_transport_client(handler) -> httpx.AsyncClient:
    """An AsyncClient wired straight to a MockTransport handler, for tests that
    need to control the response body's timing/shape (endless/slow streams)
    beyond what respx's canned responses can do."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_fetch_page_text_streams_and_stops_at_the_cap(monkeypatch, resolve_pub_test):
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
        text = await asyncio.wait_for(ws.fetch_page_text("http://pub.test/endless"), timeout=2)
    finally:
        await client.aclose()
    assert text is not None
    assert "filler content" in text
    # ~2000 bytes / ~324-byte chunk: capped reading takes single-digit chunks,
    # not the thousands an uncapped read of an endless body would rack up.
    assert chunks_yielded < 20


async def test_fetch_page_text_body_trickling_past_deadline_returns_none(monkeypatch, resolve_pub_test):
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
        text = await asyncio.wait_for(ws.fetch_page_text("http://pub.test/trickle"), timeout=2)
        elapsed = time.monotonic() - start
    finally:
        await client.aclose()
    assert text is None
    assert elapsed < 1.0  # deadline (0.3s) plus a generous margin


async def test_fetch_page_text_skips_non_text_content_type_without_reading_body(monkeypatch, resolve_pub_test):
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
        text = await ws.fetch_page_text("http://pub.test/doc.pdf")
    finally:
        await client.aclose()
    assert text is None
    assert body_was_read is False


async def test_fetch_page_text_still_extracts_normal_html_via_mock_transport(monkeypatch, resolve_pub_test):
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
        text = await ws.fetch_page_text("http://pub.test/normal")
    finally:
        await client.aclose()
    assert text is not None
    assert "42" in text
    assert "home about" not in text


async def test_page_fetches_share_the_services_concurrency_bound(monkeypatch):
    """fetch_page_text is what read_web_pages fans out over, so the bound lives in it, not in its callers."""
    monkeypatch.setattr(ws, "_semaphore", asyncio.Semaphore(2))
    running, peak = 0, 0

    async def slow_get(url):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.02)
        running -= 1
        return None

    monkeypatch.setattr(ws, "_safe_get", slow_get)
    results = await asyncio.gather(*(ws.fetch_page_text(f"http://pub.test/{i}") for i in range(6)))
    assert results == [None] * 6 and peak == 2


def test_the_summary_machinery_is_gone():
    for name in ("summarize_one", "_SUMMARY_PROMPT", "MAX_PAGE_CHARS", "SUMMARY_TIMEOUT_S", "fetch_and_extract"):
        assert not hasattr(ws, name), name


def _no_fetch_no_model(monkeypatch):
    """Anything that fetches a page or calls a model fails the test. Patched where a call would land, whichever
    way the service reaches it: the HTTP client every page fetch uses; the router, both as a module attribute
    (an import made at call time) and as a name the service imported at the top (the old summary code did, so
    it is patched here if it is there again); and the router's two ways to a model."""
    from server.llm import router as llm_router

    async def no_fetch(*a, **k):
        raise AssertionError("web_search must not fetch a page")

    def no_client():
        raise AssertionError("web_search must not use the HTTP client for a page")

    def no_model(*a, **k):
        raise AssertionError("web_search must not call a model")

    monkeypatch.setattr(ws, "fetch_page_text", no_fetch)
    monkeypatch.setattr(ws, "_safe_get", no_fetch)
    monkeypatch.setattr(ws, "_fetch_capped", no_fetch)
    monkeypatch.setattr(ws, "_get_client", no_client)
    monkeypatch.setattr(llm_router, "get_router", no_model)
    monkeypatch.setattr(ws, "get_router", no_model, raising=False)
    monkeypatch.setattr(llm_router.Router, "complete", no_model)
    monkeypatch.setattr(llm_router.Router, "stream_chat", no_model)


async def test_web_search_makes_no_page_fetch_and_no_model_call(monkeypatch):
    async def fake_search(query, count):
        return [
            {"title": "T1", "url": "http://one.test/a", "snippet": "snip1"},
            {"title": "T2", "url": "https://WWW.Two.Test/b", "snippet": "snip2"},
        ]

    monkeypatch.setattr(ws, "searxng_search", fake_search)
    _no_fetch_no_model(monkeypatch)

    out = await ws.web_search("q", 5)
    assert out == {"query": "q", "results": [
        {"title": "T1", "url": "http://one.test/a", "site": "one.test", "snippet": "snip1"},
        {"title": "T2", "url": "https://WWW.Two.Test/b", "site": "two.test", "snippet": "snip2"},
    ]}


@pytest.mark.parametrize("url,site", [
    ("https://www.example.com/a/b?x=1", "example.com"),
    ("https://WWW.Example.COM/", "example.com"),
    ("http://sub.example.org:8080/x", "sub.example.org"),
    ("https://wwwx.example.net/", "wwwx.example.net"),    # only a leading "www." goes
    ("https://docs.www.example.net/", "docs.www.example.net"),
    ("notaurl", ""),
    ("", ""),
])
def test_site_is_the_lowercased_hostname_without_a_leading_www(url, site):
    assert ws.site_of(url) == site


async def test_web_search_clamps_the_count(monkeypatch):
    asked = []

    async def fake_search(query, count):
        asked.append(count)
        return []

    monkeypatch.setattr(ws, "searxng_search", fake_search)
    assert await ws.web_search("q", 99) == {"query": "q", "results": []}
    await ws.web_search("q", 0)
    assert asked == [10, 1]


# ---- page_passages: the part of a page worth reading ----
#
# trafilatura.extract puts one paragraph, heading or list item per line, separated by a single "\n" and never a
# blank line, so these pages do too.

def _line(head: str, size: int = 600) -> str:
    """One line of exactly `size` characters that starts with `head`. Two never fit one passage (the packing
    limit is 1,000), so each is a passage of its own."""
    return (head + " " + "filler " * 200)[:size - 1] + "."


def _para(i: int) -> str:
    return f"Section {i} of the manual. " + "Plain filler sentence about nothing in particular. " * 12


def test_page_passages_find_text_past_six_thousand_characters():
    """The old fetch kept the first 6,000 characters, so an answer on page two of a long article was never
    seen. The passages are chosen from the whole page."""
    answer = "To change the listening port, edit server.toml and set port = 8080 under [network]."
    page = "\n".join([_para(i) for i in range(30)] + [answer] + [_para(i) for i in range(30, 40)])
    assert page.index(answer) > 6000
    picked = ws.page_passages(page, "change listening port", "the config setting for the port", 3000)
    assert sum(answer in p for p in picked) == 1             # whole, in one passage
    assert sum(len(p) for p in picked) + 2 * (len(picked) - 1) <= 3000


def test_page_passages_come_back_in_page_order():
    lines = [_line("intro words"), _line("alpha beta gamma"), _line("nothing here"), _line("beta gamma delta epsilon")]
    page = "\n".join(lines)
    need = "alpha beta gamma delta epsilon"
    assert ws.page_passages(page, "", need, 10_000) == lines
    assert ws.page_passages(page, "", need, 1300) == [lines[1], lines[3]]      # the best two, in page order


def test_page_passages_score_distinct_casefolded_words_of_need_and_query():
    lines = [_line("Port PORT port"), _line("install the Port"), _line("unrelated")]
    # Room for one. "port" three times counts once (1 point); "install" + "port" count twice, and the query's
    # words are added to the need's.
    assert ws.page_passages("\n".join(lines), "install", "port", 650) == [lines[1]]


def test_page_passages_break_ties_by_earlier_position():
    lines = [_line("one needle here"), _line("two needle here"), _line("six needle here")]
    page = "\n".join(lines)
    assert ws.page_passages(page, "needle", "", 600) == [lines[0]]
    assert ws.page_passages(page, "needle", "", 1202) == lines[:2]
    assert ws.page_passages(page, "needle", "", 1804) == lines


def test_page_passages_take_the_top_of_the_page_when_nothing_matches():
    lines = [_line("first block"), _line("second block"), _line("third block"), _line("fourth block")]
    page = "\n".join(lines)
    assert ws.page_passages(page, "zzz", "yyy", 1300) == lines[:2]
    assert ws.page_passages(page, "", "", 10_000) == lines


def test_the_top_of_the_page_fallback_stops_at_the_first_passage_that_does_not_fit():
    """No gaps: a short passage further down must not jump the one that didn't fit. (When passages do match,
    a smaller one may fill the room a bigger one left: that is ranking, not reading from the top.)"""
    big, wide, short = _line("first", 600), _line("second", 900), _line("third", 100)
    page = "\n".join([big, wide, short])
    assert ws.page_passages(page, "zzz", "yyy", 1300) == [big]
    a, b, c = _line("alpha beta", 600), _line("alpha", 900), _line("beta", 100)
    assert ws.page_passages("\n".join([a, b, c]), "", "alpha beta", 1300) == [a, c]


def test_page_passages_count_the_joins_in_the_budget():
    page = "\n".join([_line("aaaa needle"), _line("bbbb needle")])    # 600 + 2 + 600
    assert len(ws.page_passages(page, "needle", "", 1202)) == 2
    assert len(ws.page_passages(page, "needle", "", 1201)) == 1


def test_page_passages_pack_adjacent_lines_up_to_about_a_thousand_characters():
    lines = [f"{i:02d} " + "x" * 96 for i in range(30)]               # 99 characters each
    page = "\n".join(lines)
    picked = ws.page_passages(page, "", "", 100_000)
    assert [p.count("\n") + 1 for p in picked] == [10, 10, 10]         # 10 * 99 + 9 = 999
    assert all(len(p) <= 1000 for p in picked)
    assert "\n".join(picked) == page                                   # nothing lost, nothing repeated, in order


def test_page_passages_ignore_blank_lines_and_surrounding_space():
    assert ws.page_passages("  one \n\n   \n two\r\n\n three  ", "", "", 1000) == ["one\ntwo\nthree"]


def test_a_passage_never_starts_or_ends_mid_line_unless_the_line_is_over_a_thousand():
    short = [f"Row {i}: " + "data " * (i % 40) + "end" for i in range(90)]      # 8 to 203 characters
    long_line = "A long statement about the row. " * 80                       # 2,560 characters, in sentences
    lines = short[:45] + [long_line.strip()] + short[45:]
    picked = ws.page_passages("\n".join(lines), "", "", 1_000_000)
    seen = [part for p in picked for part in p.split("\n")]
    cut = [part for part in seen if part not in short]
    assert set(short) <= set(seen)                                           # every short line, whole
    assert cut and all(part in long_line and len(part) <= 1000 for part in cut)
    assert " ".join(cut) == long_line.strip()                                # the long line, cut at sentence ends


def test_page_passages_split_a_long_line_at_sentence_ends():
    sentences = [f"Sentence number {i:02d} says something of about this length. " for i in range(40)]
    page = "".join(sentences).strip()
    assert len(page) > 2000 and "\n" not in page
    picked = ws.page_passages(page, "", "Sentence", 100_000)
    assert len(picked) >= 2
    assert all(len(p) <= 1000 for p in picked)
    assert all(p.endswith(".") for p in picked)                       # cut at sentence ends
    assert " ".join(picked) == page                                    # nothing lost, nothing repeated


def test_page_passages_split_a_long_line_with_no_sentence_end_at_spaces_then_anywhere():
    page = "word " * 600                                              # 3,000 characters, no sentence end
    picked = ws.page_passages(page, "", "word", 100_000)
    assert len(picked) >= 3 and all(0 < len(p) <= 1000 for p in picked)
    assert "".join(p.replace(" ", "") for p in picked) == "word" * 600
    unbroken = ws.page_passages("x" * 2500, "", "x", 100_000)
    assert [len(p) for p in unbroken] == [1000, 1000, 500]


def test_page_passages_never_return_a_passage_longer_than_the_budget():
    picked = ws.page_passages("y" * 900, "", "y", 500)
    assert picked and all(len(p) <= 500 for p in picked)


def test_page_passages_of_no_text_are_none():
    assert ws.page_passages("", "q", "n", 1000) == []
    assert ws.page_passages(" \n\n \n ", "q", "n", 1000) == []


async def test_a_list_page_as_trafilatura_gives_it_keeps_the_answers_row_whole(monkeypatch, resolve_pub_test):
    """The page shape that matters: trafilatura output has no blank lines, and a 120-row list is one run of
    lines. The row with the answer (97) must come back whole, inside one passage, so a quote of it verifies."""
    rows = "".join(f"<li>Item {i}: key{i} is set to value{i} (default: unset)</li>" for i in range(120))
    html = ("<html><head><title>Guide</title></head><body><nav>home about</nav><article>"
            "<h1>Configuration guide</h1>"
            "<p>This guide explains every setting of the product in detail. Read it before changing anything.</p>"
            f"<h2>Settings</h2><ul>{rows}</ul>"
            "<p>That is all there is to know about the settings, thank you for reading.</p></article></body></html>")

    async def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=html.encode())

    client = _mock_transport_client(handler)
    monkeypatch.setattr(ws, "_client", client)
    try:
        text = await ws.fetch_page_text("http://pub.test/guide")
    finally:
        await client.aclose()
    assert text is not None and "\n\n" not in text and text.count("\n") > 100     # the real shape
    lines = text.split("\n")
    row = next(line for line in lines if "key97 " in line)
    assert len(text) > 6000 and text.index(row) > 4000

    picked = ws.page_passages(text, "config", "the value that key97 is set to", 3000)
    holding = [p for p in picked if row in p]
    assert len(holding) == 1                                   # the row, whole, in one passage
    assert all(part in lines for p in picked for part in p.split("\n"))       # no row cut anywhere
    assert sum(len(p) for p in picked) + 2 * (len(picked) - 1) <= 3000
    assert picked == sorted(picked, key=text.index)             # page order
