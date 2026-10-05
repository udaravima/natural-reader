"""v2.3 Task G: the document-QA evaluation harness. The live run needs
Postgres, an embedding model and a chat model (scripts/eval_doc_qa.py); the
fixture and the scoring are pure, and tested here."""
from __future__ import annotations

import pytest

from server.evals import doc_qa
from server.services import extract


def test_the_fixture_plants_every_fact_on_the_page_it_reports():
    fixture = doc_qa.build_fixture()
    pages = {c.page: c.text for c in extract.extract_text(fixture.text).chunks}
    assert len(pages) >= 8                                         # long enough that pages matter
    for case in fixture.cases:
        for fact, page in zip(case.facts, case.pages):
            assert fact in pages[page], (case.name, fact, page)


def test_the_fixture_covers_each_journey_and_is_the_same_every_time():
    a, b = doc_qa.build_fixture(), doc_qa.build_fixture()
    assert a.text == b.text and a.doc_id == b.doc_id
    kinds = {c.kind for c in a.cases}
    assert kinds == {"single-hop", "two-hop", "exact-label", "page-read", "absent"}
    two_hop = next(c for c in a.cases if c.kind == "two-hop")
    assert len(set(two_hop.pages)) == 2 and abs(two_hop.pages[0] - two_hop.pages[1]) >= 3
    absent = next(c for c in a.cases if c.kind == "absent")
    assert not absent.pages and not any(t.lower() in a.text.lower() for t in absent.expect)


@pytest.mark.parametrize("answer,pages", [
    ("It is BLUE HERON (page 4).", {4}),
    ("See page 2 and (Page 11); also page 0.", {2, 11}),
    ("No citation here.", set()),
])
def test_cited_pages_reads_what_the_app_links(answer, pages):
    assert doc_qa.cited_pages(answer) == pages


@pytest.mark.parametrize("answer,refused", [
    ("The document doesn't seem to cover the 2030 capital budget.", True),
    ("I couldn't find anything about that in the document.", True),
    ("The report does not mention a capital budget.", True),
    ("The capital budget is 4 million (page 3).", False),
])
def test_a_refusal_is_recognised(answer, refused):
    assert doc_qa.is_refusal(answer) == refused


def _case(kind, expect, pages):
    return doc_qa.Case(name="c", kind=kind, question="q", facts=("f",) * len(pages), pages=tuple(pages),
                       expect=tuple(expect))


def test_a_fact_case_needs_the_fact_and_every_page():
    case = _case("two-hop", ["zephyr-9", "aldermoor"], [3, 9])
    ok = doc_qa.score(case, "It uses ZEPHYR-9 (page 3), collected by Aldermoor (page 9).", [])
    assert ok.passed and ok.fact and ok.cited
    no_page = doc_qa.score(case, "It uses ZEPHYR-9 (page 3), collected by Aldermoor.", [])
    assert no_page.fact and not no_page.cited and not no_page.passed
    wrong = doc_qa.score(case, "It uses MIMIC (page 3) (page 9).", [])
    assert not wrong.fact and not wrong.passed


def test_an_absent_case_needs_a_refusal_and_no_invented_page():
    case = _case("absent", ["capital budget"], [])
    assert doc_qa.score(case, "The document doesn't seem to cover that.", []).passed
    invented = doc_qa.score(case, "The document doesn't seem to cover it, but see (page 4).", [])
    assert not invented.passed and invented.invented_pages == {4}
    assert not doc_qa.score(case, "It is 3 million.", []).passed


def test_the_score_records_tools_and_rounds():
    calls = [{"toolName": "search_documents", "round": 1}, {"toolName": "read_document_pages", "round": 2},
             {"toolName": "search_documents", "round": 2}]
    result = doc_qa.score(_case("single-hop", ["x"], [1]), "x (page 1)", calls)
    assert result.tools == ["search_documents", "read_document_pages", "search_documents"]
    assert result.rounds == 2


def test_the_report_is_one_line_per_case_and_a_total():
    case = _case("single-hop", ["x"], [1])
    results = [doc_qa.score(case, "x (page 1)", []), doc_qa.score(case, "nope", [])]
    report = doc_qa.format_report(results, model="ollama:llama3.2:3b")
    lines = report.splitlines()
    assert "ollama:llama3.2:3b" in lines[0]
    assert sum("PASS" in l for l in lines) == 1 and sum("FAIL" in l for l in lines) == 1
    assert lines[-1].startswith("1/2 passed")


# ---- Task G review fix round 1 ----

async def test_the_eval_user_is_its_own_row_and_never_claims_the_seed_admin(db_conn):
    """Review C1: the OIDC resolver binds a new identity to an unclaimed seed
    admin. On a dev-bypass machine (the one this eval is for) that is the
    person's own admin account."""
    from server.auth.users import SEED_ADMIN_ID
    cur = await db_conn.execute("SELECT oidc_sub, email FROM users WHERE id = %s", (SEED_ADMIN_ID,))
    before = await cur.fetchone()
    first = await doc_qa.eval_user(db_conn)
    again = await doc_qa.eval_user(db_conn)
    assert first == again != SEED_ADMIN_ID
    cur = await db_conn.execute("SELECT oidc_sub, email FROM users WHERE id = %s", (SEED_ADMIN_ID,))
    assert await cur.fetchone() == before
    cur = await db_conn.execute("SELECT capabilities, status FROM users WHERE id = %s", (first,))
    caps, status = await cur.fetchone()
    assert "admin" not in caps and status == "active"


@pytest.mark.parametrize("answer", [
    "The capital budget for 2030 isn't mentioned in the document.",
    "The document does not provide a 2030 capital budget.",
    "The report doesn't specify any capital budget.",
    "There is nothing about a capital budget in it.",
    "The report says nothing about it.",
    "I don't have information on that from this document.",
])
def test_refusals_in_the_models_own_words_are_recognised(answer):
    """Review I4."""
    assert doc_qa.is_refusal(answer)


@pytest.mark.parametrize("answer", [
    "Not mentioned in the text, but it is 4 million.",
    "The budget is 4m; earlier drafts did not include it.",
])
def test_a_refusal_followed_by_a_made_up_figure_fails(answer):
    case = _case("absent", ["capital budget", "2030"], [])
    assert not doc_qa.score(case, answer, []).passed


@pytest.mark.parametrize("term,answer,found", [
    ("41 ms|41ms|41 milliseconds", "a median of 41 ms", True),
    ("41 ms|41ms|41 milliseconds", "a median of 410 ms", False),
    ("41 ms|41ms|41 milliseconds", "a median of 141 ms", False),
    ("nine weeks|9 weeks|nine-week", "It ran for 9 weeks.", True),
    ("zephyr-9", "the ZEPHYR‑9 dataset", True),                   # a non-breaking hyphen
])
def test_facts_match_whole_words_and_any_hyphen(term, answer, found):
    """Review M5."""
    assert doc_qa.has_term(answer, term) == found


def test_a_turn_that_errored_is_reported_as_an_error_not_a_model_failure():
    """Review M8."""
    case = _case("single-hop", ["x"], [1])
    result = doc_qa.score(case, "", [], error="provider_error")
    assert not result.passed and result.error == "provider_error"
    assert "ERROR" in doc_qa.format_report([result], model="m")


def test_the_fixture_page_size_is_the_extractors():
    from server.services import extract
    assert doc_qa.SENTENCES_PER_PAGE is extract.SENTENCES_PER_PAGE
