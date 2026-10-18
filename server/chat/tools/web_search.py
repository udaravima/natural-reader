"""web_search: SearXNG results, numbered across the turn (spec §5.3, C2.1 §5.8). Moved from
src/lib/chatTools/webSearch.js; the service's SSRF guards are unchanged.

It returns title, address, site and a short snippet for each result, and reads no page: the model picks the
results worth reading and read_web_pages reads them. A result's number is how the model names it later, so it
is numbered across all the turn's searches and a URL found twice keeps its first number. Titles and snippets
are someone else's text: they reach the model as one inert line and as fenced text.
"""
from __future__ import annotations

import re
from typing import Any

from ...llm.types import ToolSpec
from ...services import web_search as web_search_service
from ...services.web_search import RESULT_COUNT, web_search
from ..prompt import display_name, fence

# Review focus 4: document text never leaves the server through a web query.
QUERY_MAX_CHARS = 300
COPIED_RUN_WORDS = 8     # a run this long shared with document text is a copy, not a topic
# Scripts written without spaces (Chinese, Japanese kana, Thai, Lao, Myanmar,
# Khmer) have no words to count: there, a shared run of this many characters.
COPIED_RUN_CHARS = 12
_NO_SPACES = ("\u0e00-\u0eff\u1000-\u109f\u1780-\u17ff\u3040-\u30ff"
              "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff")
COPIED = ("That query copies text from the document or an earlier answer. Search the web with "
          "the topic in a few words, not text from the document.")
TOO_LONG = (f"That query is over {QUERY_MAX_CHARS} characters. Search the web with the topic in a "
            "few words, not text from the document.")
SNIPPET_MAX_CHARS = 300
_WORD = re.compile(rf"[^\W{_NO_SPACES}]+")
_UNSPACED = re.compile(rf"[{_NO_SPACES}]+")


def _runs(text: str) -> set:
    """Every run of COPIED_RUN_WORDS words, and of COPIED_RUN_CHARS characters
    of unspaced script, case and punctuation ignored."""
    text = text.lower()
    words = _WORD.findall(text)
    runs: set = {tuple(words[i:i + COPIED_RUN_WORDS]) for i in range(len(words) - COPIED_RUN_WORDS + 1)}
    chars = "".join(_UNSPACED.findall(text))
    runs |= {chars[i:i + COPIED_RUN_CHARS] for i in range(len(chars) - COPIED_RUN_CHARS + 1)}
    return runs


def copies_document(query: str, texts: list[str]) -> bool:
    runs = _runs(query)
    return bool(runs) and any(runs & _runs(t) for t in texts)


def web_tools_available(ctx) -> bool:
    """web_search and read_web_pages are offered when the engine is set up and the user's yes is not still owed:
    the deployment searches without asking ("auto"), or the user approved a search this turn. "off" never.
    The enforcement of "ask first" is this: the tools simply aren't there (offer_web_search is, instead).
    With no turn at all (`ctx` None: the evals ask whether web search is set up here) only the engine counts."""
    if ctx is None:
        return bool(web_search_service.SEARXNG_URL)
    return (bool(web_search_service.SEARXNG_URL) and ctx.web_mode != "off"
            and (ctx.web_mode == "auto" or ctx.web_approved))


_SPEC = ToolSpec(
    name="web_search",
    # v2.4: the old text invited it for "a factual question requiring ...
    # verification" — "What is the project's codename?" is one, and a 3B model
    # sent it to the web every time (ledger 2026-10-05).
    description=(
        "Search the internet for current or outside information: news, prices, weather, recent events, "
        "facts that can't be in the user's document. Not for the open document's contents: a name or "
        "term you don't recognise is probably from the document, so search the document first. The "
        "query is the topic in a few words, never text copied from the document. It lists each result's "
        "title, address and a short snippet; the snippets are not the pages."),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string",
                      "description": 'The topic in a few words, e.g. "Tromsø weather today".'},
            "count": {"type": "integer", "minimum": 1, "maximum": 10, "description": "Number of results."},
        },
        "required": ["query"],
    },
)


def _snippet(text: Any) -> str:
    """A snippet as the model reads it: fenced, and at most SNIPPET_MAX_CHARS characters."""
    text = fence(str(text or "")).strip()
    return text if len(text) <= SNIPPET_MAX_CHARS else text[:SNIPPET_MAX_CHARS - 1].rstrip() + "…"


def _numbered(found: dict[str, Any], query: str, web_results: dict[int, dict]) -> dict[str, Any]:
    """`found` (the service's result) with each result numbered and made inert. A new URL takes the next number
    after the turn's highest and is recorded in `web_results`; one the turn has seen keeps its number, and is
    listed once however often the engine returns it."""
    number_of = {entry["url"]: n for n, entry in web_results.items()}
    listed: dict[int, dict[str, Any]] = {}
    for r in found.get("results") or []:
        url = r.get("url") or ""
        if not url:
            continue
        n = number_of.get(url)
        if n is None:
            n = number_of[url] = max(web_results, default=0) + 1
            web_results[n] = {"title": r.get("title") or url, "url": url, "site": r.get("site") or "",
                              "query": query, "read": False}
        if n not in listed:
            listed[n] = {"n": n, "title": display_name(web_results[n]["title"]), "url": url,
                         "site": web_results[n]["site"], "snippet": _snippet(r.get("snippet"))}
    return {**found, "results": list(listed.values())}


class _WebSearch:
    name = "web_search"
    spec = _SPEC
    source = "web"

    def available(self, ctx) -> bool:
        return web_tools_available(ctx)

    async def execute(self, args: dict[str, Any], ctx) -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        if not query:
            return {"error": "query is required and must be non-empty."}
        if len(query) > QUERY_MAX_CHARS:
            return {"error": TOO_LONG}
        if ctx is not None and copies_document(query, ctx.seen_text):
            return {"error": COPIED}
        count = args.get("count", RESULT_COUNT)
        if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 10:
            return {"error": "count must be between 1 and 10."}
        found = await web_search(query, count)
        return _numbered(found, query, ctx.web_results if ctx is not None else {})

    def summarize(self, args: dict[str, Any], result: dict[str, Any], ctx) -> dict[str, Any]:
        n = len(result.get("results") or [])
        return {"ok": True, "chunk_count": None, "query": result.get("query"),
                "summary_text": f'Web search for "{result.get("query")}" returned {n} result(s).'}


TOOL = _WebSearch()
