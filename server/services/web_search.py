"""
Web search backend (C2.1 spec §5.4, §5.8): SearXNG results, and the text of one page.

web_search() only searches: it returns title, url, site and snippet, with no page fetch and no model call. The
chat model picks the results worth reading, and read_web_pages (chat/tools/read_web_pages.py) fetches those with
fetch_page_text(), cuts each page to its passages that matter (page_passages()) and reads them through the
reader sub-agent (chat/reader.py).

Mirrors services/embeddings.py: a module-level httpx.AsyncClient with start_client()/stop_client() wired into
the app lifecycle, a Semaphore bounding the parallel page fetches, and env-driven config.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import socket
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://localhost:18043").rstrip("/")
RESULT_COUNT = int(os.environ.get("WEB_SEARCH_RESULT_COUNT", "5"))
RESULT_COUNT_CAP = 10
MAX_CONCURRENCY = int(os.environ.get("WEB_SEARCH_MAX_CONCURRENCY", "4"))
FETCH_TIMEOUT_S = float(os.environ.get("WEB_SEARCH_FETCH_TIMEOUT_S", "8"))
MAX_RESPONSE_BYTES = int(os.environ.get("WEB_SEARCH_MAX_RESPONSE_BYTES", "2000000"))
FETCH_TOTAL_S = float(os.environ.get("WEB_SEARCH_FETCH_TOTAL_S", "15"))

_UA = "Mozilla/5.0 (compatible; NaturalReaderBot/1.0)"


def _is_ip_public(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str)
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


def is_url_fetchable(url: str) -> bool:
    """SSRF guard: True only for http/https whose host resolves entirely to
    public IPs. Rejects loopback/private/link-local/reserved/multicast and any
    non-http(s) scheme. DNS is resolved so a public hostname pointing at a
    private IP is still blocked."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip_str = info[4][0]
        try:
            if not _is_ip_public(ip_str):
                return False
        except ValueError:
            return False
    return True


_client: httpx.AsyncClient | None = None
_semaphore = asyncio.Semaphore(MAX_CONCURRENCY)


async def start_client() -> None:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(headers={"User-Agent": _UA})


async def stop_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _get_client() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError("Web-search HTTP client not started — call start_client() first")
    return _client


async def searxng_search(query: str, count: int) -> list[dict]:
    """Query SearXNG's JSON API and return up to `count` {title,url,snippet}."""
    client = _get_client()
    resp = await client.get(
        f"{SEARXNG_URL}/search",
        params={"q": query, "format": "json"},
        timeout=FETCH_TIMEOUT_S,
    )
    resp.raise_for_status()
    data = resp.json()
    out: list[dict] = []
    for r in (data.get("results") or [])[:count]:
        url = r.get("url") or ""
        if not url:
            continue
        out.append({
            "title": r.get("title") or url,
            "url": url,
            "snippet": r.get("content") or "",
        })
    return out


def _looks_like_text(content_type: str) -> bool:
    """True for text/* and application/xhtml+xml, or when Content-Type is
    absent (some servers omit it — we can't pre-filter, so let it through)."""
    if not content_type:
        return True
    media_type = content_type.split(";", 1)[0].strip().lower()
    return media_type.startswith("text/") or media_type == "application/xhtml+xml"


async def _fetch_capped(url: str) -> tuple[bytes, str] | None:
    """Walk redirects (SSRF-checked at every hop, same as before) and stream
    the final hop, stopping as soon as MAX_RESPONSE_BYTES is reached instead
    of buffering the whole body first. A non-text Content-Type on the final
    hop returns None before any of the body is read."""
    client = _get_client()
    current = url
    for _ in range(4):  # cap redirect hops
        if not is_url_fetchable(current):
            logger.warning("Blocked non-public URL: %s", current)
            return None
        try:
            async with client.stream(
                "GET", current, timeout=FETCH_TIMEOUT_S, follow_redirects=False,
            ) as resp:
                if resp.is_redirect:
                    loc = resp.headers.get("location")
                    if not loc:
                        return None
                    current = str(httpx.URL(current).join(loc))
                    continue
                if not resp.is_success:
                    logger.warning("Non-2xx for %s: %s", current, resp.status_code)
                    return None
                if not _looks_like_text(resp.headers.get("content-type", "")):
                    return None
                content = bytearray()
                async for chunk in resp.aiter_bytes():
                    content.extend(chunk)
                    if len(content) >= MAX_RESPONSE_BYTES:
                        break
                return bytes(content[:MAX_RESPONSE_BYTES]), resp.encoding
        except httpx.HTTPError as e:
            logger.warning("Fetch failed for %s: %s", current, e)
            return None
    return None


async def _safe_get(url: str) -> tuple[bytes, str] | None:
    """`_fetch_capped`, wrapped in one deadline for the whole walk (redirects
    plus the final streamed body) — WEB_SEARCH_FETCH_TOTAL_S — so a body that
    trickles in slowly enough to dodge the per-request timeout still can't
    hold a fetch open indefinitely."""
    try:
        return await asyncio.wait_for(_fetch_capped(url), timeout=FETCH_TOTAL_S)
    except TimeoutError:
        logger.warning("Fetch exceeded %.0fs deadline for %s", FETCH_TOTAL_S, url)
        return None


async def fetch_page_text(url: str) -> str | None:
    """A page's readable text, whole (no character cut), or None when it is blocked, can't be fetched or has no
    text. At most WEB_SEARCH_MAX_CONCURRENCY fetches run at once, whoever asks."""
    async with _semaphore:
        fetched = await _safe_get(url)
    if fetched is None:
        return None
    raw, encoding = fetched
    html = raw.decode(encoding or "utf-8", errors="ignore")
    import trafilatura
    # favor_recall keeps short pages from being dropped by trafilatura's
    # precision heuristic — we'd rather read a thin page than lose it.
    return (trafilatura.extract(html, favor_recall=True) or "").strip() or None


# ---- the part of a page worth reading --------------------------------------------------------------------

PASSAGE_CHARS = 1000        # about: the size a page is cut into before the best are picked
PASSAGE_JOIN = "\n\n"       # what passages are joined with, and so what each join costs in a budget
_PARAGRAPHS = re.compile(r"\n\s*\n")
# A zero-width cut after a sentence end that whitespace follows, or after a CJK full stop (no space is written).
_SENTENCE_ENDS = re.compile(r"(?<=[.!?])(?=\s)|(?<=[\u3002\uff01\uff1f])")
_WORDS = re.compile(r"\w+")


def _pieces(text: str, limit: int) -> list[str]:
    """`text` in pieces of at most `limit` characters, cut at a space in the window's second half when there is
    one, else anywhere."""
    out: list[str] = []
    while len(text) > limit:
        cut = next((i for i in range(limit, limit // 2, -1) if text[i].isspace()), limit)
        out.append(text[:cut].strip())
        text = text[cut:].lstrip()
    out.append(text)
    return [p for p in out if p]


def _split(text: str, limit: int) -> list[str]:
    """Paragraphs (blank-line breaks); a paragraph over `limit` is packed from its sentences, each passage as
    many whole sentences as fit; a sentence over `limit` is cut by `_pieces`."""
    out: list[str] = []
    for para in _PARAGRAPHS.split(text):
        para = para.strip()
        if len(para) <= limit:
            if para:
                out.append(para)
            continue
        current = ""
        for sentence in _SENTENCE_ENDS.split(para):
            if len(sentence.strip()) > limit:
                if current.strip():
                    out.append(current.strip())
                current = ""
                out.extend(_pieces(sentence.strip(), limit))
            elif len(current) + len(sentence) > limit:
                if current.strip():
                    out.append(current.strip())
                current = sentence
            else:
                current += sentence
        if current.strip():
            out.append(current.strip())
    return out


def page_passages(text: str, query: str, need: str, max_chars: int) -> list[str]:
    """The passages of a page that best serve `need` (and `query`, the search that found it), in page order, in
    at most `max_chars` characters counting the joins between them.

    The page is cut into passages of about PASSAGE_CHARS (paragraphs, then sentences, then anywhere), each is
    scored by how many distinct words (`\\w+`, case-folded) it shares with need + query, and the best are kept
    while they fit, ties going to the earlier one. A page that shares no word with them is taken from the top."""
    passages = _split(text, max(1, min(PASSAGE_CHARS, max_chars)))
    wanted = set(_WORDS.findall(f"{need} {query}".casefold()))
    scores = [len(wanted & set(_WORDS.findall(p.casefold()))) for p in passages]
    chosen: list[int] = []
    used = 0
    for i in sorted(range(len(passages)), key=lambda i: (-scores[i], i)):
        cost = len(passages[i]) + (len(PASSAGE_JOIN) if chosen else 0)
        if used + cost <= max_chars:
            chosen.append(i)
            used += cost
    return [passages[i] for i in sorted(chosen)]


def site_of(url: str) -> str:
    """The URL's hostname, lowercased, without a leading "www."; "" when it has none."""
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return ""
    return host.removeprefix("www.")


async def web_search(query: str, count: int) -> dict:
    """Search and list what was found; nothing is fetched and no model is called. Values are SearXNG's own, not
    yet made safe to show a model (the chat tool does that)."""
    hits = await searxng_search(query, max(1, min(RESULT_COUNT_CAP, count)))
    return {"query": query, "results": [
        {"title": h["title"], "url": h["url"], "site": site_of(h["url"]), "snippet": h.get("snippet") or ""}
        for h in hits]}
