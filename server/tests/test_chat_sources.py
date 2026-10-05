"""v2.4 Task D: which pages an answer drew on, so the app can show them as
source chips even when the model doesn't cite them."""
from server.chat.sources import answer_sources

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
