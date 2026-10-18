"""Project question-answering evaluation (C2.1 Task 1).

A project is a folder of documents, and the chat is asked about all of them.
The documents are a frozen copy of this repository's own docs
(fixtures/project_docs, see SOURCE.txt for the commit), the worked example in
the design: a newcomer asks what the features are, how to deploy and test,
and what to install on a system the docs never mention. A variant adds 41
generated padding documents (9 + 41 = 50), so the project is big enough that
the map is shortened.

The fixture, the cases and the scoring are pure (server/tests/test_eval_project_qa.py).
`run_eval` (C2.1 Task 4) needs Postgres, the embedding model and a chat model, so it runs on
the local machine, on a scratch database: `scripts/eval_project_qa.py --model ... --variant 9`.

The web-read case (`--web-fixture`) plays the web-consent journey as a user would: the question, the offer the
chat ends the turn with, and the user's yes (`context.web`), answered from a fake web (web_fixture.py).

Citations in project scope are "(Name, page N)", the form the app links
(spec section 5.7). A citation of the wrong document is worse than none, so
the score fails any answer that cites one a case forbids.
"""
from __future__ import annotations

import math
import random
import re
import sys
from dataclasses import dataclass, field, replace
from functools import cache
from pathlib import Path
from typing import Any

from .doc_qa import EvalSetupError, has_term, is_refusal, web_mode   # EvalSetupError: the script catches it from here

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "project_docs"

WEB_TOOLS = {"web_search", "read_web_pages"}   # any of these ran: a web request left the server
DOC_TOOLS = {"search_documents", "read_document_pages", "extract_from_documents"}   # a trail through the documents
OVERVIEW_MIN_DOCS = 3          # an overview cites at least this many distinct documents (spec section 9)
OVERVIEW_TERM_SHARE = 2 / 3    # ... and mentions at least this share of its listed features


@dataclass(frozen=True)
class Case:
    name: str
    kind: str                  # overview | procedure | code | exact-label | named-doc | balance | gap-general | live
                               # | map-shortened (an overview or code question again; run only in the 50-document
                               # variant) | web-read (the offer, the yes, then the answer; run only with --web-fixture)
    question: str
    expect: tuple[str, ...]    # terms a right answer contains, "a|b" = either
                               # (gap-general: expect[0] is the outside term)
    docs: tuple[str, ...]      # documents a right answer cites, each at least once
    forbid_docs: tuple[str, ...] = ()   # documents a right answer never cites
                                        # (a named-doc case: every document but its own, whatever is listed here)
    setup: tuple[str, ...] = ()         # earlier user turns, asked first in the same chat
    web: str = "ask"                    # the chat's web-search mode for this case
    pages: tuple[tuple[str, int], ...] = ()   # (document, page) citations a right answer includes
    forbid_terms: tuple[str, ...] = ()        # web-read: a near-miss a right answer never gives


# ---------- the fixture ----------

def fixture_files() -> list[Path]:
    """The frozen documents, by name. The eval reads these, never docs/."""
    return sorted(FIXTURE_DIR.glob("*.md"))


_PAD_TOPICS = {
    "Beekeeping": ("hive", "brood box", "queen", "frame", "smoker", "swarm"),
    "Sourdough baking": ("starter", "loaf", "crumb", "proofing basket", "oven", "dough"),
    "Ceramic glazing": ("kiln", "glaze", "bisque", "slip", "trimming tool", "wheel"),
    "Orchard pruning": ("pear tree", "graft", "shear", "scion", "rootstock", "canopy"),
    "Railway timetables": ("platform", "signal", "branch line", "carriage", "depot", "junction"),
    "Amateur astronomy": ("telescope", "eyepiece", "nebula", "tripod", "mount", "finder scope"),
    "Cheese ageing": ("rind", "cellar", "cheese wheel", "brine", "culture", "rack"),
    "Bicycle repair": ("chain", "spoke", "brake pad", "hub", "tyre", "derailleur"),
    "Wool dyeing": ("fleece", "mordant", "skein", "vat", "indigo", "madder"),
    "Mushroom foraging": ("woodland", "spore print", "cap", "gill", "basket", "clearing"),
}
_PAD_HEADINGS = ("Preparing the {a}", "Common mistakes with the {a}", "Seasonal notes", "Tools and storage",
                 "Looking after the {a}", "A beginner's checklist", "When the {a} goes wrong")
_PAD_SENTENCES = (
    "Check the {a} and the {b} before every session with the {c}.",
    "A worn {a} can spoil the {b}, so keep a spare {a} nearby.",
    "Most beginners forget the {a} until the {b} is already spoiled.",
    "The {a} needs {n} careful passes with the {b}.",
    "Wipe the {c} dry afterwards and store the {a} away from the damp.",
    "An old hand will tell you the {b} matters more than the {a}.",
    "Allow {n} days between one {c} and the next.",
)
_PAD_NUMBERS = ("two", "three", "four", "five", "seven")   # words, so no digit can match an expected figure


def padding_docs(n: int = 41, seed: int = 7) -> list[tuple[str, str]]:
    """`n` small generated Markdown documents, (file name, text): the same ones for the same seed.
    Hobby notes, about nothing in the fixture, so they add names and outlines to the map
    without adding an answer to any case. The default 41 plus the fixture's 9 make the 50-document variant."""
    rnd = random.Random(seed)
    topics = list(_PAD_TOPICS.items())
    docs = []
    for i in range(n):
        topic, nouns = topics[i % len(topics)]
        name = f"{topic} notes {i + 1:02d}"
        lines = [f"# {name}", ""]
        for heading in rnd.sample(_PAD_HEADINGS, 3):
            lines += [f"## {heading.format(a=rnd.choice(nouns))}", ""]
            for _ in range(2):
                sentences = []
                for _ in range(3):
                    a, b, c = rnd.sample(nouns, 3)
                    sentences.append(rnd.choice(_PAD_SENTENCES).format(a=a, b=b, c=c, n=rnd.choice(_PAD_NUMBERS)))
                lines += [" ".join(sentences), ""]
        docs.append((f"{name}.md", "\n".join(lines)))
    return docs


@cache
def _padding_names() -> frozenset[str]:
    return frozenset(name for name, _ in padding_docs())


# ---------- the cases ----------
# Every expected term below is a string of the frozen fixture, and every expected page is where the reader's
# extraction puts it (tests check both).

# Overview: no document is named; it passes on breadth (OVERVIEW_MIN_DOCS cited, OVERVIEW_TERM_SHARE of the terms).
_FEATURES = Case(
    "features", "overview", "What are the features?",
    ("text-to-speech|read aloud|reads aloud|kokoro", "chat", "library",
     "accounts|sign in|sign-in|login|log in|keycloak|oidc", "pdf|markdown", "admin|project|projects"),
    ())
# README says it once, in the Quickstart section's table (page 3); DEPLOYMENT says it on two pages.
_START = Case(
    "start-command", "code", "Which command does the README give for starting all the containers in the background?",
    ("./startup.sh start",), ("README.md",), pages=(("README.md", 3),))

CASES: tuple[Case, ...] = (
    _FEATURES,
    Case("deploy-and-test", "procedure", "How do I deploy and test it, step by step?",
         ("./startup.sh start", "pytest server/tests"), ("DEPLOYMENT.md", "TESTING.md")),
    _START,
    # In a fenced code block of README.md, which extraction skips today (spec F4).
    Case("quickstart-command", "code", "Which command does the Linux quickstart run to set everything up?",
         ("./startup.sh quickstart",), ("README.md",), pages=(("README.md", 3),)),
    # `.venv/bin/python -m pytest server/tests/ -q` is in TESTING.md's fence; README's variant has no "python -m".
    Case("test-command", "code", "What exact command runs the backend test suite, and what must be running first?",
         ("python -m pytest server/tests", "postgres"), ("TESTING.md",), pages=(("TESTING.md", 1),)),
    Case("session-lifetimes", "exact-label", "What are the default values of SESSION_TTL_HOURS and SESSION_IDLE_HOURS?",
         ("168", "12"), ("ARCHITECTURE.md",), pages=(("ARCHITECTURE.md", 3),)),
    Case("oidc-issuer", "exact-label", "What should OIDC_ISSUER be set to in production, and what is it in local dev?",
         ("https://auth.example.com/realms/natural-reader", "http://localhost:18080/realms/natural-reader"),
         ("DEPLOYMENT.md",), pages=(("DEPLOYMENT.md", 2),)),
    # README and CHAT_WITH_PDF say a great deal more about pins than the user guide does.
    Case("user-guide-pins", "named-doc", "What does the user guide say about pins?",
         ("each message|every message",), ("USER_GUIDE.md",), ("README.md", "CHAT_WITH_PDF.md")),
    # README's nginx example has the same setting.
    Case("deployment-uploads", "named-doc", "In DEPLOYMENT.md, why do larger PDF uploads fail, and what fixes it?",
         ("client_max_body_size", "100m|100 mb|100mb"), ("DEPLOYMENT.md",), ("README.md",)),
    # The answer is in the 4 KB docs index; the long documents have many near-matches for "next" and "work".
    Case("next-work", "balance", "Where do I look to find out what the next piece of work is?",
         ("HANDOVER",), ("DOCS_INDEX.md",)),
    Case("changelog-unreleased", "balance", "What does the [Unreleased] section of the changelog cover?",
         ("v1.9.0|1.9.0",), ("DOCS_INDEX.md",)),
    # RHEL is in none of the documents. The documents' own requirements may be cited, but not for RHEL.
    Case("rhel-packages", "gap-general", "I'm on RHEL 9, which packages do I need?", ("RHEL",), ()),
    Case("ollama-release", "live", "What's the latest Ollama release?", (), ()),
    replace(_FEATURES, name="features-50", kind="map-shortened"),
    replace(_START, name="start-command-50", kind="map-shortened"),
)


# The web-read case runs only with --web-fixture (web_fixture.py has the pages). Marrowdeck is made up: the
# answer is on the current guide's page past its 6,000th character, and the archived guide a search lists first
# gives a version one digit off (4.17.3), in its snippet too.
WEB_CASES: tuple[Case, ...] = (
    Case("web-read", "web-read",
         "I'm installing the current Marrowdeck on Rocky Linux 9. Which package do I install, and what is the "
         "minimum version it needs?",
         ("marrowdeck-srv", "4.18.3"), (), forbid_terms=("4.17.3",)),
)


def _scoring_kind(case: Case) -> str:
    """A map-shortened case is scored as the case it repeats."""
    if case.kind != "map-shortened":
        return case.kind
    return next((c.kind for c in CASES if c.kind != "map-shortened" and c.question == case.question), case.kind)


def _terms_needed(case: Case) -> int:
    """How many expected terms a right answer contains: all, except an overview, which lists features."""
    if _scoring_kind(case) == "overview":
        return math.ceil(OVERVIEW_TERM_SHARE * len(case.expect))
    return len(case.expect)


# ---------- scoring ----------

def _citation_pattern(names: list[str]) -> re.Pattern[str] | None:
    """`(Name, page N)`, with any of `names`: longest first, so a name that ends like another isn't
    mistaken for it, and a name may hold commas and parentheses."""
    if not names:
        return None
    alternatives = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(rf"\(\s*({alternatives})\s*,\s*page\s+(\d+)\s*\)", re.IGNORECASE)


def cited_docs(answer: str, names: list[str]) -> dict[str, set[int]]:
    """The documents (by their names in `names`) and pages the app would link in this answer."""
    pattern = _citation_pattern(names)
    if pattern is None:
        return {}
    by_lower = {n.lower(): n for n in names}
    found: dict[str, set[int]] = {}
    for match in pattern.finditer(answer):
        page = int(match.group(2))
        if page >= 1:     # page 0 is no page
            found.setdefault(by_lower[match.group(1).lower()], set()).add(page)
    return found


_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n+")


def _pages_cited_in_sentences_with(answer: str, names: list[str], term: str) -> set[int]:
    """The pages cited, as `(Name, page N)`, inside sentences that mention `term`. A citation never splits a
    sentence, even when a name holds a full stop and a space."""
    pattern = _citation_pattern(names)
    cites = list(pattern.finditer(answer)) if pattern else []
    masked = list(answer)
    for m in cites:
        masked[m.start():m.end()] = "\0" * (m.end() - m.start())
    spans, start = [], 0
    for b in _SENTENCE_BREAK.finditer("".join(masked)):
        spans.append((start, b.start()))
        start = b.end()
    spans.append((start, len(answer)))
    return {int(m.group(2)) for a, b in spans if has_term(answer[a:b], term)
            for m in cites if a <= m.start() < b and int(m.group(2)) >= 1}


@dataclass
class Result:
    """The fields of doc_qa.Result, with their meaning, then this evaluation's own."""
    case: Case
    answer: str
    tools: list[str] = field(default_factory=list)
    rounds: int = 0
    fact: bool | None = None          # every expected term is in the answer (an overview: enough of them)
    cited: bool | None = None         # every document the case names is cited (an overview: enough documents)
    cited_inline: bool | None = None  # ... and every listed page, by the model itself, as "(Name, page N)"
    chip_pages: list[int] = field(default_factory=list)   # the source chips the app showed: recorded, not scored
    refused: bool | None = None       # gap-general: says the documents don't cover it
    invented_pages: set[int] = field(default_factory=set)   # gap-general: pages cited for the part they don't cover
    invented_figure: bool = False     # not scored here: a gap answer rightly gives the documents' own version numbers
    error: str | None = None          # the turn ended in an error event: not the model's answer
    passed: bool = False
    seconds: float = 0.0              # the case question's turn, wall clock
    trail: bool | None = None         # a document tool ran, or prefetch supplied passages
    skipped: str | None = None        # why the case couldn't be asked on this deployment
    pages_ok: bool | None = None      # every (document, page) the case lists is cited
    cited_docs: set[str] = field(default_factory=set)
    wrong_docs: set[str] = field(default_factory=set)   # cited, and not the right one (see score)
    web_calls: int = 0
    offered: bool = False             # the turn ended with the offer to search the web
    mode: str = "auto"                # the web search mode the case was asked in (its `web`; the field exists in doc_qa.Result too)
    near_miss: bool = False           # web-read: the answer gives a number the case forbids

    @property
    def paged_gap(self) -> bool:
        """gap-general: a document was cited in a sentence about the part they don't cover."""
        return bool(self.invented_pages)


def score(case: Case, answer: str, tool_calls: list[dict[str, Any]], names: list[str], *,
          offered: bool, error: str | None = None, prefetched: bool = False, web_offered: bool = True,
          chip_pages: list[int] | None = None) -> Result:
    """`tool_calls`: the turn's tool-input-available events ({toolName, round}); {"name": ...} is read too.
    `names`: every document of the project, so the citations can be read. `offered`: the turn
    ended with an offer to search the web (the data-web-offer event). `prefetched`: the app searched the
    documents before the model ran. `web_offered`: web search is configured here; without it the live and
    gap-general cases can't be asked (no offer is possible), and are skipped."""
    tools = [c.get("toolName") or c.get("name") for c in tool_calls]
    result = Result(case, answer, tools, max((c.get("round") or 0 for c in tool_calls), default=0),
                    web_calls=sum(t in WEB_TOOLS for t in tools), offered=offered, error=error, mode=case.web)
    result.chip_pages = sorted(chip_pages or [])
    if error:
        return result
    if case.kind in ("live", "gap-general", "web-read") and not web_offered:
        result.skipped = "web search not offered"
        return result
    # A padding document is never the right one to cite, and a case may name documents the runner left out.
    known = list(dict.fromkeys([*names, *case.docs, *case.forbid_docs, *sorted(_padding_names())]))
    pages = cited_docs(answer, known)
    result.cited_docs = set(pages)
    if _scoring_kind(case) == "named-doc":
        result.wrong_docs = result.cited_docs - set(case.docs)      # "cites only that document"
    else:
        result.wrong_docs = result.cited_docs & (set(case.forbid_docs) | _padding_names())
    result.fact = sum(has_term(answer, term) for term in case.expect) >= _terms_needed(case)
    result.cited = set(case.docs) <= result.cited_docs and (
        _scoring_kind(case) != "overview" or len(result.cited_docs) >= OVERVIEW_MIN_DOCS)
    result.pages_ok = all(page in pages.get(doc, ()) for doc, page in case.pages)
    if case.kind == "live":
        result.passed = offered and result.web_calls == 0
    elif case.kind == "web-read":
        # `answer` and `tool_calls` are the turn after the user's yes; `offered` is the first turn's, recorded.
        result.near_miss = any(has_term(answer, term) for term in case.forbid_terms)
        result.passed = result.fact and not result.near_miss
    elif case.kind == "gap-general":
        # A gap in the documents is filled from labelled general knowledge OR by the offer to search the web
        # (spec 5.3, 5.8; Task 14): a written refusal that names the gap passes, and so does an offer that
        # refuses in words or wrote nothing (the offer ends the turn, so a model may call it first). Neither may
        # cite a page for the part the documents lack.
        result.refused = is_refusal(answer)
        result.invented_pages = _pages_cited_in_sentences_with(answer, known, case.expect[0])
        written_gap = result.fact and result.refused
        offer_turn = offered and (result.refused or not answer.strip())
        result.passed = (not result.invented_pages and not result.wrong_docs and (written_gap or offer_turn))
    else:
        result.cited_inline = result.cited and result.pages_ok
        result.passed = result.fact and result.cited and result.pages_ok and not result.wrong_docs
        result.trail = bool(DOC_TOOLS & set(tools)) or prefetched
    return result


def _detail(r: Result) -> str:
    if r.error:
        return f"the turn failed ({r.error}): fix the setup, this says nothing about the model"
    if r.skipped:
        return f"skipped: {r.skipped}"
    case = r.case
    if case.kind == "live":
        return ("offered the search" if r.offered else "no offer") + f"; {r.web_calls} web calls"
    if case.kind == "web-read":
        found = "fact ok" if r.fact else f"fact missing ({', '.join(case.expect)})"
        miss = (f"gave the near-miss ({', '.join(t for t in case.forbid_terms if has_term(r.answer, t))})"
                if r.near_miss else "near-miss avoided")
        return f"{'offered the search' if r.offered else 'no offer'}; {found}; {miss}; {r.web_calls} web calls"
    parts = []
    unwritten = case.kind == "gap-general" and not r.answer.strip()   # the offer came first: no words to judge
    if unwritten:
        parts.append("wrote no text")
    elif case.expect:
        need = _terms_needed(case)
        if need < len(case.expect):
            hits = sum(has_term(r.answer, term) for term in case.expect)
            missing = "" if r.fact else f": {', '.join(case.expect)}"
            parts.append(f"{hits}/{len(case.expect)} terms (need {need}){missing}")
        else:
            parts.append("fact ok" if r.fact else f"fact missing ({', '.join(case.expect)})")
    if case.kind == "gap-general":
        if not unwritten:
            parts.append("said the documents don't cover it" if r.refused
                         else "didn't say the documents don't cover it")
        if r.paged_gap:
            parts.append(f"cited pages {sorted(r.invented_pages)} for the part they don't cover")
        parts.append("offered the search" if r.offered else "no offer")
    if _scoring_kind(case) == "overview":
        parts.append(f"{len(r.cited_docs)} documents cited (need {OVERVIEW_MIN_DOCS})")
    if case.docs:
        not_cited = [d for d in case.docs if d not in r.cited_docs]
        parts.append("documents ok" if not not_cited else f"not cited ({', '.join(not_cited)})")
    if case.pages:
        want = ", ".join(f"{d} p. {p}" for d, p in case.pages)
        parts.append("pages ok" if r.pages_ok else f"page missing (want {want})")
    if r.wrong_docs:
        parts.append(f"cited the wrong document ({', '.join(sorted(r.wrong_docs))})")
    return "; ".join(parts)


def _verdict(r: Result) -> str:
    if r.error:
        return "ERROR"
    if r.skipped:
        return "SKIP"
    if not r.passed:
        return "FAIL"
    # Right, but without the journey: the model got there from memory or luck.
    return "PASS (no trail)" if r.trail is False else "PASS"


def format_report(runs: list[list[Result]], *, model: str, variant: str) -> str:
    """One line per case per run; with several runs, a per-kind tally; the right-document citation rate."""
    lines = [f"Project QA eval: {model} (variant {variant})"]
    for i, results in enumerate(runs, start=1):
        if len(runs) > 1:
            lines.append(f"-- run {i}")
        for r in results:
            tools = ",".join(r.tools) or "-"
            lines.append(f"{_verdict(r):<5} {r.case.kind:<14} {r.case.name:<22} {r.seconds:.1f}s rounds={r.rounds} "
                         f"tools={tools}  {_detail(r)}")
    every = [r for results in runs for r in results]
    asked = [r for r in every if not r.skipped]
    if len(runs) > 1:
        tally: dict[str, tuple[int, int]] = {}
        for r in asked:
            n, p = tally.get(r.case.kind, (0, 0))
            tally[r.case.kind] = (n + 1, p + r.passed)
        lines.append("tally: " + ", ".join(f"{k} {p}/{n}" for k, (n, p) in tally.items()))
    answered = [r for r in asked if not r.error
                and (r.case.docs or r.case.forbid_docs or _scoring_kind(r.case) == "overview")]
    if answered:
        right = sum(bool(r.cited) and not r.wrong_docs for r in answered)
        lines.append(f"right-document citations {right}/{len(answered)}")
    lines.append(f"web calls {sum(r.web_calls for r in every)}")
    skipped = len(every) - len(asked)
    lines.append(f"{sum(r.passed for r in asked)}/{len(asked)} passed" + (f" ({skipped} skipped)" if skipped else ""))
    return "\n".join(lines)


# ---------- the live run ----------

EVAL_SUB = "project-qa"
PROJECT_NAME = "Eval project"
VARIANTS = ("9", "50")
TURN_TIMEOUT_S = 300           # one chat turn; a hung provider ends as an error, not a hung eval


def cases_for(variant: str, web_fixture: bool = False) -> tuple[Case, ...]:
    """The cases this variant asks: the map-shortened ones only where the project is big enough for that, and
    the web-read case only with a fake web to read."""
    return tuple(c for c in CASES if variant == "50" or c.kind != "map-shortened") + (WEB_CASES if web_fixture else ())


def project_documents(variant: str) -> list[tuple[str, bytes]]:
    """(name, bytes) of every document of the project: the fixture, plus the padding in variant "50"."""
    docs = [(p.name, p.read_bytes()) for p in fixture_files()]
    if variant == "50":
        docs += [(name, text.encode("utf-8")) for name, text in padding_docs()]
    return docs


async def seed_project(conn, user_id: str, documents: list[tuple[str, bytes]], *, name: str) -> tuple[str, list[str]]:
    """A fresh project for the eval user, owned by them, with `documents` filed as it files a
    library document (doc_versions.file_from_library). The content stays indexed between runs
    (its bytes are written to the storage folder again, the script's temporary one); only the
    project is made anew, so no earlier run's documents linger in it.
    Returns (project id, the content doc ids, in project order)."""
    import hashlib

    from ..services import doc_storage, doc_versions

    await conn.execute("DELETE FROM projects WHERE created_by = %s AND name = %s", (user_id, name))
    cur = await conn.execute("INSERT INTO projects (created_by, name) VALUES (%s, %s) RETURNING id", (user_id, name))
    project_id = str((await cur.fetchone())[0])
    await conn.execute("INSERT INTO project_members (project_id, user_id, role, added_by, added_via) "
                       "VALUES (%s, %s, 'owner', %s, 'member')", (project_id, user_id, user_id))
    base = doc_storage.storage_dir()
    base.mkdir(parents=True, exist_ok=True)
    doc_ids = []
    for file_name, data in documents:
        doc_id = hashlib.sha256(data).hexdigest()
        file_type = doc_storage.type_from_name(file_name)
        path = base / f"{doc_id}.{doc_storage._EXT[file_type]}"
        path.write_bytes(data)
        await conn.execute(
            "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, state, bytes_path, extracted_by) "
            "VALUES (%s, %s, %s, %s, 'stored', %s, 'server') "
            # An earlier run kept its files in a storage folder that is gone by now: point at this run's.
            "ON CONFLICT (doc_id) DO UPDATE SET bytes_path = EXCLUDED.bytes_path",
            (doc_id, file_name, file_type, len(data), str(path)))
        await conn.execute(
            "INSERT INTO library_entries (user_id, doc_id, added_via, verified, file_name) "
            "VALUES (%s, %s, 'upload', true, %s) ON CONFLICT DO NOTHING", (user_id, doc_id, file_name))
        await doc_versions.file_from_library(conn, user_id, project_id, doc_id, file_name=file_name, keep_both=True)
        doc_ids.append(doc_id)
    return project_id, doc_ids


@dataclass
class Turn:
    """What one chat turn did, read from its events."""
    answer: str = ""
    calls: list[dict[str, Any]] = field(default_factory=list)   # the tool-input-available events (the app's too)
    error: str | None = None
    prefetched: bool = False        # the app searched the documents before the model ran
    chips: list[int] = field(default_factory=list)              # source chip pages
    offered: bool = False           # the turn ended with the offer to search the web
    offer_query: str | None = None  # ... and the query it offered


async def _turn(text: str, *, session_id: str, user_id: str, project_id: str, model: str, think: str,
                router, cfg, budget, web_query: str | None = None) -> Turn:
    """One chat turn through the real path, scoped to the project. `web_query`: the user's yes to a web
    search, sent as `context.web` does."""
    import asyncio
    from contextlib import aclosing

    from ..chat import store
    from ..chat.orchestrator import TurnRequest, run_turn
    from ..llm.types import CallSettings

    out = Turn()
    claim = await store.begin_turn(session_id=session_id, user_id=user_id, model_id=model, text=text,
                                   attachments=[], new_session_pins=None, scope_project_id=project_id)
    req = TurnRequest(user_id=user_id, session_id=session_id, model_id=model, text=text, attachments=(),
                      settings=CallSettings(think=think), doc_id=None, timezone="UTC", project_id=project_id,
                      web_query=web_query)
    try:
        async with asyncio.timeout(TURN_TIMEOUT_S):
            async with aclosing(run_turn(req, claim, router=router, cfg=cfg, deployment_budget=budget)) as events:
                async for ev in events:
                    if ev["type"] == "text-delta":
                        out.answer += ev["delta"]
                    elif ev["type"] == "tool-input-available":
                        out.calls.append(ev)
                        out.offered = out.offered or ev.get("toolName") == "offer_web_search"
                    elif ev["type"] == "data-web-offer":
                        out.offered, out.offer_query = True, ev.get("query")
                    elif ev["type"] == "data-sources":
                        out.chips += [p for p in ev.get("pages") or [] if isinstance(p, int)]
                    elif ev["type"] == "data-context":
                        out.prefetched = out.prefetched or any(i.get("kind") == "prefetch" for i in ev.get("items") or [])
                    elif ev["type"] == "error":
                        out.error = ev.get("code") or "error"
    except TimeoutError:
        out.error = "timeout"
    return out


async def _ask(case: Case, *, names: list[str], web_offered: bool, **turn) -> Result:
    """Ask the case's setup turns, then its question, in one chat, in the case's web mode; score and time the
    question's turn only. A web-read case goes on to the user's yes: the query the chat offered (the question
    itself if it offered none) sent as `context.web`. That second turn is the one scored and timed; whether
    the first one offered is recorded."""
    import time
    import uuid

    from ..db import get_pool

    session_id = f"eval-{uuid.uuid4()}"
    try:
        with web_mode(case.web):
            for text in case.setup:
                setup = await _turn(text, session_id=session_id, **turn)
                if setup.error:
                    return score(case, "", [], names, offered=False, error=setup.error)
            started = time.monotonic()
            first = await _turn(case.question, session_id=session_id, **turn)
            last, offered = first, first.offered
            if case.kind == "web-read" and web_offered and not first.error:
                query = first.offer_query or case.question
                started = time.monotonic()
                last = await _turn(f"Search the web for: {query}", session_id=session_id, web_query=query, **turn)
            seconds = time.monotonic() - started
    finally:
        # Even on an error or Ctrl-C: the eval leaves no chats behind.
        async with get_pool().connection() as conn:
            await conn.execute("DELETE FROM chat_sessions WHERE id = %s", (session_id,))
    result = score(case, last.answer, last.calls, names, offered=offered, error=first.error or last.error,
                   prefetched=last.prefetched, web_offered=web_offered, chip_pages=last.chips)
    result.seconds = seconds
    return result


async def run_eval(model: str, *, variant: str = "9", think: str = "off", repeat: int = 1,
                   show_answers: bool = False, web_fixture: bool = False) -> list[list[Result]]:
    """Index the project's documents for the eval user and ask every case through run_turn,
    scoped to the project, started and stopped as the app does. Needs DATABASE_URL (a scratch
    database: the script refuses the real one), the embedding model and `model` reachable;
    raises EvalSetupError otherwise. The documents' files go to DOC_STORAGE_DIR, which the
    script points at a temporary folder of its own: a caller other than the script must do the same. Documents stay indexed, so a second run doesn't re-embed them.
    `web_fixture`: serve web search and page fetches from fixtures/web (web_fixture.py) in this process, and
    ask the web-read case too."""
    from contextlib import nullcontext

    from ..chat import orchestrator
    from ..chat.config import get_chat_config
    from ..db import close_db, get_pool, init_db
    from ..llm.router import UnknownModel, get_router, start_router, stop_router
    from ..services import doc_pipeline, embeddings, model_router, web_search
    from . import web_fixture as fake_web
    from .doc_qa import eval_user

    if variant not in VARIANTS:
        raise EvalSetupError(f"variant must be one of {', '.join(VARIANTS)}.")
    documents = project_documents(variant)
    names = [name for name, _ in documents]
    if not await init_db():
        raise EvalSetupError("The database isn't reachable (DATABASE_URL).")
    try:
        await embeddings.start_client()
        await web_search.start_client()
        await start_router()
        router = get_router()
        if not router.is_allowed(model):     # the turn route's order: allowed first, then resolved
            raise EvalSetupError(f"{model} isn't allowed on this deployment (INFERENCE_MODELS).")
        try:
            canonical = router.canonical_id(model)
        except UnknownModel as e:
            raise EvalSetupError(f"No provider serves {model!r}: {e}") from e
        async with get_pool().connection() as conn:
            user_id = await eval_user(conn, sub=EVAL_SUB, email="project-qa-eval@example.com",
                                      display_name="Project QA eval")
            project_name = PROJECT_NAME if variant == "9" else f"{PROJECT_NAME} ({variant} documents)"
            project_id, doc_ids = await seed_project(conn, user_id, documents, name=project_name)
        for doc_id in dict.fromkeys(doc_ids):
            await doc_pipeline.run_pipeline(doc_id)     # a no-op once indexed
            await doc_pipeline.run_rebuild(doc_id)      # a no-op under the current profile
        async with get_pool().connection() as conn:
            cur = await conn.execute(
                "SELECT file_name, state, error_message FROM documents WHERE doc_id = ANY(%s) AND state <> 'indexed'",
                (doc_ids,))
            unindexed = await cur.fetchall()
        if unindexed:
            first = ", ".join(f"{n} ({s}: {m})" for n, s, m in unindexed[:3])
            raise EvalSetupError(f"{len(unindexed)} documents didn't index: {first}. Is the embedding model up?")

        from ..chat.tools import web_search as web_search_tool
        # With no turn to ask about, "available" means web search is set up here: the offer (the default mode)
        # and the web tools both need exactly that.
        web_offered = bool(web_search_tool.TOOL.available(None))
        runs = []
        with (fake_web.served() if web_fixture else nullcontext()) as web:
            for _ in range(repeat):
                results = []
                for case in cases_for(variant, web_fixture):
                    result = await _ask(case, names=names, web_offered=web_offered, user_id=user_id,
                                        project_id=project_id, model=canonical, think=think, router=router,
                                        cfg=get_chat_config(), budget=model_router.get_config().daily_token_budget)
                    results.append(result)
                    print(f"{_verdict(result):<5} {case.name} {result.seconds:.1f}s", file=sys.stderr, flush=True)
                    if case.kind == "web-read" and web is not None:
                        print(f"      the fake web was asked: {web.queries} and fetched {web.fetched}",
                              file=sys.stderr, flush=True)
                        web.queries.clear(), web.fetched.clear()
                    if show_answers:
                        print(f"--- {case.kind} {case.name}: {case.question}\n{result.answer}\n")
                runs.append(results)
        return runs
    finally:
        await stop_router()
        await web_search.stop_client()
        await embeddings.stop_client()
        await orchestrator.drain_background(timeout=5)
        await close_db()
