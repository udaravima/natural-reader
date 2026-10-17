"""C2.1 Task 1: the project-chat evaluation's fixture, cases and scoring. The
live run (Task 4) needs Postgres, the embedding model and a chat model; the
parts here are pure."""
from __future__ import annotations

import pytest

from server.evals import project_qa as pq
from server.evals.doc_qa import has_term


def _case(kind):
    return next(c for c in pq.CASES if c.kind == kind)


def _fixture_text(*names):
    return "\n".join((pq.FIXTURE_DIR / n).read_text(encoding="utf-8") for n in names)


def test_fixture_is_frozen_and_complete():
    names = {p.name for p in pq.fixture_files()}
    assert {"README.md", "DEPLOYMENT.md", "USER_GUIDE.md", "TESTING.md"} <= names
    assert (pq.FIXTURE_DIR / "SOURCE.txt").read_text().strip()


def test_fixture_is_the_nine_documents_of_the_worked_example():
    """Spec section 1: README, DEPLOYMENT, USER_GUIDE, ARCHITECTURE, IDENTITY_AND_ROLES, LIBRARY,
    CHAT_WITH_PDF, TESTING and docs/README (named DOCS_INDEX.md: it would collide with README.md)."""
    names = [p.name for p in pq.fixture_files()]
    assert sorted(names) == sorted({"README.md", "DOCS_INDEX.md", "DEPLOYMENT.md", "USER_GUIDE.md",
                                    "ARCHITECTURE.md", "IDENTITY_AND_ROLES.md", "LIBRARY.md",
                                    "CHAT_WITH_PDF.md", "TESTING.md"})
    assert "04e9633" in (pq.FIXTURE_DIR / "SOURCE.txt").read_text()


def test_padding_docs_are_deterministic_and_distinct():
    a, b = pq.padding_docs(), pq.padding_docs()
    assert a == b and len(a) == 30 and len({n for n, _ in a}) == 30


def test_padding_docs_are_small_markdown_unrelated_to_the_fixture():
    docs = pq.padding_docs()
    fixture_names = {p.name for p in pq.fixture_files()}
    assert not {n for n, _ in docs} & fixture_names
    for name, text in docs:
        assert name.endswith(".md") and text.startswith("# ") and text.count("\n## ") >= 2
        assert len(text) < 4000
    # No padding document can answer a case: not one has every expected term of any case.
    for case in pq.CASES:
        if case.expect:
            assert not any(all(has_term(t, term) for term in case.expect) for _, t in docs), case.name


def test_padding_docs_differ_with_the_seed_and_honour_n():
    assert pq.padding_docs(seed=8) != pq.padding_docs()
    assert len(pq.padding_docs(5)) == 5 and pq.padding_docs(5) == pq.padding_docs()[:5]


def test_cases_cover_every_kind():
    kinds = {c.kind for c in pq.CASES}
    assert {"overview", "procedure", "code", "exact-label", "named-doc", "balance",
            "gap-general", "live", "map-shortened"} <= kinds


def test_cases_are_well_formed():
    assert 12 <= len(pq.CASES) <= 16
    assert len({c.name for c in pq.CASES}) == len(pq.CASES)
    fixture_names = {p.name for p in pq.fixture_files()}
    for c in pq.CASES:
        assert c.question.strip() and c.web == "ask", c.name
        assert set(c.docs) | set(c.forbid_docs) <= fixture_names, c.name
        assert not set(c.docs) & set(c.forbid_docs), c.name
    assert all(c.forbid_docs for c in pq.CASES if c.kind == "named-doc")
    assert all(c.docs for c in pq.CASES if c.kind in {"procedure", "named-doc", "balance"})


def test_expected_terms_are_copied_from_the_frozen_fixture():
    """Never invented: a term is found in the documents the case says hold the answer (or, with none named,
    anywhere in the fixture). The gap case is the opposite: its outside term is absent from every document."""
    everything = _fixture_text(*(p.name for p in pq.fixture_files()))
    for c in pq.CASES:
        if c.kind == "gap-general":
            assert not has_term(everything, c.expect[0]), c.name
            continue
        where = _fixture_text(*c.docs) if c.docs else everything
        for term in c.expect:
            assert has_term(where, term), (c.name, term)


def test_the_code_cases_expect_exact_commands_from_the_fixture():
    start = next(c for c in pq.CASES if c.name == "start-command")
    assert start.expect == ("./startup.sh start",)
    tests = next(c for c in pq.CASES if c.name == "test-command")
    assert "python -m pytest server/tests" in tests.expect


def test_map_shortened_cases_repeat_an_overview_and_a_code_question():
    shortened = [c for c in pq.CASES if c.kind == "map-shortened"]
    others = {c.question: c for c in pq.CASES if c.kind != "map-shortened"}
    assert {c.question for c in shortened} <= others.keys()
    assert {others[c.question].kind for c in shortened} == {"overview", "code"}
    for c in shortened:
        twin = others[c.question]
        assert (c.expect, c.docs, c.forbid_docs) == (twin.expect, twin.docs, twin.forbid_docs)


def test_cited_docs_reads_names_with_commas_and_parentheses():
    names = ["Report (final), v2.pdf", "README.md"]
    got = pq.cited_docs("See (Report (final), v2.pdf, page 2) and (readme.md, page 1).", names)
    assert got == {"Report (final), v2.pdf": {2}, "README.md": {1}}


def test_cited_docs_ignores_what_the_app_would_not_link():
    names = ["README.md", "DOCS_INDEX.md"]
    assert pq.cited_docs("(page 3) and (README.md) and (README.md, page 0) and (other.md, page 2).", names) == {}
    assert pq.cited_docs("(DOCS_INDEX.md, page 1) (DOCS_INDEX.md, page 4)", names) == {"DOCS_INDEX.md": {1, 4}}
    # A longer name that ends like a shorter one is not mistaken for it.
    assert pq.cited_docs("(docs README.md, page 1)", ["README.md", "docs README.md"]) == {"docs README.md": {1}}


def test_a_wrong_document_citation_fails_the_case():
    case = next(c for c in pq.CASES if c.kind == "named-doc")
    r = pq.score(case, f"... ({case.forbid_docs[0]}, page 1)", [], ["README.md", *case.docs, *case.forbid_docs],
                 offered=False)
    assert not r.passed and r.wrong_docs


def test_live_passes_only_with_an_offer_and_no_web_call():
    case = next(c for c in pq.CASES if c.kind == "live")
    assert pq.score(case, "I can search the web for that.", [], [], offered=True).passed
    assert not pq.score(case, "x", [{"name": "web_search"}], [], offered=False).passed


def test_gap_general_fails_when_it_gives_a_page_for_rhel():
    case = next(c for c in pq.CASES if c.kind == "gap-general")
    bad = "The documents cover RHEL: install python3.13 with dnf (DEPLOYMENT.md, page 2)."
    assert not pq.score(case, bad, [], ["DEPLOYMENT.md"], offered=True).passed


# ---- scoring, kind by kind ----

NAMES = [p.name for p in pq.fixture_files()]


def _score(case, answer, calls=(), **kw):
    kw.setdefault("offered", False)
    return pq.score(case, answer, list(calls), NAMES, **kw)


def test_an_answer_with_the_facts_and_every_named_document_passes():
    case = next(c for c in pq.CASES if c.name == "deploy-and-test")
    good = ("Deploy with ./startup.sh start (DEPLOYMENT.md, page 1), then run the backend tests with "
            ".venv/bin/python -m pytest server/tests/ -q (TESTING.md, page 1).")
    r = _score(case, good, [{"toolName": "extract_from_documents", "round": 2}])
    assert r.passed and r.fact and r.cited and not r.wrong_docs
    assert r.cited_docs == {"DEPLOYMENT.md", "TESTING.md"}
    assert r.tools == ["extract_from_documents"] and r.rounds == 2


@pytest.mark.parametrize("answer", [
    "Run ./startup.sh start, then pytest server/tests (DEPLOYMENT.md, page 1).",     # TESTING.md not cited
    "Run ./startup.sh up, then pytest server/tests (DEPLOYMENT.md, page 1) (TESTING.md, page 1).",   # wrong command
    "Run ./startup.sh start (DEPLOYMENT.md, page 1) (TESTING.md, page 1).",          # no test command
    "Run ./startup.sh start and pytest server/tests (page 1).",                      # no document named
])
def test_a_missing_fact_or_document_fails_the_case(answer):
    assert not _score(next(c for c in pq.CASES if c.name == "deploy-and-test"), answer).passed


def test_an_unlisted_document_is_not_wrong_unless_the_case_forbids_it():
    case = next(c for c in pq.CASES if c.name == "deploy-and-test")
    r = _score(case, "./startup.sh start (DEPLOYMENT.md, page 1); pytest server/tests (TESTING.md, page 1) "
                     "(README.md, page 9).")
    assert r.passed and not r.wrong_docs


def test_a_padding_document_is_always_a_wrong_document():
    case = next(c for c in pq.CASES if c.kind == "code")
    pad = pq.padding_docs()[0][0]
    answer = f"./startup.sh start ({pad}, page 1)."
    r = pq.score(case, answer, [], [*NAMES, pad], offered=False)
    assert r.wrong_docs == {pad} and not r.passed
    # ... even when the runner's names leave it out.
    assert pq.score(case, answer, [], NAMES, offered=False).wrong_docs == {pad}


def test_named_doc_passes_when_only_that_document_is_cited():
    case = next(c for c in pq.CASES if c.name == "user-guide-pins")
    ok = _score(case, "Pins you added still go with each message (USER_GUIDE.md, page 3).")
    assert ok.passed
    both = _score(case, "Pins go with every message (USER_GUIDE.md, page 3) and persist (README.md, page 1).")
    assert not both.passed and both.wrong_docs == {"README.md"}


def test_balance_needs_the_short_document_cited():
    case = _case("balance")
    assert case.docs == ("DOCS_INDEX.md",)
    long_only = _score(case, "See HANDOVER (README.md, page 1).")
    assert not long_only.passed and not long_only.cited


def test_the_gap_case_wants_rhel_a_not_covered_phrase_and_no_page_for_rhel():
    case = _case("gap-general")
    good = ("The documents don't cover RHEL. They require Python 3.12 to 3.13 and Node 20.19 or newer "
            "(README.md, page 5). From general knowledge, not from the documents: on RHEL 9 install "
            "python3.12 with dnf.")
    assert _score(case, good, offered=True).passed
    assert not _score(case, "Install python3.12 with dnf.", offered=True).passed                  # no RHEL, no refusal
    assert not _score(case, "On RHEL 9, install python3.12 with dnf.", offered=True).passed       # no refusal
    paged = ("The documents don't cover RHEL 9 (README.md, page 5) but need Python 3.12.")
    r = _score(case, paged, offered=True)
    assert not r.passed and r.paged_gap and r.refused
    # A page in a sentence that doesn't mention RHEL is fine, even right after one that does.
    split = "The documents don't cover RHEL.\nThey need Python 3.12 (README.md, page 5)."
    assert _score(case, split, offered=True).passed


def test_a_citation_with_a_dot_inside_the_name_does_not_split_the_rhel_sentence():
    case = _case("gap-general")
    names = ["Report v2. final.md"]
    answer = "(Report v2. final.md, page 1) has nothing for RHEL."
    assert pq.score(case, answer, [], names, offered=True).paged_gap


def test_the_offer_is_recorded_for_the_gap_case_and_reported():
    case = _case("gap-general")
    answer = "The documents don't cover RHEL. Shall I search the web?"
    r = _score(case, answer, offered=True)
    assert r.offered and "offered" in pq.format_report([[r]], model="m", variant="9")


def test_live_fails_with_an_offer_but_a_web_call_and_counts_the_calls():
    case = _case("live")
    calls = [{"toolName": "web_search", "round": 1}, {"toolName": "read_web_pages", "round": 2}]
    r = _score(case, "It is 0.12.", calls, offered=True)
    assert not r.passed and r.web_calls == 2
    assert not _score(case, "I don't know.", offered=False).passed


def test_a_turn_that_errored_is_an_error_not_a_model_failure():
    r = _score(_case("code"), "", error="provider_error")
    assert not r.passed and r.error == "provider_error"
    report = pq.format_report([[r]], model="m", variant="9")
    assert "ERROR" in report and "0/1 passed" in report


def test_the_report_has_a_line_per_case_a_citation_rate_and_a_total():
    code = _case("code")
    ok = _score(code, "./startup.sh start (DEPLOYMENT.md, page 1).")
    bad = _score(code, "Use docker.")
    ok.seconds = 2.5
    report = pq.format_report([[ok, bad]], model="ollama:gemma4:31b-cloud", variant="50")
    lines = report.splitlines()
    assert "ollama:gemma4:31b-cloud" in lines[0] and "50" in lines[0]
    assert sum(l.startswith("PASS") for l in lines) == 1 and sum(l.startswith("FAIL") for l in lines) == 1
    assert "2.5s" in report and "web calls 0" in report
    assert lines[-1].startswith("1/2 passed")


def test_the_report_tallies_repeats_by_kind():
    code = _case("code")
    runs = [[_score(code, "./startup.sh start")], [_score(code, "nope")]]
    assert "code 1/2" in pq.format_report(runs, model="m", variant="9")
