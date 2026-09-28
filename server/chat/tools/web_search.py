"""web_search: SearXNG + page summaries (spec §5.3). Moved from
src/lib/chatTools/webSearch.js; the service's SSRF guards are unchanged."""
from __future__ import annotations

from typing import Any

from ...llm.types import ToolSpec
from ...services import web_search as web_search_service
from ...services.web_search import RESULT_COUNT, web_search

_SPEC = ToolSpec(
    name="web_search",
    description=(
        "Searches the live internet for real-time information, recent news, current prices, and "
        "up-to-date facts. Use this tool whenever a user asks about events after your knowledge "
        "cutoff, requests current data/statistics, or asks a factual question requiring live "
        "information or verification. Not for questions about the document the user has open: "
        "use search_document for those."),
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

    async def execute(self, args: dict[str, Any], ctx) -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        if not query:
            return {"error": "query is required and must be non-empty."}
        count = args.get("count", RESULT_COUNT)
        if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 10:
            return {"error": "count must be between 1 and 10."}
        return await web_search(query, count)

    def summarize(self, args: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        n = len(result.get("results") or [])
        return {"ok": True, "chunk_count": None, "query": result.get("query"),
                "summary_text": f'Web search for "{result.get("query")}" returned {n} result(s).'}


TOOL = _WebSearch()
