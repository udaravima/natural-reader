"""Which pages an answer drew on (v2.4 Task D), for the source chips under a
reply. A model that finds the right facts but writes no "(page N)" still
gives the reader a way to the page; measured on 2026-10-05, a missing
citation was the largest single failure left in the eval.

Pure: the turn's evidence (every passage or page the model was given) and the
answer's text in, (pages, kind) out.
- "used": pages the answer cites that it was given, plus pages whose text
  shares a run of RUN_WORDS consecutive words with the answer;
- "retrieved": nothing matched, so every page the model was given, which the
  UI labels as searched, not as sources.

Two kinds of shared run don't count, so common wording doesn't pass for
evidence: a run with fewer than CONTENT_WORDS words outside STOPWORDS ("one
of the best"), and a run found on more than BOILERPLATE_PAGES of the pages
given (boilerplate, a running header).

A project answer (C2.1 spec section 5.7) cites "(Name, page N)", and its
evidence comes from several documents: `cited_pages` reads the citations
against the project's names, `sources_by_document` applies the same rule per
document, and `web_sources` lists the web pages a reader returned facts from.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

from .prompt import display_name

RUN_WORDS = 4
CONTENT_WORDS = 2       # words in a run that aren't STOPWORDS: "uses the ZEPHYR-9 dataset" has 3
BOILERPLATE_PAGES = 2   # a run on more of the given pages than this says nothing about any one
# The app's own citation pattern (src/lib/citations.js, server/evals/doc_qa.py).
_CITATION = re.compile(r"\(page\s+(\d+)\)|\bpage\s+(\d+)\b", re.IGNORECASE)
_WORD = re.compile(r"[\w][\w'\-.]*[\w]|\w")
STOPWORDS = frozenset("""
a about above after again all also an and any are as at be because been before being below between both but
by can could did do does doing down during each few for from further had has have having he her here hers him
his how i if in into is it its itself just me more most my no nor not now of off on once only or other our
ours out over own same she should so some such than that the their them then there these they this those
through to too under until up very was we were what when where which while who whom why will with would you
your yours one
""".split())


def _runs(text: str) -> set[tuple[str, ...]]:
    words = [w.lower() for w in _WORD.findall(text)]
    return {tuple(words[i:i + RUN_WORDS]) for i in range(len(words) - RUN_WORDS + 1)
            if sum(w not in STOPWORDS for w in words[i:i + RUN_WORDS]) >= CONTENT_WORDS}


def _distinctive(answer: str, given: Iterable[set[tuple[str, ...]]]) -> set[tuple[str, ...]]:
    """The answer's runs that appear on at least one, and on no more than BOILERPLATE_PAGES, of the pages
    given (`given`: the runs of each page)."""
    seen_on = Counter(run for runs in given for run in runs)
    return {run for run in _runs(answer) if 0 < seen_on[run] <= BOILERPLATE_PAGES}


def answer_sources(answer: str, evidence: list[tuple[int | None, str]]) -> tuple[list[int], str]:
    given: dict[int, set[tuple[str, ...]]] = {}
    for page, text in evidence:
        if page is not None:
            given.setdefault(page, set()).update(_runs(text))
    if not given:
        return [], "retrieved"
    cited = {int(a or b) for a, b in _CITATION.findall(answer)} & given.keys()
    distinctive = _distinctive(answer, given.values())
    used = cited | {page for page, runs in given.items() if runs & distinctive}
    if used:
        return sorted(used), "used"
    return sorted(given), "retrieved"


# ---- a project answer: citations and sources by document (C2.1 Task 15) ----

# "page 4", "p. 4", "pages 4-5", "pp. 4 and 6", "pages 4, 6 & 8"; page numbers of at most six digits. This is the
# grammar of the client (src/lib/citations.js): the server counts what the client links. A page word is followed by
# a space ("page3" is no citation), "p." and "pp." need not be.
_PAGE_LABEL = r"(?:pages?\s+|pp?\.\s*)"
_PAGE_ITEM = r"\d{1,6}(?:\s*[-\u2013\u2014]\s*\d{1,6})?"
_PAGE_LIST = rf"{_PAGE_ITEM}(?:\s*(?:,\s*and\b|,|\band\b|&)\s*{_PAGE_ITEM})*"
_PAGE_NUMBER = re.compile(r"(\d+)(?:\s*[-\u2013\u2014]\s*(\d+))?")
RANGE_PAGES = 10   # a range of up to this many pages counts every page in it; a longer one, only its two ends
_BARE_CITATION = re.compile(rf"\(\s*{_PAGE_LABEL}({_PAGE_LIST})\s*\)", re.IGNORECASE)
_WEB_SCHEMES = ("http://", "https://")   # the only links the client may render for a web source


def _page_numbers(spec: str) -> set[int]:
    """The pages of a list such as "4-5 and 8". A list with an item that starts below page 1 ("0-3", "0 and 4") is
    no citation as a whole, as the client reads it: none of its pages."""
    pages: set[int] = set()
    for first, last in _PAGE_NUMBER.findall(spec):
        lo = int(first)
        if lo < 1:
            return set()
        hi = int(last) if last else lo
        pages.update(range(lo, hi + 1) if 0 <= hi - lo < RANGE_PAGES else (lo, hi))
    pages.discard(0)    # the far end of "3-0" is no page
    return pages


def cited_pages(answer: str, names: list[str]) -> dict[str, set[int]]:
    """The pages the answer cites as `(Name, page N)`, by document, for any of `names` (their canonical
    spelling is the key; the match ignores case). A name may hold commas and parentheses, and one that is the
    front of another ("Guide.md", "Guide.md (2)") never claims the longer one's citation, because the longest
    is tried first.

    A citation is a part, NAME, comma, a page form, that sits right after "(" or ";" and right before ";" or ")":
    so a joined citation "(A.md, page 3; B.md, pages 1-2)" is several parts, a part with another name or a
    malformed neighbour ("(A.md, page 3;)", "(A.md, page 3; see (fig. 2))") leaves the rest counted, and
    "A.md, page 3" in running prose is none. The client reads it the same way (`named` in src/lib/citations.js).
    One scan of the answer, with no nesting to backtrack through: linear in its length for a fixed set of names."""
    names = [n for n in names if n]
    if not names or ("(" not in answer and ";" not in answer):
        return {}
    alternatives = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    part = re.compile(rf"(?<=[(;])\s*({alternatives})\s*,\s*{_PAGE_LABEL}({_PAGE_LIST})(?=\s*[;)])", re.IGNORECASE)
    canonical = {n.lower(): n for n in names}
    found: dict[str, set[int]] = {}
    for name, spec in part.findall(answer):
        pages = _page_numbers(spec)
        if pages and name.lower() in canonical:
            found.setdefault(canonical[name.lower()], set()).update(pages)
    return found


def sources_by_document(answer: str, evidence: list[tuple[str, int | None, str]],
                        docs: list[tuple[str, str]]) -> list[dict]:
    """The pages a project answer drew on, one entry per document the model was given a page of, in the
    order of `docs` ((doc_id, name), the scope's order): [{docId, docName, pages, used}].

    Per document, the rule of `answer_sources`: "used" is the pages it cites (as "(Name, page N)", or as a bare
    "(page N)" when the evidence comes from exactly one document, so the page can only be that document's) that
    it was given, plus the pages sharing a distinctive run with the answer; with none, `pages` is every page it
    was given and `used` is False (the UI labels these as searched). The boilerplate count is over the pages of
    every document: a running header on each document's pages says nothing about which was used.
    A citation of a name that isn't in `docs` makes no entry."""
    given: dict[str, dict[int, set[tuple[str, ...]]]] = {}
    for doc_id, page, text in evidence:
        if page is not None:
            given.setdefault(doc_id, {}).setdefault(page, set()).update(_runs(text))
    present = [(doc_id, name) for doc_id, name in docs if doc_id in given]
    if not present:
        return []
    cited = cited_pages(answer, [name for _, name in present])
    bare = {p for spec in _BARE_CITATION.findall(answer) for p in _page_numbers(spec)} if len(present) == 1 else set()
    distinctive = _distinctive(answer, (runs for doc_id, _ in present for runs in given[doc_id].values()))
    sources = []
    for doc_id, name in present:
        pages = given[doc_id]
        used = ((cited.get(name, set()) | bare) & pages.keys()) | {p for p, runs in pages.items() if runs & distinctive}
        sources.append({"docId": doc_id, "docName": name, "pages": sorted(used or pages), "used": bool(used)})
    return sources


def web_sources(web_results: dict[int, dict]) -> list[dict]:
    """The web pages an answer can have drawn on: the results (ToolContext.web_results) a reader returned
    facts from, by number, as [{n, title, url}]. The title is one inert line. Only http and https links
    qualify, whatever case: the client renders each as a link, and a search engine can list `javascript:` or
    `data:` URLs."""
    sources = []
    for n in sorted(web_results):
        entry = web_results[n]
        url = entry.get("url")
        if entry.get("read") and isinstance(url, str) and url.lower().startswith(_WEB_SCHEMES):
            sources.append({"n": n, "title": display_name(entry.get("title") or url), "url": url})
    return sources
