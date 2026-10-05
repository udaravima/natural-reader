"""Document question-answering evaluation (v2.3 Task G).

A fixed multi-page document with planted facts is indexed, and the real chat
turn path (run_turn: prefetch, the tool rounds, the rules) answers questions
about it with a configured model. Each answer is scored on: the fact, the
"(page N)" citations the app links, and, for a fact the document doesn't
have, a refusal with no invented page.

The fixture and the scoring here are pure (server/tests/test_eval_doc_qa.py);
`run_eval` needs Postgres, the embedding model and a chat model, so it runs on
the local machine: `python scripts/eval_doc_qa.py --model ollama:llama3.2:3b`.
"""
from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass, field
from typing import Any

from ..services.extract import SENTENCES_PER_PAGE   # a text file's reader page

PAGES = 10
FILE_NAME = "Eval Report.txt"


@dataclass(frozen=True)
class Case:
    name: str
    kind: str                  # single-hop | two-hop | exact-label | page-read | absent (the document),
                               # small-talk | general | live | follow-up (the strategy: which source, if any)
    question: str
    facts: tuple[str, ...]     # planted sentences
    pages: tuple[int, ...]     # the reader page of each fact
    expect: tuple[str, ...]    # terms a right answer contains, "a|b" = either (absent: terms the document lacks)
    setup: tuple[str, ...] = ()   # earlier user turns, asked first in the same chat (follow-up)


@dataclass(frozen=True)
class Fixture:
    text: str
    doc_id: str
    cases: tuple[Case, ...]


# (kind, name, question, [(sentence, page)], expected terms[, setup turns])
_PLANTED = [
    ("single-hop", "codename", "What is the project's codename?",
     [("The project's codename is BLUE HERON.", 2)], ("blue heron",)),
    ("two-hop", "dataset", "Which dataset does the method in chapter 3 use, and who collected it?",
     [("The method in chapter 3 uses the ZEPHYR-9 dataset.", 3),
      ("The ZEPHYR-9 dataset was collected by the Aldermoor Institute.", 8)], ("zephyr-9", "aldermoor")),
    ("exact-label", "table", "What does Table 7.3 report?",
     [("Table 7.3 reports a median latency of 41 ms.", 6)], ("41 ms|41ms|41 milliseconds",)),
    ("page-read", "page", "What does page 5 say about the field trial?",
     [("The field trial ran for nine weeks in Tromsø.", 5)], ("nine weeks|9 weeks|nine-week|9-week",)),
    ("absent", "absent", "What is the capital budget for 2030?", [], ("capital budget", "2030")),
    # v2.4: the strategy's other sources. None of them plants a fact, so the
    # document (and its doc_id) is the same as before: earlier runs compare.
    ("small-talk", "thanks", "Thanks, that's all I needed!", [], ()),
    ("general", "moon", "In what year did people first walk on the Moon?", [], ("1969",)),
    # Tromsø is in the document (page 5): a name from it with a live intent.
    ("live", "weather", "What is the weather in Tromsø right now?", [], ()),
    ("follow-up", "shorter", "Say that in five words or fewer.", [], ("blue heron",),
     ("What is the project's codename?",)),
]
DOC_TOOLS = {"search_documents", "read_document_pages"}

_TOPICS = ["staffing", "procurement", "site safety", "data retention", "stakeholder feedback",
           "training", "risk registers", "quality checks"]
_FILLER = [
    "Section {s} reviews {t} in the context of the wider programme.",
    "The team recorded {n} observations about {t} during this phase.",
    "Earlier drafts treated {t} differently, as the appendix explains.",
    "Readers interested in {t} should compare this with section {s}.",
    "A short summary of {t} closes this part of the report.",
]


def build_fixture() -> Fixture:
    """The same document and cases every time (a fixed seed)."""
    rnd = random.Random(7)
    sentences = [rnd.choice(_FILLER).format(s=rnd.randint(1, 12), n=rnd.randint(3, 97), t=rnd.choice(_TOPICS))
                 for _ in range(PAGES * SENTENCES_PER_PAGE)]
    cases = []
    for kind, name, question, facts, expect, *setup in _PLANTED:
        for sentence, page in facts:
            # Somewhere in the middle of its page, never at a page edge.
            sentences[(page - 1) * SENTENCES_PER_PAGE + rnd.randint(5, SENTENCES_PER_PAGE - 6)] = sentence
        cases.append(Case(name, kind, question, tuple(s for s, _ in facts), tuple(p for _, p in facts), expect,
                          tuple(setup[0]) if setup else ()))
    text = " ".join(sentences) + "\n"
    return Fixture(text, hashlib.sha256(text.encode("utf-8")).hexdigest(), tuple(cases))


# ---------- scoring ----------

_CITATION = re.compile(r"\(page\s+(\d+)\)|\bpage\s+(\d+)\b", re.IGNORECASE)   # src/lib/citations.js
_HYPHENS = re.compile("[‐‑‒–—−]")
_NEG = r"(?:n't|not|never)"
_REFUSAL = re.compile(
    rf"{_NEG} (?:seem to |appear to )?(?:cover|mention|say|contain|include|discuss|provide|specify|give|state)"
    rf"|(?:{_NEG}|unable to|cannot) (?:be )?(?:find|found|locate)"
    rf"|{_NEG} (?:mentioned|covered|found|included|stated|specified|provided|given)"
    r"|nothing (?:about|on|regarding)|says nothing|no (?:information|mention|details?|data) (?:about|on|of|regarding)"
    rf"|{_NEG} have (?:any )?(?:information|details)",
    re.IGNORECASE)


def _normalise(text: str) -> str:
    text = text.replace("\u2019", "'").replace("\u00a0", " ").replace("\u202f", " ")
    return _HYPHENS.sub("-", text).lower()


def cited_pages(answer: str) -> set[int]:
    """The pages the app would link in this answer (page 0 is no page)."""
    return {int(a or b) for a, b in _CITATION.findall(answer) if int(a or b) >= 1}


def is_refusal(answer: str) -> bool:
    return bool(_REFUSAL.search(_normalise(answer)))


def has_term(answer: str, term: str) -> bool:
    """`term` ("a|b" = either) as whole words, any hyphen, any case: "41 ms"
    is not found in "410 ms"."""
    text = _normalise(answer)
    return any(re.search(rf"(?<!\w){re.escape(_normalise(alt))}(?!\w)", text) for alt in term.split("|"))


def _invents_a_figure(answer: str) -> bool:
    """For a fact the document lacks: a number other than the question's own
    year or a cited page is an answer made up after (or instead of) a refusal."""
    rest = _CITATION.sub(" ", answer).replace("2030", " ")
    return bool(re.search(r"\d", rest))


@dataclass
class Result:
    case: Case
    answer: str
    tools: list[str] = field(default_factory=list)
    rounds: int = 0
    fact: bool | None = None          # fact cases: every expected term is in the answer
    cited: bool | None = None         # fact cases: every fact's page is cited
    refused: bool | None = None       # absent case
    invented_pages: set[int] = field(default_factory=set)   # absent case: pages cited anyway
    invented_figure: bool = False     # absent case: a number given anyway
    error: str | None = None          # the turn ended in an error event: not the model's answer
    passed: bool = False
    seconds: float = 0.0              # the case question's turn, wall clock
    trail: bool | None = None         # fact cases: a document tool ran, or prefetch supplied passages
    skipped: str | None = None        # why the case couldn't be asked on this deployment


def score(case: Case, answer: str, tool_calls: list[dict[str, Any]], *, error: str | None = None,
          prefetched: bool = False, web_offered: bool = True) -> Result:
    """`tool_calls`: the turn's tool-input-available events ({toolName, round}).
    `prefetched`: the app searched the document before the model ran and
    handed it passages. `web_offered`: web_search is configured here."""
    result = Result(case, answer, [c.get("toolName") for c in tool_calls],
                    max((c.get("round") or 0 for c in tool_calls), default=0), error=error)
    if error:
        return result
    if case.kind == "live" and not web_offered:
        result.skipped = "web_search not offered"
        return result
    if case.kind == "small-talk":
        result.passed = not result.tools and not cited_pages(answer)
    elif case.kind == "general":
        result.fact = all(has_term(answer, term) for term in case.expect)
        result.passed = result.fact and not cited_pages(answer)
    elif case.kind == "live":
        result.passed = "web_search" in result.tools
    elif case.kind == "follow-up":
        result.fact = all(has_term(answer, term) for term in case.expect)
        result.passed = result.fact and not result.tools
    elif case.kind == "absent":
        result.refused = is_refusal(answer)
        result.invented_pages = cited_pages(answer)
        result.invented_figure = _invents_a_figure(answer)
        result.passed = result.refused and not result.invented_pages and not result.invented_figure
    else:
        result.fact = all(has_term(answer, term) for term in case.expect)
        result.cited = set(case.pages) <= cited_pages(answer)
        result.passed = result.fact and result.cited
        result.trail = bool(DOC_TOOLS & set(result.tools)) or prefetched
    return result


def _detail(r: Result) -> str:
    if r.error:
        return f"the turn failed ({r.error}): fix the setup, this says nothing about the model"
    if r.skipped:
        return f"skipped: {r.skipped}"
    kind = r.case.kind
    if kind == "small-talk":
        if r.tools:
            return "called a tool for small talk"
        return "cited a page for small talk" if cited_pages(r.answer) else "no tools, no page"
    if kind == "general":
        if not r.fact:
            return f"fact missing ({', '.join(r.case.expect)})"
        return "cited a document page" if cited_pages(r.answer) else "fact ok; no page"
    if kind == "live":
        return "searched the web" if r.passed else "didn't search the web"
    if kind == "follow-up":
        fact = "fact ok" if r.fact else f"fact missing ({', '.join(r.case.expect)})"
        return f"{fact}; {'searched again' if r.tools else 'no new search'}"
    if r.case.kind == "absent":
        if r.invented_pages:
            return f"cited pages {sorted(r.invented_pages)} for a fact the document lacks"
        if r.invented_figure:
            return "gave a figure the document doesn't have"
        return "refused" if r.refused else "answered instead of saying the document doesn't cover it"
    fact = "fact ok" if r.fact else f"fact missing ({', '.join(r.case.expect)})"
    pages = "pages ok" if r.cited else f"pages missing (want {', '.join(map(str, r.case.pages))})"
    return f"{fact}; {pages}"


def _verdict(r: Result) -> str:
    if r.error:
        return "ERROR"
    if r.skipped:
        return "SKIP"
    if not r.passed:
        return "FAIL"
    # Right, but without the journey: the model got there from memory or luck.
    return "PASS (no trail)" if r.trail is False else "PASS"


def format_report(runs: list[list[Result]], *, model: str) -> str:
    """One line per case per run; with several runs, a per-case tally."""
    lines = [f"Document QA eval: {model}"]
    for i, results in enumerate(runs, start=1):
        if len(runs) > 1:
            lines.append(f"-- run {i}")
        for r in results:
            tools = ",".join(r.tools) or "-"
            lines.append(f"{_verdict(r):<5} {r.case.kind:<12} {r.seconds:.1f}s rounds={r.rounds} tools={tools}  "
                         f"{_detail(r)}")
    every = [r for results in runs for r in results]
    asked = [r for r in every if not r.skipped]
    if len(runs) > 1:
        tally = {}
        for r in asked:
            n, p = tally.get(r.case.kind, (0, 0))
            tally[r.case.kind] = (n + 1, p + r.passed)
        lines.append("tally: " + ", ".join(f"{k} {p}/{n}" for k, (n, p) in tally.items()))
    skipped = len(every) - len(asked)
    lines.append(f"{sum(r.passed for r in asked)}/{len(asked)} passed" + (f" ({skipped} skipped)" if skipped else ""))
    return "\n".join(lines)


# ---------- the live run ----------

EVAL_ISS, EVAL_SUB = "natural-reader-eval", "doc-qa"


class EvalSetupError(RuntimeError):
    """The harness couldn't run (database, models): not a verdict on the model."""


async def eval_user(conn) -> str:
    """The eval's own user: a row of its own, never via the OIDC resolver,
    which binds a new identity to an unclaimed seed admin (on a dev-bypass
    machine, the person's own account)."""
    from ..auth.users import enroll_linked_user
    cur = await conn.execute("SELECT id, capabilities FROM users WHERE oidc_iss = %s AND oidc_sub = %s",
                             (EVAL_ISS, EVAL_SUB))
    row = await cur.fetchone()
    if row:
        if "admin" in (row[1] or []):
            # An earlier eval build bound the seed admin to this identity.
            raise EvalSetupError("The eval identity is bound to an admin account; unbind it first "
                                 f"(users where oidc_iss = '{EVAL_ISS}').")
        return str(row[0])
    try:
        user = await enroll_linked_user(conn, iss=EVAL_ISS, sub=EVAL_SUB, email="doc-qa-eval@example.com",
                                       display_name="Document QA eval", capabilities=["chat", "reader"])
    except ValueError as e:
        raise EvalSetupError("Another user already has doc-qa-eval@example.com.") from e
    return str(user["id"])


async def _turn(text: str, *, session_id: str, user_id: str, model: str, doc_id: str, think: str,
                router, cfg, budget) -> tuple[str, list[dict[str, Any]], str | None, bool]:
    """One chat turn through the real path: (answer, tool calls, error, prefetched)."""
    from contextlib import aclosing

    from ..chat import store
    from ..chat.orchestrator import TurnRequest, run_turn
    from ..llm.types import CallSettings

    answer, calls, error, prefetched = "", [], None, False
    claim = await store.begin_turn(session_id=session_id, user_id=user_id, model_id=model,
                                   text=text, attachments=[], new_session_pins=None)
    req = TurnRequest(user_id=user_id, session_id=session_id, model_id=model, text=text,
                      attachments=(), settings=CallSettings(think=think), doc_id=doc_id, timezone="UTC")
    async with aclosing(run_turn(req, claim, router=router, cfg=cfg, deployment_budget=budget)) as events:
        async for ev in events:
            if ev["type"] == "text-delta":
                answer += ev["delta"]
            elif ev["type"] == "tool-input-available":
                calls.append(ev)
            elif ev["type"] == "data-context":
                prefetched = prefetched or any(i.get("kind") == "prefetch" for i in ev.get("items") or [])
            elif ev["type"] == "error":
                error = ev.get("code") or "error"
    return answer, calls, error, prefetched


async def _ask(case: Case, *, web_offered: bool, **turn) -> Result:
    """Ask the case's setup turns, then its question, in one chat; score and
    time the question's turn only."""
    import time
    import uuid

    from ..db import get_pool

    if case.kind == "live" and not web_offered:
        return score(case, "", [], web_offered=False)
    session_id = f"eval-{uuid.uuid4()}"
    try:
        for text in case.setup:
            *_, error, _ = await _turn(text, session_id=session_id, **turn)
            if error:
                return score(case, "", [], error=error)
        started = time.monotonic()
        answer, calls, error, prefetched = await _turn(case.question, session_id=session_id, **turn)
        seconds = time.monotonic() - started
    finally:
        # Even on an error or Ctrl-C: the eval leaves no chats behind.
        async with get_pool().connection() as conn:
            await conn.execute("DELETE FROM chat_sessions WHERE id = %s", (session_id,))
    result = score(case, answer, calls, error=error, prefetched=prefetched, web_offered=web_offered)
    result.seconds = seconds
    return result


async def run_eval(model: str, *, show_answers: bool = False, think: str = "off",
                   repeat: int = 1) -> list[list[Result]]:
    """Index the fixture for the eval user and ask every question through
    run_turn, started and stopped as the app does. Needs DATABASE_URL, the
    embedding model and `model` reachable; raises EvalSetupError otherwise.
    The document stays indexed, so a second run doesn't re-embed it."""
    from pathlib import Path

    from ..chat import orchestrator
    from ..chat.config import get_chat_config
    from ..db import close_db, get_pool, init_db
    from ..llm.router import UnknownModel, get_router, start_router, stop_router
    from ..services import doc_pipeline, doc_storage, embeddings, model_router, web_search

    fixture = build_fixture()
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
            user_id = await eval_user(conn)
            path = Path(doc_storage.storage_dir()) / f"{fixture.doc_id}.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(fixture.text, encoding="utf-8")
            await conn.execute(
                "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, state, bytes_path, "
                "extracted_by) VALUES (%s, %s, 'text', %s, 'stored', %s, 'server') ON CONFLICT DO NOTHING",
                (fixture.doc_id, FILE_NAME, path.stat().st_size, str(path)))
            await conn.execute(
                "INSERT INTO library_entries (user_id, doc_id, added_via, verified, file_name) "
                "VALUES (%s, %s, 'upload', true, %s) ON CONFLICT DO NOTHING",
                (user_id, fixture.doc_id, FILE_NAME))
        await doc_pipeline.run_pipeline(fixture.doc_id)     # a no-op once indexed
        await doc_pipeline.run_rebuild(fixture.doc_id)      # a no-op under the current profile
        async with get_pool().connection() as conn:
            cur = await conn.execute("SELECT state, error_message FROM documents WHERE doc_id = %s",
                                     (fixture.doc_id,))
            state, message = await cur.fetchone()
        if state != "indexed":
            raise EvalSetupError(f"The fixture didn't index ({state}: {message}). Is the embedding model up?")

        from ..chat.tools import web_search as web_search_tool
        web_offered = web_search_tool.TOOL.available(None)
        runs = []
        for _ in range(repeat):
            results = []
            for case in fixture.cases:
                result = await _ask(case, web_offered=web_offered, user_id=user_id, model=canonical,
                                    doc_id=fixture.doc_id, think=think, router=router, cfg=get_chat_config(),
                                    budget=model_router.get_config().daily_token_budget)
                results.append(result)
                if show_answers:
                    print(f"--- {case.kind}: {case.question}\n{result.answer}\n")
            runs.append(results)
        return runs
    finally:
        await stop_router()
        await web_search.stop_client()
        await embeddings.stop_client()
        await orchestrator.drain_background(timeout=5)
        await close_db()
