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

logger = logging.getLogger(__name__)

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://localhost:8080").rstrip("/")
SUMMARY_MODEL = os.environ.get("WEB_SEARCH_SUMMARY_MODEL", "llama3.2:3b")
RESULT_COUNT = int(os.environ.get("WEB_SEARCH_RESULT_COUNT", "5"))
RESULT_COUNT_CAP = 10
MAX_CONCURRENCY = int(os.environ.get("WEB_SEARCH_MAX_CONCURRENCY", "4"))
FETCH_TIMEOUT_S = float(os.environ.get("WEB_SEARCH_FETCH_TIMEOUT_S", "8"))
SUMMARY_TIMEOUT_S = float(os.environ.get("WEB_SEARCH_SUMMARY_TIMEOUT_S", "30"))
MAX_PAGE_CHARS = int(os.environ.get("WEB_SEARCH_MAX_PAGE_CHARS", "6000"))
MAX_RESPONSE_BYTES = int(os.environ.get("WEB_SEARCH_MAX_RESPONSE_BYTES", "2000000"))

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
