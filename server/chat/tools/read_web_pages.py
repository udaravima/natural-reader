"""read_web_pages: read the pages the model picked from a web search (C2.1 spec §5.4, §5.8).

A search result is a title and a snippet, which is rarely enough to answer from. This tool fetches the chosen
pages, cuts each to the passages that matter for the model's goal (`need`, and the query of the search that found
it), and hands them to the reader sub-agent (chat/reader.py). The model's context holds the findings, each with
a quote the app has checked against the page, not the pages.

Each page is one reader batch and is read on its own, so one slow or failing page costs only itself. Labels sent
to the reader are "[web n]" only: a page's title is someone else's text. A page the reader failed on comes back
as an excerpt of its best passages, fenced and short, for the model to read itself.

Web facts are not document text: they never go into ctx.seen_text (the copy guard that keeps document text off
the web) or ctx.evidence (document pages for the answer's sources). A page whose facts were returned is marked
in ctx.web_results instead, for the web sources.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Iterable

from ...llm.types import ToolSpec
from ...services.web_search import PASSAGE_JOIN, fetch_page_text, page_passages
from ..prompt import display_name, fence
from ..reader import Passage, ReaderResult, get_reader_config, read_many
from .read_document_pages import page_number
from .web_search import web_tools_available

logger = logging.getLogger(__name__)

MAX_RESULTS = 3
NEED_MAX_CHARS = 500
EXCERPT_MAX_CHARS = 1500    # the passages a failed read is replaced by
COULDNT_READ = "Couldn't read this page."
RESULTS_SHAPE = (f"results must be a list of 1-{MAX_RESULTS} result numbers from the search results, "
                 "such as [1, 3].")

_SPEC = ToolSpec(
    name="read_web_pages",
    # Names no other tool (test_no_tool_description_names_another_tool): routing between tools is the rules'.
    description=(
        "Read pages from the web search results and get back only the facts that serve one goal, each with a "
        "quote copied from the page. A separate reader takes each page, so none of its text fills your context. "
        "Use it when a result's snippet is not enough to answer: pick the 1-3 results most likely to hold the "
        "answer, by their numbers. State what you need, and name the exact things to copy (numbers, names, "
        "versions, dates). A page that can't be read comes back with an error or an excerpt to read yourself."),
    parameters={
        "type": "object",
        "properties": {
            "results": {"type": "array", "items": {"type": "integer"}, "minItems": 1, "maxItems": MAX_RESULTS,
                        "description": "Which results to read, 1-3: their numbers (n) in the search results."},
            "need": {"type": "string", "minLength": 1, "maxLength": NEED_MAX_CHARS, "description": (
                "What you need from them, in a sentence or two: the facts to find, not the user's question.")},
        },
        "required": ["results", "need"],
    },
)


class _Refused(Exception):
    """The call can't be run; the message is the error the model reads."""


def ranges(numbers: Iterable[int]) -> str:
    """Numbers as compact ranges: [1, 2, 3, 5, 7, 8] is "1-3, 5, 7-8"."""
    runs: list[list[int]] = []
    for n in sorted(set(numbers)):
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in runs)


def _need(value: Any) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        raise _Refused(f"need is required: say what to find, in 1-{NEED_MAX_CHARS} characters.")
    if len(text) > NEED_MAX_CHARS:
        raise _Refused(f"need is too long ({len(text)} characters): at most {NEED_MAX_CHARS}.")
    return text


def _numbers(value: Any, ctx) -> list[int]:
    """The result numbers asked for, each once, in the order given. Whole numbers also from "2" and 2.0 (small
    models send those), the way the document tools take refs; only results of this turn's searches."""
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_RESULTS:
        raise _Refused(RESULTS_SHAPE)
    numbers: list[int] = []
    for item in value:
        n = page_number(item)
        if n is None:
            raise _Refused(RESULTS_SHAPE)
        if n not in numbers:
            numbers.append(n)
    if any(n not in ctx.web_results for n in numbers):
        have = f"{ranges(ctx.web_results)}." if ctx.web_results else "none yet. Search first."
        raise _Refused(f"Read only results of this turn's web searches: {have}")
    return numbers


async def _text(url: str) -> str | None:
    """The page's text, or None when it can't be had: a page failing is that page's result, not the call's."""
    try:
        text = await fetch_page_text(url)
    except Exception as e:  # noqa: BLE001
        logger.warning("read_web_pages: fetching a page failed (%s)", type(e).__name__)
        return None
    return text if text and text.strip() else None


def _facts(result: ReaderResult) -> list[dict[str, str]]:
    facts: list[dict[str, str]] = []
    for f in result.facts:
        entry = {"fact": f.fact, "quote": f.quote}
        if entry not in facts:
            facts.append(entry)
    return facts


def _excerpt(text: str, entry: dict[str, Any], need: str) -> str:
    """What the model gets in place of facts when the reader failed: the page's best passages, fenced."""
    best = page_passages(text, entry["query"], need, EXCERPT_MAX_CHARS)
    return fence(PASSAGE_JOIN.join(best))[:EXCERPT_MAX_CHARS]


class _ReadWebPages:
    name = "read_web_pages"
    spec = _SPEC
    source = "web"
    reads_documents = False   # a web page is not document text: the data rule is not brought by this tool

    def available(self, ctx) -> bool:
        return web_tools_available(ctx)

    async def execute(self, args: dict[str, Any], ctx) -> dict[str, Any]:
        try:
            need = _need(args.get("need"))
            numbers = _numbers(args.get("results"), ctx)
        except _Refused as refused:
            return {"error": str(refused)}
        reader_cfg = get_reader_config()    # per call: the knobs are read from the environment

        entries = {n: ctx.web_results[n] for n in numbers}
        texts = dict(zip(numbers, await asyncio.gather(*(_text(e["url"]) for e in entries.values()))))
        batches = {n: [Passage(f"[web {n}]", p)
                       for p in page_passages(texts[n], entries[n]["query"], need, reader_cfg.input_chars)]
                   for n in numbers if texts[n]}
        results = dict(zip(batches, await read_many(ctx.question, need, list(batches.values()),
                                                    chat_model=ctx.model, cfg=reader_cfg)))

        pages: list[dict[str, Any]] = []
        for n in numbers:
            entry = entries[n]
            page: dict[str, Any] = {"n": n, "title": display_name(entry["title"]), "url": entry["url"]}
            result = results.get(n)
            if result is None:
                page["error"] = COULDNT_READ
            elif result.error:
                page["unread_excerpt"] = _excerpt(texts[n], entry, need)
            else:
                page |= {"found": result.found, "facts": _facts(result)}
                if result.found:
                    entry["read"] = True
            pages.append(page)
        return {"need": need, "pages": pages}

    def summarize(self, args: dict[str, Any], result: dict[str, Any], ctx) -> dict[str, Any]:
        read = [p for p in result["pages"] if "error" not in p and "unread_excerpt" not in p]
        sites = list(dict.fromkeys(s for p in read if (s := ctx.web_results[p["n"]].get("site"))))
        text = f"Read {len(read)} page(s)" + (f": {', '.join(sites)}" if sites else "") + "."
        if len(read) < len(result["pages"]):
            text += f" Couldn't read {len(result['pages']) - len(read)}."
        return {"ok": True, "chunk_count": None, "query": None, "summary_text": text}


TOOL = _ReadWebPages()
