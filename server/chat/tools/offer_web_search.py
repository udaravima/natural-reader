"""offer_web_search: the model asks the user's leave to search the web (C2.1 spec 5.8).

In "ask" mode (the default) this is the only way the model gets a search: web_search is never offered to it, and
read_web_pages (which reads the results of a search the user approved) only after a yes. Every search the model
wants is an offer, even after a yes, because a yes is for the one search the user saw. Calling it changes nothing
outside the server. The orchestrator ends the turn
with an offer card (the reason, the query in an editable field, "Search the web" / "Not now"), and a yes
comes back as the next turn's `context.web`, on which the app runs that search itself.

The query is what would leave the server, so it gets the same guard as a search the model runs: text copied from
a document is refused (the model reads the error and rewrites the query as a topic). The user sees the query
and may edit it before approving, which is the second guard.
"""
from __future__ import annotations

from typing import Any

from ...llm.types import ToolSpec
from ...services import web_search as web_search_service
from .web_search import COPIED, QUERY_MAX_CHARS, TOO_LONG, copies_document

REASON_MAX_CHARS = 300
SUMMARY_TEXT = "Offered to the user; they decide."

_SPEC = ToolSpec(
    name="offer_web_search",
    # Names no other tool (test_no_tool_description_names_another_tool): routing between tools is the rules'.
    description=(
        "Ask the user whether to search the web. You can't search it yourself: they decide, and their answer "
        "comes with their next message. Use it when the answer needs current or outside facts you don't have "
        "(news, prices, versions, a system the documents don't mention). Calling it ends your turn, so write "
        "first what you can say without the web, and don't repeat it afterwards. The query is the topic in a few "
        "words, never text copied from a document; the user reads and may edit it."),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": QUERY_MAX_CHARS,
                      "description": 'The search, in a few words, e.g. "RHEL 9 python 3.13 packages".'},
            "reason": {"type": "string", "minLength": 1, "maxLength": REASON_MAX_CHARS,
                       "description": "Why the web is needed, in one short sentence the user reads."},
        },
        "required": ["query", "reason"],
    },
)


class _OfferWebSearch:
    name = "offer_web_search"
    spec = _SPEC
    source = "web"
    reads_documents = False   # nothing here is document text
    unmetered = True          # its result (about 150 characters) must always reach the turn: see TurnTools

    def available(self, ctx) -> bool:
        # Also after a yes (ctx.web_approved): that yes was for one search, and a different one needs its own offer.
        return bool(web_search_service.SEARXNG_URL) and ctx.web_mode == "ask"

    async def execute(self, args: dict[str, Any], ctx) -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        reason = str(args.get("reason") or "").strip()
        if not query:
            return {"error": "query is required and must be non-empty."}
        if len(query) > QUERY_MAX_CHARS:
            return {"error": TOO_LONG}
        if not reason:
            return {"error": "reason is required: one short sentence for the user."}
        if len(reason) > REASON_MAX_CHARS:
            return {"error": f"reason is too long ({len(reason)} characters): at most {REASON_MAX_CHARS}."}
        if copies_document(query, ctx.seen_text):
            return {"error": COPIED}
        return {"offered": True, "query": query, "reason": reason}

    def summarize(self, args: dict[str, Any], result: dict[str, Any], ctx) -> dict[str, Any]:
        return {"ok": True, "chunk_count": None, "query": result.get("query"), "summary_text": SUMMARY_TEXT}


TOOL = _OfferWebSearch()
