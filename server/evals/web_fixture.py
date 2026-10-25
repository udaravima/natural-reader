"""A fake web for the project evaluation's web-read case (C2.1 Task 13).

`scripts/eval_project_qa.py --web-fixture` serves web search and page fetches from server/evals/fixtures/web/
instead of the network: the case then measures the chat model and the reader, not SearXNG or the internet. The
pages describe a made-up product (Marrowdeck), so the answer can only come from reading them. One page has the
answer past the 6,000th character, where the old fetch cut a page; another is an archived guide whose version
number is one digit off.

It replaces the two network calls in this process only: `searxng_search` (the search) and the page fetch
read_web_pages makes. Nothing is served over HTTP, and the product code has no switch for it: the SSRF guard
of the real fetch (services/web_search.py) is left alone, because the fetch it guards is not called.
Fixture pages go through the same extractor as a fetched page (trafilatura, favor_recall).
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Iterator

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "web"


@dataclass(frozen=True)
class FixturePage:
    title: str
    url: str
    snippet: str
    file: str


@cache
def pages() -> tuple[FixturePage, ...]:
    """The pages, in the order every search lists them."""
    index = json.loads((FIXTURE_DIR / "index.json").read_text(encoding="utf-8"))
    return tuple(FixturePage(r["title"], r["url"], r["snippet"], r["file"]) for r in index["results"])


def page_text(url: str) -> str | None:
    """The text a fetch of `url` would give: the page's HTML through the extractor. None for a url that isn't
    a fixture page (a fetch that fails)."""
    import trafilatura

    page = next((p for p in pages() if p.url == url), None)
    if page is None:
        return None
    html = (FIXTURE_DIR / page.file).read_text(encoding="utf-8")
    return (trafilatura.extract(html, favor_recall=True) or "").strip() or None


@dataclass
class Served:
    """What the evaluation asked of the fake web, in order."""
    queries: list[str] = field(default_factory=list)
    fetched: list[str] = field(default_factory=list)


@contextmanager
def served() -> Iterator[Served]:
    from ..chat.tools import read_web_pages
    from ..services import web_search

    log = Served()

    async def searxng_search(query: str, count: int) -> list[dict]:
        log.queries.append(query)
        return [{"title": p.title, "url": p.url, "snippet": p.snippet} for p in pages()[:count]]

    async def fetch_page_text(url: str) -> str | None:
        log.fetched.append(url)
        return page_text(url)

    saved = web_search.searxng_search, read_web_pages.fetch_page_text
    web_search.searxng_search, read_web_pages.fetch_page_text = searxng_search, fetch_page_text
    try:
        yield log
    finally:
        web_search.searxng_search, read_web_pages.fetch_page_text = saved
