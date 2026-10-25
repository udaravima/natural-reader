"""v2.4 Task D: which pages an answer drew on, so the app can show them as
source chips even when the model doesn't cite them. C2.1 Task 15: the same by
document, from `(Name, page N)` citations, and the web pages an answer read."""
import pytest

from server.chat.sources import answer_sources, cited_pages, sources_by_document, web_sources

EV = [(3, "The method in chapter 3 uses the ZEPHYR-9 dataset."),
      (8, "The ZEPHYR-9 dataset was collected by the Aldermoor Institute."),
      (5, "Section 2 reviews training in the context of the wider programme.")]


def test_pages_the_answer_drew_on_without_citing_them():
    pages, kind = answer_sources("It uses the ZEPHYR-9 dataset, collected by the Aldermoor Institute.", EV)
    assert (pages, kind) == ([3, 8], "used")


def test_inline_citations_count_only_for_pages_it_was_given():
    assert answer_sources("See (page 8) and page 40.", EV) == ([8], "used")


def test_boilerplate_on_many_pages_marks_none_of_them():
    """Review focus 4: wording the document repeats on page after page is no
    evidence of which page an answer used."""
    filler = "Readers interested in staffing should compare this with section 4."
    ev = [(2, filler), (4, filler), (6, filler), (9, "The codename is BLUE HERON.")]
    assert answer_sources("Readers interested in staffing should compare this with section 4.", ev)[1] == "retrieved"


def test_runs_of_common_words_alone_dont_make_a_source():
    ev = [(5, "It is one of the best of all the ones that there are.")]
    assert answer_sources("It is one of the best answers I know.", ev)[1] == "retrieved"


def test_a_refusal_lists_what_was_searched():
    assert answer_sources("The document doesn't seem to cover that.", EV) == ([3, 5, 8], "retrieved")


def test_no_evidence_no_sources():
    assert answer_sources("Hello!", []) == ([], "retrieved")


def test_text_without_a_page_is_never_a_source():
    assert answer_sources("It uses the ZEPHYR-9 dataset.", [(None, "The method uses the ZEPHYR-9 dataset.")]) == \
        ([], "retrieved")


# ---- C2.1 Task 15: citations and sources by document (spec 5.7) ----

NAMES = ["Guide.md", "Guide.md (2)", "Notes, v2 (final).md", "Tour.md"]


def test_cited_pages_handles_commas_and_parentheses_in_names():
    answer = "Set it up (Notes, v2 (final).md, page 3), then start it (Tour.md, page 1)."
    assert cited_pages(answer, NAMES) == {"Notes, v2 (final).md": {3}, "Tour.md": {1}}


def test_cited_pages_prefers_the_longest_name():
    """`Guide.md` is the front of `Guide.md (2)`: the citation belongs to the one it names."""
    assert cited_pages("(Guide.md (2), page 5)", NAMES) == {"Guide.md (2)": {5}}
    assert cited_pages("(Guide.md, page 4) and (Guide.md (2), page 5)", NAMES) == \
        {"Guide.md": {4}, "Guide.md (2)": {5}}


def test_cited_pages_returns_the_canonical_name_whatever_the_case():
    assert cited_pages("(GUIDE.md, PAGE 2) and (tour.MD, Page 7)", NAMES) == {"Guide.md": {2}, "Tour.md": {7}}


def test_cited_pages_reads_joined_citations():
    """The model sometimes joins two documents in one pair of parentheses."""
    assert cited_pages("(Guide.md, page 3; Tour.md, page 1)", NAMES) == {"Guide.md": {3}, "Tour.md": {1}}
    assert cited_pages("(Guide.md, page 3;Tour.md, page 1 ; Guide.md (2), page 9)", NAMES) == \
        {"Guide.md": {3}, "Tour.md": {1}, "Guide.md (2)": {9}}
    assert cited_pages("(Guide.md, page 3; Guide.md, page 5)", NAMES) == {"Guide.md": {3, 5}}


@pytest.mark.parametrize("citation, want", [
    ("(Guide.md, page 4)", {4}),
    ("(Guide.md, p. 4)", {4}),
    ("(Guide.md, pages 4-5)", {4, 5}),
    ("(Guide.md, pages 4 – 6)", {4, 5, 6}),               # an en dash, spaces round it
    ("(Guide.md, pp. 2-4)", {2, 3, 4}),
    ("(Guide.md, pages 4 and 6)", {4, 6}),
    ("(Guide.md, pages 4, 6 and 8)", {4, 6, 8}),
    ("(Guide.md, pages 4, 6, and 8)", {4, 6, 8}),
    ("(Guide.md, pp. 3-4 & 9)", {3, 4, 9}),
    ("(Guide.md, pages 1-10)", set(range(1, 11))),         # ten pages: all of them
    ("(Guide.md, pages 1-11)", {1, 11}),                   # past ten, only the two the model named
    ("(Guide.md, pages 6-6)", {6}),
    ("(Guide.md, pages 5-4)", {4, 5}),                     # not a range, but both are named
])
def test_cited_pages_reads_page_ranges_and_lists(citation, want):
    assert cited_pages(citation, NAMES) == {"Guide.md": want}


@pytest.mark.parametrize("text", [
    "(Guide.md, page 0)", "(Guide.md, page)", "(Guide.md)", "Guide.md, page 4", "(Guide.md, page 4, line 5)",
    "(Guide.md, page 1234567)",                              # not a page number
    "(Old Guide.md, page 3)",                                # a longer name than any in the scope
    "(Guide.md, page 4",
])
def test_cited_pages_ignores_what_is_not_a_citation_of_a_known_name(text):
    assert cited_pages(text, NAMES) == {}


def test_cited_pages_ignores_unknown_names_and_keeps_the_known_ones_beside_them():
    assert cited_pages("(Other.md, page 3)", NAMES) == {}
    assert cited_pages("(Other.md, page 3; Tour.md, page 2; Third.md, page 1)", NAMES) == {"Tour.md": {2}}
    assert cited_pages("(Guide.md, page 3)", []) == {}


@pytest.mark.parametrize("text, want", [
    # The five strings the review of Task 15 probed: one bad item beside a good citation never costs the good one.
    ("(Guide.md, page 3;)", {"Guide.md": {3}}),
    ("(Guide.md, page 3; Tour.md, page 1;)", {"Guide.md": {3}, "Tour.md": {1}}),
    ("(Guide.md, page 3;; Tour.md, page 1)", {"Guide.md": {3}, "Tour.md": {1}}),
    ("(Guide.md, page 3; see (fig. 2))", {"Guide.md": {3}}),
    ("(Guide.md, page 3; Other (v2).md, page 2)", {"Guide.md": {3}}),
    # The page word needs a space (or a "." for p.), and a list holding page 0 is no citation at all.
    ("(Guide.md, page3)", {}),
    ("(Guide.md, pages3)", {}),
    ("(Guide.md, pages 0-3)", {}),
    ("(Guide.md, pages 0 and 4)", {}),
    ("(Guide.md, pages 4 and 0)", {}),
    ("(Guide.md, pages 3-0)", {"Guide.md": {3}}),      # only the first number of an item must be a page
    ("(Guide.md, p.4)", {"Guide.md": {4}}),
    ("(Guide.md, pp.4-5)", {"Guide.md": {4, 5}}),
    ("( Guide.md , page 3 )", {"Guide.md": {3}}),
    ("x; Guide.md, page 3)", {"Guide.md": {3}}),         # a part sits after "(" OR ";" and before ";" or ")"
    ("(Guide.md, page 4, line 5)", {}),
])
def test_cited_pages_counts_exactly_what_the_client_links(text, want):
    """Server/client parity (ruled in fix round 1 of Task 15): a part is counted when the client links it. Both
    read a part as NAME, comma, a page form, sitting after "(" or ";" and before ";" or ")" (cited_pages here,
    `named` in src/lib/citations.js). Every row was checked against `remarkCitations` when it was added.
    The client's own tests pin the same grammar in src/lib/citations.test.js: "links the known parts of a joined
    citation and leaves an unknown name's part as text", "does not link page 0", "links a long or backwards range at
    its first page too" (pages 0-3) and "does not link a page word inside another word". The review's five strings,
    "page3" and "pages 0 and 4" are not in that file yet; src/ was another task's tree when this was written."""
    assert cited_pages(text, NAMES) == want


@pytest.mark.parametrize("answer", ["Look at (page3).", "Look at (pages 0).", "Look at (pages 0 and 4)."])
def test_a_bare_page_needs_the_page_word_spaced_and_no_page_zero(answer):
    ev = [(G, 3, "Alpha text one."), (G, 4, "Beta text two.")]
    [out] = sources_by_document(answer, ev, DOCS)
    assert out["used"] is False


def test_cited_pages_is_not_slowed_by_fifty_long_names_and_a_dense_answer():
    """Fifty names of 300 characters that share a 299-character front, against an answer with a delimiter every 300
    characters, each followed by the shared front: every delimiter makes every name match almost to its end."""
    import time

    front = "A" * 299
    names = [f"{front}{i}" for i in range(50)]
    for answer in (("(" + front + ";") * 400, ("(" + front) * 400, (";" + front) * 400, "(" + ";" * 100000,
                   "(" * 100000, ("( " * 50 + front) * 200):
        started = time.perf_counter()
        cited_pages(answer, names)
        assert time.perf_counter() - started < 2


@pytest.mark.parametrize("shape", [
    "(" + "Guide.md, page 3; " * 4000,                    # a list that never closes
    "(" + "Guide.md, page 3; " * 4000 + ")",              # ... or ends on a stray ";"
    "(Guide.md, page 3; " * 4000,                         # many openers
    "(" + " " * 20000, "(  " * 8000, "(" + "a;" * 8000, "(a (b (c (d " * 3000, "(" + "word " * 8000,
    "(Guide.md, pages " + "1, " * 4000 + "x)", "(Guide.md, pages " + "1-" * 4000, "(Guide.md, pages " + "1 and " * 4000,
])
def test_cited_pages_is_not_slowed_by_a_long_malformed_answer(shape):
    """A model's text is not ours to trust: a pathological answer must not hold the server."""
    import time

    started = time.perf_counter()
    cited_pages(shape, NAMES)
    assert time.perf_counter() - started < 2


G, T, N = "g" * 64, "t" * 64, "n" * 64
DOCS = [(G, "Guide.md"), (T, "Tour.md"), (N, "Notes, v2 (final).md"), ("x" * 64, "Empty.md")]
EVIDENCE = [(G, 3, "The method in chapter 3 uses the ZEPHYR-9 dataset."),
            (G, 8, "Closing remarks on staffing and the wider programme."),
            (G, 9, "Appendix tables for the yearly budget."),
            (T, 2, "The ZEPHYR-9 dataset was collected by the Aldermoor Institute."),
            (N, 5, "Section 2 reviews training in the context of the wider programme.")]


def test_sources_by_document_groups_pages():
    answer = ("It uses the ZEPHYR-9 dataset (Guide.md, page 3), which was collected by the Aldermoor Institute. "
              "Staffing is covered at the end (Guide.md, page 8) and (Guide.md, page 40).")
    assert sources_by_document(answer, EVIDENCE, DOCS) == [
        # cited: 3 and 8 (40 was never given); page 9 was only searched, so it isn't listed beside them
        {"docId": G, "docName": "Guide.md", "pages": [3, 8], "used": True},
        # not cited at all, but its text shares a distinctive run with the answer
        {"docId": T, "docName": "Tour.md", "pages": [2], "used": True},
        # nothing of it matched: the pages it was given, as searched
        {"docId": N, "docName": "Notes, v2 (final).md", "pages": [5], "used": False}]
    # a document it was not given any page of has no entry, and the entries follow the scope's order


def test_sources_by_document_orders_by_the_scope_not_by_the_evidence():
    ev = [(T, 2, "The ZEPHYR-9 dataset was collected by the Aldermoor Institute."),
          (G, 3, "The method in chapter 3 uses the ZEPHYR-9 dataset.")]
    out = sources_by_document("It uses the ZEPHYR-9 dataset (Tour.md, page 2) (Guide.md, page 3).", ev, DOCS)
    assert [s["docName"] for s in out] == ["Guide.md", "Tour.md"]


def test_a_refusal_lists_what_was_searched_by_document():
    out = sources_by_document("The documents don't seem to cover that.", EVIDENCE, DOCS)
    assert [(s["docName"], s["pages"], s["used"]) for s in out] == [
        ("Guide.md", [3, 8, 9], False), ("Tour.md", [2], False), ("Notes, v2 (final).md", [5], False)]


def test_a_citation_of_an_unknown_document_makes_no_chip():
    out = sources_by_document("It is in (Other.md, page 3) and (Guide.md (3), page 8).", EVIDENCE, DOCS)
    assert {s["docName"] for s in out} <= {"Guide.md", "Tour.md", "Notes, v2 (final).md"}
    assert not any(s["used"] for s in out)          # neither name is in the scope: nothing was cited


def test_a_known_name_beside_an_unknown_one_in_the_same_parentheses_still_counts():
    out = sources_by_document("See (Other.md, page 3; Tour.md, page 2).", EVIDENCE, DOCS)
    assert {s["docName"]: s["used"] for s in out} == {"Guide.md": False, "Tour.md": True, "Notes, v2 (final).md": False}


def test_a_citation_of_a_longer_name_that_gave_no_evidence_never_credits_the_shorter_one():
    """Only the names of documents with evidence are read, so "Guide.md (2)" (no evidence) is not in the pattern;
    "Guide.md" must still not claim its citation."""
    docs = [(G, "Guide.md"), ("h" * 64, "Guide.md (2)")]
    ev = [(G, 3, "Alpha text one."), (G, 5, "Beta text two.")]
    [out] = sources_by_document("It is in (Guide.md (2), page 5).", ev, docs)
    assert (out["docName"], out["pages"], out["used"]) == ("Guide.md", [3, 5], False)
    [out] = sources_by_document("It is in (Guide.md, page 5).", ev, docs)
    assert (out["pages"], out["used"]) == ([5], True)


def test_text_without_a_page_is_never_a_source_by_document():
    assert sources_by_document("It uses the ZEPHYR-9 dataset.", [(G, None, "It uses the ZEPHYR-9 dataset.")], DOCS) == []
    assert sources_by_document("Hello!", [], DOCS) == []


def test_evidence_of_a_document_outside_the_scope_is_ignored():
    assert sources_by_document("(Guide.md, page 1)", [("z" * 64, 1, "text")], DOCS) == []


def test_runs_found_on_many_documents_pages_mark_none_of_them():
    """The boilerplate rule counts the pages of the whole project: a running header on every document's page
    says nothing about which one the answer used."""
    filler = "Readers interested in staffing should compare this with section 4."
    ev = [(G, 1, filler), (T, 1, filler), (N, 1, filler)]
    out = sources_by_document(filler, ev, DOCS)
    assert [s["used"] for s in out] == [False, False, False]


def test_bare_page_counts_only_with_one_document_in_evidence():
    one = [(G, 3, "Alpha text one."), (G, 8, "Beta text two.")]
    two = [*one, (T, 8, "Gamma text three.")]
    answer = "It is described near the end (page 8)."
    assert sources_by_document(answer, one, DOCS) == [{"docId": G, "docName": "Guide.md", "pages": [8], "used": True}]
    out = sources_by_document(answer, two, DOCS)
    assert [(s["docName"], s["pages"], s["used"]) for s in out] == [("Guide.md", [3, 8], False), ("Tour.md", [8], False)]
    # a bare page with a name in front is that document's, not the lone document's
    assert [(s["pages"], s["used"]) for s in sources_by_document("(Tour.md, page 8)", one, DOCS)] == [([3, 8], False)]


def test_a_bare_page_range_counts_for_the_one_document():
    ev = [(G, p, f"Text of chapter {p}.") for p in (3, 4, 5, 9)]
    [out] = sources_by_document("It spans (pages 3-5).", ev, DOCS)
    assert (out["pages"], out["used"]) == ([3, 4, 5], True)


def test_text_that_cites_in_prose_is_not_a_bare_page():
    """Only "(page N)" is read: prose such as "on page 8 of the guide" has no document to belong to."""
    ev = [(G, 8, "Alpha text one."), (G, 9, "Beta text two.")]
    [out] = sources_by_document("It is on page 8 of the guide.", ev, DOCS)
    assert out["used"] is False


# ---- web sources ----

def _hit(n, url, title="A page", read=True):
    return {"title": title, "url": url, "site": "a.test", "query": "q", "read": read}


def test_a_web_source_is_a_result_the_reader_got_facts_from_in_the_order_of_its_number():
    results = {3: _hit(3, "https://c.test/x", "Third"), 1: _hit(1, "https://a.test/x", "First"),
               2: _hit(2, "https://b.test/x", "Second", read=False)}
    assert web_sources(results) == [{"n": 1, "title": "First", "url": "https://a.test/x"},
                                    {"n": 3, "title": "Third", "url": "https://c.test/x"}]
    assert web_sources({}) == []
    assert web_sources({1: {"title": "t", "url": "https://a.test"}}) == []         # never marked


@pytest.mark.parametrize("url", ["javascript:alert(1)", "JavaScript:alert(1)", " javascript:alert(1)",
                                 "data:text/html,<script>1</script>", "file:///etc/passwd", "ftp://a.test/x",
                                 "//a.test/x", "a.test/x", "vbscript:x", "", None, 7, "mailto:a@b.test"])
def test_a_url_that_is_not_http_or_https_is_never_a_web_source(url):
    assert web_sources({1: _hit(1, url)}) == []


def test_http_and_https_are_accepted_whatever_their_case():
    results = {1: _hit(1, "HTTPS://A.test/x"), 2: _hit(2, "http://b.test/x"), 3: _hit(3, "Http://c.test/x")}
    assert [p["n"] for p in web_sources(results)] == [1, 2, 3]


def test_a_web_source_title_is_one_inert_line():
    [page] = web_sources({1: _hit(1, "https://a.test/x", 'Evil <b>page</b>\n"quoted"\x00 ' + "x" * 300)})
    assert "\n" not in page["title"] and "<" not in page["title"] and '"' not in page["title"]
    assert page["title"].startswith("Evil ‹b›page‹/b›") and len(page["title"]) <= 130


def test_a_web_source_without_a_title_is_named_by_its_url():
    [page] = web_sources({1: {"url": "https://a.test/x", "read": True}})
    assert page["title"] == "https://a.test/x"
