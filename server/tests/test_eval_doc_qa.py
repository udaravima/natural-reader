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
