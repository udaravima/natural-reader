"""
Web search tool backend — SearXNG search + per-page fetch/extract + small-model
summary.

Mirrors services/embeddings.py: a module-level httpx.AsyncClient with
start_client()/stop_client() wired into the app lifecycle, a Semaphore bounding
the parallel Ollama calls, and env-driven config. Unlike indexing this runs
synchronously (a tool call can't poll), so web_search() does all the work in one
request and returns per-result summaries — the chat model does final synthesis.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
from urllib.parse import urlparse

import httpx

from ..llm.router import get_router
from ..llm.types import CallSettings, Message
from . import model_router

logger = logging.getLogger(__name__)

# OLLAMA_URL / SUMMARY_MODEL live in model_router now (SUMMARIZE_MODEL env,
# falling back to the pre-gateway WEB_SEARCH_SUMMARY_MODEL).
SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://localhost:18043").rstrip("/")
RESULT_COUNT = int(os.environ.get("WEB_SEARCH_RESULT_COUNT", "5"))
RESULT_COUNT_CAP = 10
MAX_CONCURRENCY = int(os.environ.get("WEB_SEARCH_MAX_CONCURRENCY", "4"))
FETCH_TIMEOUT_S = float(os.environ.get("WEB_SEARCH_FETCH_TIMEOUT_S", "8"))
SUMMARY_TIMEOUT_S = float(os.environ.get("WEB_SEARCH_SUMMARY_TIMEOUT_S", "30"))
MAX_PAGE_CHARS = int(os.environ.get("WEB_SEARCH_MAX_PAGE_CHARS", "6000"))
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


async def fetch_and_extract(url: str) -> str | None:
    """Fetch a page and return clean, truncated text — or None on block/failure."""
    fetched = await _safe_get(url)
    if fetched is None:
        return None
    raw, encoding = fetched
    html = raw.decode(encoding or "utf-8", errors="ignore")
    import trafilatura
    # favor_recall keeps short pages from being dropped by trafilatura's
    # precision heuristic — we'd rather summarize a thin page than lose it.
    text = (trafilatura.extract(html, favor_recall=True) or "").strip()
    if not text:
        return None
    return text[:MAX_PAGE_CHARS]


_SUMMARY_PROMPT = (
    "You are summarizing a web page for someone researching: \"{query}\".\n"
    "Write 2-4 sentences capturing ONLY the information on the page relevant to "
    "that query. If the page has nothing relevant, say so in one sentence. Do "
    "not invent information that is not present.\n\nPAGE CONTENT:\n{text}"
)


async def summarize_one(query: str, text: str) -> str:
    """Summarize one page's text with the summary model, through the provider
    router (spec §4.5) — any configured provider can serve it."""
    cfg = model_router.get_config()
    prompt = _SUMMARY_PROMPT.format(query=query, text=text)
    out = await asyncio.wait_for(
        get_router().complete(cfg.summarize_model, [Message("user", prompt)], CallSettings()),
        timeout=SUMMARY_TIMEOUT_S)
    return out.strip()


async def web_search(query: str, count: int) -> dict:
    """Search, then fetch+summarize each result in parallel (semaphore-bounded).
    Per-result failures degrade to the SearXNG snippet rather than dropping —
    the caller always gets one entry per result."""
    count = max(1, min(RESULT_COUNT_CAP, count))
    hits = await searxng_search(query, count)

    async def _one(hit: dict) -> dict:
        async with _semaphore:
            summary = ""
            source = "snippet"
            text = await fetch_and_extract(hit["url"])
            if text:
                try:
                    summary = await summarize_one(query, text)
                    if summary:
                        source = "page"
                except Exception as e:  # noqa: BLE001 — degrade, don't abort the batch
                    # %r not %s: httpx timeout exceptions stringify to "", which
                    # hides the cause — repr keeps the type (e.g. ReadTimeout('')).
                    logger.warning("Summary failed for %s: %r", hit["url"], e)
            if not summary:
                summary = hit.get("snippet") or "(no summary available)"
                source = "snippet"
            return {
                "title": hit["title"],
                "url": hit["url"],
                "summary": summary.strip(),
                "source": source,
            }

    results = await asyncio.gather(*(_one(h) for h in hits))
    return {"query": query, "results": list(results)}
