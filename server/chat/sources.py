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
"""
from __future__ import annotations

import re

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


def answer_sources(answer: str, evidence: list[tuple[int | None, str]]) -> tuple[list[int], str]:
    given: dict[int, set[tuple[str, ...]]] = {}
    for page, text in evidence:
        if page is not None:
            given.setdefault(page, set()).update(_runs(text))
    if not given:
        return [], "retrieved"
    cited = {int(a or b) for a, b in _CITATION.findall(answer)} & given.keys()
    seen_on: dict[tuple[str, ...], int] = {}
    for runs in given.values():
        for run in runs:
            seen_on[run] = seen_on.get(run, 0) + 1
    distinctive = {run for run in _runs(answer) if seen_on.get(run, 0) and seen_on[run] <= BOILERPLATE_PAGES}
    used = cited | {page for page, runs in given.items() if runs & distinctive}
    if used:
        return sorted(used), "used"
    return sorted(given), "retrieved"
