"""C2.1 Task 1: the project-chat evaluation's fixture, cases and scoring. The
live run (Task 4) needs Postgres, the embedding model and a chat model; the
parts here are pure."""
from __future__ import annotations

import re
from dataclasses import fields, replace

import pytest

from server.evals import project_qa as pq
from server.evals import doc_qa
from server.evals import web_fixture as wf
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


@pytest.mark.parametrize("name,other", [("user-guide-pins", "ARCHITECTURE.md"),
                                        ("deployment-uploads", "IDENTITY_AND_ROLES.md")])
def test_a_named_doc_case_cites_only_that_document(name, other):
    """Spec section 9: "cites only that document" - any other document is wrong, listed as forbidden or not."""
    case = next(c for c in pq.CASES if c.name == name)
    assert other not in case.forbid_docs
    own = case.docs[0]
    fact = "pins go with each message and the nginx setting client_max_body_size 100m"
    ok = _score(case, f"{fact} ({own}, page 3) ({own}, page 4).")
    assert ok.passed and not ok.wrong_docs
    mixed = _score(case, f"{fact} ({own}, page 3) ({other}, page 1).")
    assert not mixed.passed and mixed.wrong_docs == {other}
    assert mixed.fact and mixed.cited


def test_other_kinds_still_allow_an_unlisted_document():
    """Only a named-doc case reads "cites only that document"; elsewhere extra documents are fine."""
    case = next(c for c in pq.CASES if c.name == "session-lifetimes")
    r = _score(case, "168 and 12 (ARCHITECTURE.md, page 3) (USER_GUIDE.md, page 1).")
    assert r.passed and not r.wrong_docs


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


def test_the_gap_case_passes_on_a_refusal_with_or_without_the_offer_of_a_web_search():
    """Task 14 (spec 5.8): a gap is filled from labelled general knowledge OR by an offer to search the web, so
    a written refusal passes on its own; the report still says whether it offered."""
    case = _case("gap-general")
    answer = "The documents don't cover RHEL. They need Python 3.12 (README.md, page 5)."
    assert _score(case, answer, offered=True).passed
    r = _score(case, answer, offered=False)
    assert r.passed and not r.offered
    assert "no offer" in pq.format_report([[r]], model="m", variant="9")


def test_the_gap_case_passes_on_an_offer_whose_turn_wrote_no_text():
    """The offer ends the turn: a model that calls it first has said nothing yet. That is no fact to check and
    no page to invent."""
    case = _case("gap-general")
    r = _score(case, "", [{"toolName": "offer_web_search", "round": 1}], offered=True)
    assert r.passed and r.offered and not r.refused
    assert "offered the search" in pq.format_report([[r]], model="m", variant="9")
    assert _score(case, "   \n", offered=True).passed
    # Nothing at all (no refusal, no offer) is no answer.
    assert not _score(case, "", offered=False).passed
    # An offer turn that DID write something unhedged, or invented a page, is still judged by its words.
    assert not _score(case, "On RHEL 9, install python3.12 with dnf.", offered=True).passed
    paged = "The documents don't cover RHEL 9 (README.md, page 5)."
    assert not _score(case, paged, offered=True).passed
    # An offer that refuses in words needs no RHEL in them: there is no fact to give.
    assert _score(case, "The documents don't say. Shall I search the web?", offered=True).passed


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


# ---- the fields of doc_qa.Result, and the cases that can't be asked ----

def test_result_keeps_every_field_of_the_single_document_result_with_its_default():
    single, project = {f.name: f for f in fields(doc_qa.Result)}, {f.name: f for f in fields(pq.Result)}
    assert single.keys() <= project.keys()
    for name in single.keys() - {"case", "answer"}:
        assert project[name].default == single[name].default, name
        assert (project[name].default_factory is single[name].default_factory) or (
            project[name].default_factory() == single[name].default_factory()), name
    assert {"cited_docs", "wrong_docs", "web_calls", "offered"} <= project.keys()


def test_live_and_gap_cases_are_skipped_when_web_search_is_not_offered():
    for kind in ("live", "gap-general"):
        case = _case(kind)
        r = _score(case, "The documents don't cover RHEL.", web_offered=False)
        assert r.skipped == "web search not offered" and not r.passed and r.error is None
    # Nothing else needs the web.
    r = _score(_case("code"), "./startup.sh start (README.md, page 3).", web_offered=False)
    assert r.skipped is None and r.passed


def test_a_skipped_case_is_reported_as_skipped_and_left_out_of_the_totals():
    skipped = _score(_case("live"), "", web_offered=False)
    passed = _score(_case("code"), "./startup.sh start (README.md, page 3).")
    report = pq.format_report([[skipped, passed]], model="m", variant="9")
    lines = report.splitlines()
    assert any(l.startswith("SKIP") and "skipped: web search not offered" in l for l in lines)
    assert lines[-1] == "1/1 passed (1 skipped)"
    report = pq.format_report([[skipped], [skipped]], model="m", variant="9")
    assert "tally:" in report and "live" not in report.split("tally:")[1].splitlines()[0]
    assert report.splitlines()[-1] == "0/0 passed (2 skipped)"


def test_an_error_beats_a_skip():
    r = _score(_case("live"), "", error="provider_error", web_offered=False)
    assert r.error == "provider_error" and r.skipped is None


def test_a_right_answer_without_a_trail_is_marked():
    case = _case("code")
    answer = "./startup.sh start (README.md, page 3)."
    bare = _score(case, answer)
    assert bare.passed and bare.trail is False
    assert "PASS (no trail)" in pq.format_report([[bare]], model="m", variant="9")
    for r in (_score(case, answer, [{"toolName": "search_documents", "round": 1}]),
              _score(case, answer, [{"toolName": "extract_from_documents", "round": 1}]),
              _score(case, answer, prefetched=True)):
        assert r.trail is True
        assert "PASS (no trail)" not in pq.format_report([[r]], model="m", variant="9")
    # Looking only at the map is not a trail through the documents; neither does a case without documents have one.
    assert _score(case, answer, [{"toolName": "document_outline", "round": 1}]).trail is False
    assert _score(_case("live"), "I can search.", offered=True).trail is None


def test_inline_citations_chips_and_gap_pages_are_recorded():
    case = next(c for c in pq.CASES if c.name == "session-lifetimes")
    inline = _score(case, "168 and 12 (ARCHITECTURE.md, page 3).", chip_pages=[4, 3, 3])
    assert inline.cited_inline and inline.chip_pages == [3, 3, 4]
    assert not _score(case, "168 and 12 (ARCHITECTURE.md, page 4).").cited_inline
    gap = _score(_case("gap-general"), "On RHEL 9 use dnf (README.md, page 5) (DEPLOYMENT.md, page 2).", offered=True)
    assert gap.invented_pages == {2, 5} and gap.paged_gap
    assert _score(_case("gap-general"), "The documents don't cover RHEL.", offered=True).invented_pages == set()


# ---- Task 4: the runner and its script ----

def _script():
    """scripts/eval_project_qa.py as a module (it is not on the import path)."""
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "scripts" / "eval_project_qa.py"
    spec = importlib.util.spec_from_file_location("eval_project_qa_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runner_refuses_the_real_database():
    script = _script()
    with pytest.raises(SystemExit):
        script.check_database_url("postgresql://u:p@localhost:5433/natural_reader")
    script.check_database_url("postgresql://u:p@localhost:5433/natural_reader_eval")


@pytest.mark.parametrize("url", [
    None, "",                                                   # unset: the server would use its default, the real one
    "postgresql://u:p@localhost:5433/natural_reader",
    "postgresql://u:p@localhost:5433/natural_reader?sslmode=disable",
    "postgresql://u:p@localhost:5433/",                         # no database named
    "dbname=natural_reader user=u host=localhost",              # the keyword form of the same
    "not a connection string at all ::",
])
def test_the_script_refuses_anything_but_a_named_scratch_database(url, capsys):
    with pytest.raises(SystemExit) as stopped:
        _script().check_database_url(url)
    assert stopped.value.code == 2
    assert "u:p" not in capsys.readouterr().err      # the URL holds a password: never echoed


@pytest.mark.parametrize("url", ["postgresql://u:p@localhost:5434/natural_reader_eval",
                                 "dbname=natural_reader_eval host=localhost"])
def test_the_script_accepts_a_scratch_database(url):
    _script().check_database_url(url)


def test_the_eval_script_loads_dotenv_before_any_server_module():
    """As doc_qa's: server modules read settings when imported; and check_database_url needs none."""
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    probe = ("import importlib.util, sys\n"
             f"spec = importlib.util.spec_from_file_location('ev', {str(root / 'scripts' / 'eval_project_qa.py')!r})\n"
             "spec.loader.exec_module(importlib.util.module_from_spec(spec))\n"
             "print(sorted(m for m in sys.modules if m == 'server' or m.startswith('server.')))\n")
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, cwd=root, check=True)
    assert out.stdout.strip() == "[]"


def test_an_exported_database_url_wins_over_dotenv(tmp_path):
    """The baseline must never fall to .env's database: dotenv fills only what the environment lacks."""
    import os
    import subprocess
    import sys
    (tmp_path / ".env").write_text("DATABASE_URL=postgresql://u:p@h:5433/natural_reader\nOTHER_FROM_DOTENV=1\n")
    probe = ("from dotenv import load_dotenv; import os\n"
             "load_dotenv(override=False)\n"
             "print(os.environ['DATABASE_URL'], os.environ['OTHER_FROM_DOTENV'])\n")
    env = {**os.environ, "DATABASE_URL": "postgresql://u:p@h:5434/natural_reader_eval"}
    env.pop("OTHER_FROM_DOTENV", None)
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, cwd=tmp_path, env=env, check=True)
    assert out.stdout.split() == ["postgresql://u:p@h:5434/natural_reader_eval", "1"]


def test_the_nine_document_variant_skips_the_map_shortened_cases():
    nine, fifty = pq.cases_for("9"), pq.cases_for("50")
    assert not [c for c in nine if c.kind == "map-shortened"]
    assert [c for c in fifty if c.kind == "map-shortened"] and len(fifty) == len(pq.CASES)
    assert len(nine) == len(pq.CASES) - 2


def test_the_variants_hold_nine_and_fifty_documents():
    nine, fifty = pq.project_documents("9"), pq.project_documents("50")
    assert [n for n, _ in nine] == [p.name for p in pq.fixture_files()] and len(nine) == 9
    assert len(fifty) == 50 and fifty[:9] == nine
    assert len({n for n, _ in fifty}) == 50 and len({d for _, d in fifty}) == 50


async def test_the_eval_project_files_every_document_for_its_user(db_conn, tmp_path, monkeypatch):
    from server.chat.project_scope import load_project_scope
    monkeypatch.setenv("DOC_STORAGE_DIR", str(tmp_path))
    user = await doc_qa.eval_user(db_conn, sub="project-qa-test", email="pq-test@example.com")
    docs = pq.project_documents("9")
    pid, doc_ids = await pq.seed_project(db_conn, user, docs, name="Eval project")
    scope = await load_project_scope(db_conn, user, pid, open_doc_id=None)
    assert sorted(d.name for d in scope.docs) == sorted(n for n, _ in docs)
    assert sorted(d.doc.doc_id for d in scope.docs) == sorted(doc_ids)
    assert all((tmp_path / f"{i}.md").is_file() for i in doc_ids)
    # A second run (its own storage folder: the first one is gone) makes the project anew: still nine
    # documents, not eighteen, and their content points at the new folder.
    second = tmp_path / "second"
    monkeypatch.setenv("DOC_STORAGE_DIR", str(second))
    pid2, _ = await pq.seed_project(db_conn, user, docs, name="Eval project")
    cur = await db_conn.execute("SELECT bytes_path FROM documents WHERE doc_id = ANY(%s)", (doc_ids,))
    assert {r[0] for r in await cur.fetchall()} == {str(second / f"{i}.md") for i in doc_ids}
    scope = await load_project_scope(db_conn, user, pid2, open_doc_id=None)
    assert len(scope.docs) == 9 and pid2 != pid
    assert (await (await db_conn.execute("SELECT count(*) FROM projects WHERE created_by = %s", (user,))).fetchone())[0] == 1


# ---- the script's own storage folder ----

def test_the_eval_gets_a_fresh_temp_storage_folder_whatever_is_exported(monkeypatch):
    import tempfile
    from pathlib import Path
    from server.services import doc_storage
    monkeypatch.setenv("DOC_STORAGE_DIR", "/the/apps/real/storage")
    monkeypatch.setenv("PDF_STORAGE_DIR", "/the/apps/old/storage")
    with _script().eval_storage(None) as folder:
        assert folder.is_dir() and folder.name.startswith("nr-eval-storage-")
        assert folder.parent == Path(tempfile.gettempdir())
        assert doc_storage.storage_dir() == folder.resolve()      # what the pipeline will really use
        (folder / "x.md").write_text("kept until the block ends")
    assert not folder.exists()
    import os
    assert os.environ["DOC_STORAGE_DIR"] == "/the/apps/real/storage"   # the exported value is put back


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_the_temp_storage_folder_is_removed_on_an_error_or_ctrl_c(failure, monkeypatch):
    monkeypatch.delenv("DOC_STORAGE_DIR", raising=False)
    seen = []
    with pytest.raises(failure):
        with _script().eval_storage(None) as folder:
            seen.append(folder)
            (folder / "doc.md").write_text("x")
            raise failure()
    assert seen and not seen[0].exists()


def test_an_explicit_storage_dir_is_used_created_and_kept(tmp_path, monkeypatch):
    import os
    monkeypatch.setenv("DOC_STORAGE_DIR", "/the/apps/real/storage")
    wanted = tmp_path / "mine" / "docs"
    with _script().eval_storage(str(wanted)) as folder:
        assert folder == wanted.resolve() and wanted.is_dir()
        assert os.environ["DOC_STORAGE_DIR"] == str(wanted.resolve())
        (wanted / "x.md").write_text("mine")
    assert (wanted / "x.md").read_text() == "mine"           # the caller's folder is never removed


_MAIN_PROBE = """
import asyncio, dotenv, importlib.util, json, os, sys
from pathlib import Path
real = dotenv.load_dotenv
dotenv.load_dotenv = lambda *a, **k: real(Path(os.environ["PROBE_DOTENV"]), **k)    # a .env of the test's own
spec = importlib.util.spec_from_file_location("ev", os.environ["PROBE_SCRIPT"])
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)
import server.evals.project_qa as pq
from server.services import doc_storage
seen = {}
async def fake_run_eval(model, **kw):
    seen["web_fixture"] = kw.get("web_fixture")
    seen["dir"] = str(doc_storage.storage_dir())
    seen["exists"] = doc_storage.storage_dir().is_dir()
    return []
pq.run_eval = fake_run_eval
sys.argv = ["eval_project_qa.py", "--model", "m", *sys.argv[1:]]
rc = script.main()
print(json.dumps({"rc": rc, **seen, "after": os.path.isdir(seen["dir"])}))
"""


def _run_main(tmp_path, *argv):
    """The script's real main(), with a fake run_eval that records where storage points, in a subprocess
    (main loads a .env into os.environ). The test's .env and the environment both name another folder."""
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    (tmp_path / ".env").write_text("DOC_STORAGE_DIR=/from/dotenv\nPDF_STORAGE_DIR=/from/dotenv/pdfs\n")
    env = {**os.environ, "PROBE_DOTENV": str(tmp_path / ".env"), "PROBE_SCRIPT": str(root / "scripts" / "eval_project_qa.py"),
           "DATABASE_URL": "postgresql://u:p@localhost:5434/natural_reader_eval",
           "DOC_STORAGE_DIR": "/exported/by/the/caller", "PYTHONPATH": str(root)}
    out = subprocess.run([sys.executable, "-c", _MAIN_PROBE, *argv], capture_output=True, text=True, cwd=root, env=env)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_main_runs_the_eval_in_a_temp_storage_folder_and_removes_it(tmp_path):
    import tempfile
    from pathlib import Path
    seen = _run_main(tmp_path)
    assert seen["rc"] == 0 and seen["exists"]
    assert Path(seen["dir"]).parent == Path(tempfile.gettempdir()).resolve()
    assert Path(seen["dir"]).name.startswith("nr-eval-storage-")
    assert not seen["after"]
    assert seen["dir"] not in ("/exported/by/the/caller", "/from/dotenv")


def test_main_honours_storage_dir_and_keeps_it(tmp_path):
    mine = tmp_path / "kept"
    seen = _run_main(tmp_path, "--storage-dir", str(mine))
    assert seen["dir"] == str(mine.resolve()) and seen["exists"] and seen["after"]


def test_main_passes_the_web_fixture_flag_on(tmp_path):
    assert _run_main(tmp_path)["web_fixture"] is False
    assert _run_main(tmp_path, "--web-fixture")["web_fixture"] is True


# ---- Task 13: the web-read case and its fake web ----

WEB = pq.WEB_CASES[0]


def test_the_web_read_case_is_asked_only_with_the_fake_web():
    assert WEB not in pq.CASES and WEB.kind == "web-read" and WEB.web == "ask"
    assert WEB not in pq.cases_for("9") and WEB not in pq.cases_for("50")
    assert pq.cases_for("9", web_fixture=True)[-1] is WEB
    assert len(pq.cases_for("50", web_fixture=True)) == len(pq.cases_for("50")) + 1


def test_the_web_read_question_is_one_no_model_or_project_document_can_answer():
    """The answer terms and the near-miss are made up: nothing in the project's documents (or its padding) has
    them, so the answer can only come from the web."""
    assert WEB.expect == ("marrowdeck-srv", "4.18.3") and WEB.forbid_terms == ("4.17.3",)
    texts = [p.read_text(encoding="utf-8") for p in pq.fixture_files()] + [t for _, t in pq.padding_docs()]
    for term in (*WEB.expect, *WEB.forbid_terms, "marrowdeck"):
        assert not any(has_term(t, term) for t in texts), term
    assert not any(has_term(WEB.question, t) for t in (*WEB.expect, *WEB.forbid_terms))   # the question gives neither


def test_the_fake_web_lists_the_archived_page_first_and_hides_the_answer_from_the_snippets():
    pages = wf.pages()
    assert [p.url for p in pages][:2] == ["https://docs.marrowdeck.example/4.17/install",
                                          "https://docs.marrowdeck.example/4.18/install"]
    snippets = " ".join(p.snippet for p in pages)
    assert "4.17.3" in snippets                       # the near-miss is tempting: it is in a snippet
    assert not has_term(snippets, "4.18.3")            # the answer is not: the page has to be read


def test_the_answer_is_on_the_current_page_past_6000_characters_and_the_near_miss_on_the_other():
    current = wf.page_text("https://docs.marrowdeck.example/4.18/install")
    archived = wf.page_text("https://docs.marrowdeck.example/4.17/install")
    assert len(current) > 7000
    assert current.index("marrowdeck-srv") > 6000 and current.index("4.18.3") > 6000
    assert "4.17.3" not in current and "4.18.3" not in archived
    assert has_term(archived, "4.17.3") and has_term(archived, "marrowdeck-srv")
    # Every page is HTML the extractor reads (a page that extracts to nothing would be an unreadable fetch).
    assert all(wf.page_text(p.url) for p in wf.pages())
    assert wf.page_text("https://elsewhere.example/") is None


async def test_the_fake_web_replaces_the_search_and_the_fetch_in_this_process_only_while_served():
    from server.chat.tools import read_web_pages
    from server.services import web_search
    real = web_search.searxng_search, read_web_pages.fetch_page_text
    with wf.served() as log:
        found = await web_search.web_search("anything at all", 2)
        text = await read_web_pages.fetch_page_text("https://docs.marrowdeck.example/4.18/install")
        assert await read_web_pages.fetch_page_text("https://nowhere.example/") is None
    assert [r["url"] for r in found["results"]] == [p.url for p in wf.pages()[:2]]
    assert found["results"][0]["site"] == "docs.marrowdeck.example" and "4.18.3" in text
    assert log.queries == ["anything at all"]
    assert log.fetched == ["https://docs.marrowdeck.example/4.18/install", "https://nowhere.example/"]
    assert (web_search.searxng_search, read_web_pages.fetch_page_text) == real           # put back


def test_web_read_passes_on_the_facts_and_a_near_miss_fails_it():
    right = "Install marrowdeck-srv; the minimum version is 4.18.3."
    assert _score(WEB, right, offered=True).passed
    assert _score(WEB, right, offered=False).passed                 # the offer is recorded, not scored
    assert not _score(WEB, "Install marrowdeck-srv, version 4.17.3 or newer.", offered=True).passed
    assert _score(WEB, "Install marrowdeck-srv, version 4.17.3 or newer.", offered=True).near_miss
    both = _score(WEB, right + " (The archived guide says 4.17.3.)", offered=True)
    assert both.fact and both.near_miss and not both.passed
    assert not _score(WEB, "Install marrowdeck-srv.", offered=True).passed
    assert not _score(WEB, "I could not find it.", offered=True).fact


def test_web_read_is_skipped_without_web_search_and_reported_with_its_offer():
    assert _score(WEB, "", web_offered=False).skipped == "web search not offered"
    ok = _score(WEB, "marrowdeck-srv 4.18.3", [{"toolName": "web_search", "round": 0},
                                              {"toolName": "read_web_pages", "round": 1}], offered=True)
    miss = _score(WEB, "marrowdeck-srv 4.17.3", offered=False)
    line = pq.format_report([[ok, miss]], model="m", variant="9").splitlines()
    assert "offered the search; fact ok; near-miss avoided; 2 web calls" in line[1]
    assert line[1].startswith("PASS") and line[2].startswith("FAIL")
    assert "no offer; fact missing (marrowdeck-srv, 4.18.3); gave the near-miss (4.17.3)" in line[2]


class _Pool:
    """get_pool() for _ask, which deletes its chat afterwards."""
    def connection(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def conn():
            class C:
                async def execute(self, *a, **k):
                    return None
            yield C()
        return conn()


def _fake_turns(monkeypatch, script):
    """_turn replaced by a script of Turn results; the calls it was given are returned."""
    import server.db
    seen = []

    async def fake(text, **kw):
        seen.append({"text": text, **kw})
        return script.pop(0)

    monkeypatch.setattr(pq, "_turn", fake)
    monkeypatch.setattr(server.db, "get_pool", lambda: _Pool())
    return seen


async def test_web_read_asks_the_question_then_sends_the_offered_query_as_the_users_yes(monkeypatch):
    seen = _fake_turns(monkeypatch, [
        pq.Turn(answer="Not in the documents.", offered=True, offer_query="marrowdeck rocky 9 package"),
        pq.Turn(answer="Install marrowdeck-srv, minimum 4.18.3.",
                calls=[{"toolName": "web_search", "round": 0}, {"toolName": "read_web_pages", "round": 1}])])
    r = await pq._ask(WEB, names=NAMES, web_offered=True, user_id="u", project_id="p", model="m", think="off",
                      router=None, cfg=None, budget=None)
    assert [t["text"] for t in seen] == [WEB.question, "Search the web for: marrowdeck rocky 9 package"]
    assert "web_query" not in seen[0] and seen[1]["web_query"] == "marrowdeck rocky 9 package"
    assert r.passed and r.offered and r.web_calls == 2 and r.answer.startswith("Install")


async def test_web_read_without_an_offer_sends_the_question_itself_and_records_no_offer(monkeypatch):
    seen = _fake_turns(monkeypatch, [pq.Turn(answer="Probably 4.17.3."),
                                     pq.Turn(answer="marrowdeck-srv 4.18.3")])
    r = await pq._ask(WEB, names=NAMES, web_offered=True, user_id="u", project_id="p", model="m", think="off",
                      router=None, cfg=None, budget=None)
    assert seen[1]["web_query"] == WEB.question and not r.offered
    assert r.passed                                        # scored on the second turn's answer alone


async def test_web_read_stops_when_the_first_turn_failed(monkeypatch):
    seen = _fake_turns(monkeypatch, [pq.Turn(error="provider_error")])
    r = await pq._ask(WEB, names=NAMES, web_offered=True, user_id="u", project_id="p", model="m", think="off",
                      router=None, cfg=None, budget=None)
    assert len(seen) == 1 and r.error == "provider_error" and not r.passed


async def test_each_case_runs_in_its_own_web_mode_and_the_environment_is_put_back(monkeypatch):
    import os
    monkeypatch.setenv("CHAT_WEB_SEARCH", "auto")
    modes = []

    async def fake(text, **kw):
        modes.append(os.environ["CHAT_WEB_SEARCH"])
        return pq.Turn(answer="x")

    import server.db
    monkeypatch.setattr(pq, "_turn", fake)
    monkeypatch.setattr(server.db, "get_pool", lambda: _Pool())
    await pq._ask(replace(_case("code"), web="off"), names=NAMES, web_offered=True, user_id="u", project_id="p",
                  model="m", think="off", router=None, cfg=None, budget=None)
    assert modes == ["off"] and os.environ["CHAT_WEB_SEARCH"] == "auto"
    monkeypatch.delenv("CHAT_WEB_SEARCH")
    await pq._ask(_case("code"), names=NAMES, web_offered=True, user_id="u", project_id="p", model="m",
                  think="off", router=None, cfg=None, budget=None)
    assert modes == ["off", "ask"] and "CHAT_WEB_SEARCH" not in os.environ
