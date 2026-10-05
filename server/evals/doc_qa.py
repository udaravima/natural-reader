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
    kind: str                  # single-hop | two-hop | exact-label | page-read | absent
    question: str
    facts: tuple[str, ...]     # planted sentences
    pages: tuple[int, ...]     # the reader page of each fact
    expect: tuple[str, ...]    # terms a right answer contains, "a|b" = either (absent: terms the document lacks)


@dataclass(frozen=True)
class Fixture:
    text: str
    doc_id: str
    cases: tuple[Case, ...]


# (kind, name, question, [(sentence, page)], expected terms)
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
]

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
    for kind, name, question, facts, expect in _PLANTED:
        for sentence, page in facts:
            # Somewhere in the middle of its page, never at a page edge.
            sentences[(page - 1) * SENTENCES_PER_PAGE + rnd.randint(5, SENTENCES_PER_PAGE - 6)] = sentence
        cases.append(Case(name, kind, question, tuple(s for s, _ in facts), tuple(p for _, p in facts), expect))
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


def score(case: Case, answer: str, tool_calls: list[dict[str, Any]], *, error: str | None = None) -> Result:
    """`tool_calls`: the turn's tool-input-available events ({toolName, round})."""
    result = Result(case, answer, [c.get("toolName") for c in tool_calls],
                    max((c.get("round") or 0 for c in tool_calls), default=0), error=error)
    if error:
        return result
    if case.kind == "absent":
        result.refused = is_refusal(answer)
        result.invented_pages = cited_pages(answer)
        result.invented_figure = _invents_a_figure(answer)
        result.passed = result.refused and not result.invented_pages and not result.invented_figure
    else:
        result.fact = all(has_term(answer, term) for term in case.expect)
        result.cited = set(case.pages) <= cited_pages(answer)
        result.passed = result.fact and result.cited
    return result


def _detail(r: Result) -> str:
    if r.error:
        return f"the turn failed ({r.error}): fix the setup, this says nothing about the model"
    if r.case.kind == "absent":
        if r.invented_pages:
            return f"cited pages {sorted(r.invented_pages)} for a fact the document lacks"
        if r.invented_figure:
            return "gave a figure the document doesn't have"
        return "refused" if r.refused else "answered instead of saying the document doesn't cover it"
    fact = "fact ok" if r.fact else f"fact missing ({', '.join(r.case.expect)})"
    pages = "pages ok" if r.cited else f"pages missing (want {', '.join(map(str, r.case.pages))})"
    return f"{fact}; {pages}"


def format_report(results: list[Result], *, model: str) -> str:
    lines = [f"Document QA eval: {model}"]
    for r in results:
        tools = ",".join(r.tools) or "-"
        verdict = "ERROR" if r.error else "PASS" if r.passed else "FAIL"
        lines.append(f"{verdict:<5} {r.case.kind:<12} rounds={r.rounds} tools={tools}  {_detail(r)}")
    lines.append(f"{sum(r.passed for r in results)}/{len(results)} passed")
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


async def _ask(case: Case, *, user_id: str, model: str, doc_id: str, router, cfg, budget) -> Result:
    import uuid
    from contextlib import aclosing

    from ..chat import store
    from ..chat.orchestrator import TurnRequest, run_turn
    from ..db import get_pool
    from ..llm.types import CallSettings

    session_id = f"eval-{uuid.uuid4()}"
    answer, calls, error = "", [], None
    try:
        claim = await store.begin_turn(session_id=session_id, user_id=user_id, model_id=model,
                                       text=case.question, attachments=[], new_session_pins=None)
        req = TurnRequest(user_id=user_id, session_id=session_id, model_id=model, text=case.question,
                          attachments=(), settings=CallSettings(), doc_id=doc_id, timezone="UTC")
        async with aclosing(run_turn(req, claim, router=router, cfg=cfg, deployment_budget=budget)) as events:
            async for ev in events:
                if ev["type"] == "text-delta":
                    answer += ev["delta"]
                elif ev["type"] == "tool-input-available":
                    calls.append(ev)
                elif ev["type"] == "error":
                    error = ev.get("code") or "error"
    finally:
        # Even on an error or Ctrl-C: the eval leaves no chats behind.
        async with get_pool().connection() as conn:
            await conn.execute("DELETE FROM chat_sessions WHERE id = %s", (session_id,))
    return score(case, answer, calls, error=error)


async def run_eval(model: str, *, show_answers: bool = False) -> list[Result]:
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

        results = []
        for case in fixture.cases:
            result = await _ask(case, user_id=user_id, model=canonical, doc_id=fixture.doc_id, router=router,
                                cfg=get_chat_config(), budget=model_router.get_config().daily_token_budget)
            results.append(result)
            if show_answers:
                print(f"--- {case.kind}: {case.question}\n{result.answer}\n")
        return results
    finally:
        await stop_router()
        await web_search.stop_client()
        await embeddings.stop_client()
        await orchestrator.drain_background(timeout=5)
        await close_db()
