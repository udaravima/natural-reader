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

SENTENCES_PER_PAGE = 40   # extract.SENTENCES_PER_PAGE: a text file's reader page
PAGES = 10
FILE_NAME = "Eval Report.txt"


@dataclass(frozen=True)
class Case:
    name: str
    kind: str                  # single-hop | two-hop | exact-label | page-read | absent
    question: str
    facts: tuple[str, ...]     # planted sentences
    pages: tuple[int, ...]     # the reader page of each fact
    expect: tuple[str, ...]    # terms a right answer contains (absent: terms the document lacks)


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
     [("Table 7.3 reports a median latency of 41 ms.", 6)], ("41",)),
    ("page-read", "page", "What does page 5 say about the field trial?",
     [("The field trial ran for nine weeks in Tromsø.", 5)], ("nine weeks",)),
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
_REFUSAL = re.compile(
    r"(doesn't|does not|didn't|did not) (seem to |appear to )?(cover|mention|say|contain|include|discuss)"
    r"|(couldn't|could not|can't|cannot|didn't|did not|was unable to) find"
    r"|not (mentioned|covered|found|included|stated)"
    r"|no (information|mention|details?) (about|on|of|regarding)",
    re.IGNORECASE)


def cited_pages(answer: str) -> set[int]:
    """The pages the app would link in this answer (page 0 is no page)."""
    return {int(a or b) for a, b in _CITATION.findall(answer) if int(a or b) >= 1}


def is_refusal(answer: str) -> bool:
    return bool(_REFUSAL.search(answer.replace("’", "'")))


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
    passed: bool = False


def score(case: Case, answer: str, tool_calls: list[dict[str, Any]]) -> Result:
    """`tool_calls`: the turn's tool-input-available events ({toolName, round})."""
    result = Result(case, answer, [c.get("toolName") for c in tool_calls],
                    max((c.get("round") or 0 for c in tool_calls), default=0))
    if case.kind == "absent":
        result.refused = is_refusal(answer)
        result.invented_pages = cited_pages(answer)
        result.passed = result.refused and not result.invented_pages
    else:
        result.fact = all(term.lower() in answer.lower() for term in case.expect)
        result.cited = set(case.pages) <= cited_pages(answer)
        result.passed = result.fact and result.cited
    return result


def _detail(r: Result) -> str:
    if r.case.kind == "absent":
        if r.invented_pages:
            return f"cited pages {sorted(r.invented_pages)} for a fact the document lacks"
        return "refused" if r.refused else "answered instead of saying the document doesn't cover it"
    fact = "fact ok" if r.fact else f"fact missing ({', '.join(r.case.expect)})"
    pages = "pages ok" if r.cited else f"pages missing (want {', '.join(map(str, r.case.pages))})"
    return f"{fact}; {pages}"


def format_report(results: list[Result], *, model: str) -> str:
    lines = [f"Document QA eval: {model}"]
    for r in results:
        tools = ",".join(r.tools) or "-"
        lines.append(f"{'PASS' if r.passed else 'FAIL'}  {r.case.kind:<12} rounds={r.rounds} "
                     f"tools={tools}  {_detail(r)}")
    lines.append(f"{sum(r.passed for r in results)}/{len(results)} passed")
    return "\n".join(lines)


# ---------- the live run ----------

async def run_eval(model: str, *, show_answers: bool = False) -> list[Result]:
    """Index the fixture for an eval user and ask every question through
    run_turn. Needs DATABASE_URL, the embedding model and `model` reachable.
    The eval user's chats are deleted afterwards; the document stays indexed
    so a second run doesn't re-embed it."""
    import uuid
    from pathlib import Path

    from ..auth.users import resolve_or_provision_user, set_status
    from ..chat import store
    from ..chat.config import get_chat_config
    from ..chat.orchestrator import TurnRequest, run_turn
    from ..db import close_db, get_pool, init_db
    from ..llm.router import get_router, start_router, stop_router
    from ..llm.types import CallSettings
    from ..services import doc_pipeline, doc_storage, embeddings

    fixture = build_fixture()
    await init_db()
    await embeddings.start_client()
    await start_router()
    try:
        async with get_pool().connection() as conn:
            user = await resolve_or_provision_user(conn, iss="natural-reader-eval", sub="doc-qa",
                                                   email="doc-qa-eval@example.com")
            await set_status(conn, user["id"], "active")
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
                (user["id"], fixture.doc_id, FILE_NAME))
        await doc_pipeline.run_pipeline(fixture.doc_id)     # a no-op once indexed
        await doc_pipeline.run_rebuild(fixture.doc_id)      # a no-op under the current profile

        results = []
        for case in fixture.cases:
            session_id = f"eval-{uuid.uuid4()}"
            claim = await store.begin_turn(session_id=session_id, user_id=str(user["id"]), model_id=model,
                                           text=case.question, attachments=[], new_session_pins=None)
            req = TurnRequest(user_id=str(user["id"]), session_id=session_id, model_id=model,
                              text=case.question, attachments=(), settings=CallSettings(),
                              doc_id=fixture.doc_id, timezone="UTC")
            answer, calls = "", []
            async for ev in run_turn(req, claim, router=get_router(), cfg=get_chat_config(),
                                     deployment_budget=None):
                if ev["type"] == "text-delta":
                    answer += ev["delta"]
                elif ev["type"] == "tool-input-available":
                    calls.append(ev)
                elif ev["type"] == "error":
                    answer += f"\n[error: {ev['code']}]"
            results.append(score(case, answer, calls))
            if show_answers:
                print(f"--- {case.kind}: {case.question}\n{answer}\n")
            async with get_pool().connection() as conn:
                await conn.execute("DELETE FROM chat_sessions WHERE id = %s", (session_id,))
        return results
    finally:
        await stop_router()
        await embeddings.stop_client()
        await close_db()
