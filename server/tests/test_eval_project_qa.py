"""C2.1 Task 1: the project-chat evaluation's fixture, cases and scoring. The
live run (Task 4) needs Postgres, the embedding model and a chat model; the
parts here are pure."""
from __future__ import annotations

import re
from dataclasses import replace

import pytest

from server.evals import project_qa as pq
from server.evals.doc_qa import has_term
from server.services import extract


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
    assert a == b and len(a) == 41 and len({n for n, _ in a}) == 41


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


def test_the_fixture_plus_padding_is_the_fifty_document_variant():
    assert len(pq.fixture_files()) + len(pq.padding_docs()) == 50


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
    for c in pq.CASES:
        assert {d for d, _ in c.pages} <= set(c.docs), c.name
        assert all(page >= 1 for _, page in c.pages), c.name
    assert all(c.pages for c in pq.CASES if c.kind in {"code", "exact-label"})


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


def _words(text):
    return " ".join(re.findall(r"\w+", text.lower()))


def _page_of(doc, anchor):
    """The reader page of `anchor` in a fixture document, as the extractor paginates it: the page of the
    chunk that holds it. Text only inside a fenced code block has no chunk yet (extraction skips fences until
    code chunks exist), and a fence never starts a page (it has no sentences, so the packing keeps it on the page
    of the block before it): then it is the page of the chunk holding the line before the fence."""
    path = pq.FIXTURE_DIR / doc
    chunks = extract.extract_file(path, "markdown").chunks
    pages = {c.page for c in chunks if anchor in c.text}
    if not pages:
        lines = path.read_text(encoding="utf-8").splitlines()
        inside = next(i for i, line in enumerate(lines) if anchor in line)
        opening = max(i for i in range(inside) if lines[i].lstrip().startswith("```"))
        before = _words(next(line for line in reversed(lines[:opening]) if line.strip()))
        assert before, (doc, anchor)
        pages = {c.page for c in chunks if before in _words(c.text)}
    assert len(pages) == 1, (doc, anchor, pages)     # an anchor on several pages can't name "the" page
    return pages.pop()


# The text of each answer that the page is read from: unique in its document.
_PAGE_ANCHORS = {
    "start-command": "./startup.sh start",
    "quickstart-command": "./startup.sh quickstart",
    "test-command": "python -m pytest server/tests",
    "session-lifetimes": "SESSION_TTL_HOURS=168",
    "oidc-issuer": "OIDC_ISSUER",
}


def test_expected_pages_are_where_the_extractor_puts_them():
    """Not hard-coded in the test: a drift in the fixture or in the extractor's pagination fails here."""
    with_pages = {c.name for c in pq.CASES if c.pages and c.kind != "map-shortened"}
    assert with_pages == set(_PAGE_ANCHORS)
    for c in pq.CASES:
        if c.name in _PAGE_ANCHORS:
            want = tuple((doc, _page_of(doc, _PAGE_ANCHORS[c.name])) for doc in c.docs)
            assert c.pages == want, c.name


def test_map_shortened_cases_repeat_an_overview_and_a_code_question():
    shortened = [c for c in pq.CASES if c.kind == "map-shortened"]
    others = {c.question: c for c in pq.CASES if c.kind != "map-shortened"}
    assert {c.question for c in shortened} <= others.keys()
    assert {others[c.question].kind for c in shortened} == {"overview", "code"}
    for c in shortened:
        twin = others[c.question]
        assert (c.expect, c.docs, c.forbid_docs, c.pages) == (twin.expect, twin.docs, twin.forbid_docs, twin.pages)


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


def test_the_gap_case_fails_without_the_offer_of_a_web_search():
    case = _case("gap-general")
    answer = "The documents don't cover RHEL. They need Python 3.12 (README.md, page 5)."
    assert _score(case, answer, offered=True).passed
    r = _score(case, answer, offered=False)
    assert not r.passed and not r.offered
    assert "no offer" in pq.format_report([[r]], model="m", variant="9")


def test_the_offer_is_recorded_for_the_gap_case_and_reported():
    case = _case("gap-general")
    answer = "The documents don't cover RHEL. Shall I search the web?"
    r = _score(case, answer, offered=True)
    assert r.offered and "offered" in pq.format_report([[r]], model="m", variant="9")


# ---- overview: breadth, not named documents ----

_ALL_FEATURES = ("Text-to-speech reading (README.md, page 1), chat with your documents (USER_GUIDE.md, page 2), "
                 "a library of PDF and Markdown files (CHAT_WITH_PDF.md, page 3), accounts through Keycloak, "
                 "and an admin console.")


def test_an_overview_needs_three_distinct_documents_and_enough_features():
    for name in ("features", "features-50"):          # the map-shortened twin is scored the same way
        case = next(c for c in pq.CASES if c.name == name)
        assert len(case.expect) >= 6 and not case.docs
        r = _score(case, _ALL_FEATURES)
        assert r.passed and r.fact and r.cited and r.cited_docs == {"README.md", "USER_GUIDE.md", "CHAT_WITH_PDF.md"}
        # Any three will do: not the three the author had in mind.
        other = (_ALL_FEATURES.replace("CHAT_WITH_PDF.md", "LIBRARY.md")
                 .replace("USER_GUIDE.md", "IDENTITY_AND_ROLES.md"))
        assert _score(case, other).passed
        two = _ALL_FEATURES.replace("CHAT_WITH_PDF.md", "README.md")
        r = _score(case, two)
        assert not r.passed and r.fact and not r.cited and len(r.cited_docs) == 2
        # The same document twice, or on two pages, is one document.
        assert not _score(case, _ALL_FEATURES.replace("USER_GUIDE.md", "README.md")
                          .replace("CHAT_WITH_PDF.md", "README.md")).passed


def test_an_overview_needs_two_thirds_of_its_listed_features():
    case = _case("overview")
    need = -(-2 * len(case.expect) // 3)               # ceil(2/3 x terms)
    assert need == 4
    cited = "(README.md, page 1) (USER_GUIDE.md, page 2) (CHAT_WITH_PDF.md, page 3)"
    four = f"Text-to-speech, chat, a library and admin projects {cited}."
    assert _score(case, four).passed
    three = f"Text-to-speech, chat and a library {cited}."
    r = _score(case, three)
    assert not r.passed and not r.fact and r.cited
    assert "3/6 terms (need 4)" in pq.format_report([[r]], model="m", variant="9")


def test_an_overview_fails_on_a_wrong_document_however_broad():
    case = replace(_case("overview"), forbid_docs=("LIBRARY.md",))
    r = _score(case, _ALL_FEATURES + " (LIBRARY.md, page 1)")
    assert not r.passed and r.wrong_docs == {"LIBRARY.md"}
    # A padding document is wrong for every case, the overview included.
    pad = pq.padding_docs()[0][0]
    r = pq.score(_case("overview"), _ALL_FEATURES + f" ({pad}, page 1)", [], [*NAMES, pad], offered=False)
    assert not r.passed and r.wrong_docs == {pad}


# ---- right document and page ----

def test_a_listed_page_must_be_cited_with_its_document():
    case = next(c for c in pq.CASES if c.name == "session-lifetimes")
    assert case.pages == (("ARCHITECTURE.md", 3),)
    ok = _score(case, "TTL 168 hours, idle 12 hours (ARCHITECTURE.md, page 3).")
    assert ok.passed and ok.pages_ok
    wrong_page = _score(case, "TTL 168 hours, idle 12 hours (ARCHITECTURE.md, page 4).")
    assert not wrong_page.passed and wrong_page.cited and wrong_page.pages_ok is False
    wrong_doc = _score(case, "TTL 168 hours, idle 12 hours (DEPLOYMENT.md, page 3).")
    assert not wrong_doc.passed and not wrong_doc.cited
    bare = _score(case, "TTL 168 hours, idle 12 hours (page 3).")
    assert not bare.passed
    assert "page missing (want ARCHITECTURE.md p. 3)" in pq.format_report([[wrong_page]], model="m", variant="9")


def test_one_right_page_among_several_cited_is_enough():
    case = next(c for c in pq.CASES if c.name == "test-command")
    answer = ("With Postgres up: .venv/bin/python -m pytest server/tests/ -q "
              "(TESTING.md, page 2) (TESTING.md, page 1).")
    assert _score(case, answer).passed


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
    ok = _score(code, "./startup.sh start (README.md, page 3).")
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
    runs = [[_score(code, "./startup.sh start (README.md, page 3)")], [_score(code, "nope")]]
    assert "code 1/2" in pq.format_report(runs, model="m", variant="9")
