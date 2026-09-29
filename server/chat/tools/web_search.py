"""web_search: SearXNG + page summaries (spec §5.3). Moved from
src/lib/chatTools/webSearch.js; the service's SSRF guards are unchanged."""
from __future__ import annotations

import re
from typing import Any

from ...llm.types import ToolSpec
from ...services import web_search as web_search_service
from ...services.web_search import RESULT_COUNT, web_search

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


_SPEC = ToolSpec(
    name="web_search",
    description=(
        "Searches the live internet for real-time information, recent news, current prices, and "
        "up-to-date facts. Use this tool whenever a user asks about events after your knowledge "
        "cutoff, requests current data/statistics, or asks a factual question requiring live "
        "information or verification. Not for questions about the document the user has open."),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string",
                      "description": "The optimized search query string used to look up information on the web."},
            "count": {"type": "integer", "minimum": 1, "maximum": 10, "description": "Number of results."},
        },
        "required": ["query"],
    },
)


class _WebSearch:
    name = "web_search"
    spec = _SPEC

    def available(self, ctx) -> bool:
        return bool(web_search_service.SEARXNG_URL)

    def guidance(self, ctx) -> str:
        line = ("search the internet for current or outside information (news, prices, facts you "
                "don't know). Search by topic.")
        if ctx is not None and ctx.doc is not None and ctx.doc.state == "indexed":
            # The walk: a small model sent "Zephyr station" (a name from the
            # open document) to the web. No tool is named here: the document
            # search may not be offered.
            line += (" Never copy text from the document into a query. For names or terms you "
                     "don't recognise, search the document first: they are probably from it.")
        return line

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
        return await web_search(query, count)

    def summarize(self, args: dict[str, Any], result: dict[str, Any], ctx) -> dict[str, Any]:
        n = len(result.get("results") or [])
        return {"ok": True, "chunk_count": None, "query": result.get("query"),
                "summary_text": f'Web search for "{result.get("query")}" returned {n} result(s).'}


TOOL = _WebSearch()
