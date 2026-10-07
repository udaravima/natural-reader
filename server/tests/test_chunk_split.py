"""v2.3 Task E: sub-page chunks. Long chunks are split with an overlap so the
whole page is searchable (the embedding input cap used to hide the bottom
half of most PDF pages), and reading a page joins the parts back exactly."""
from __future__ import annotations

import random

import pytest

from server.chat.tools.read_document_pages import join_chunks
from server.services.extract import CONTINUATION_SUFFIX, Chunk, split_chunks

MAX, OVERLAP = 1200, 200


def _sentences(n, seed=1):
    rnd = random.Random(seed)
    words = ["alpha", "beta", "gamma", "delta", "method", "results", "table", "4.2", "MIMIC-IV", "data"]
    return " ".join(" ".join(rnd.choice(words) for _ in range(rnd.randint(4, 18))).capitalize() + "."
                    for _ in range(n))


PAGE_TEXTS = {
    "prose": _sentences(120),
    "no sentence ends": " ".join(["word"] * 900),
    "no spaces (CJK)": "文字" * 1500,
    "a markdown table": "| a | b |\n|---|---|\n" + "\n".join(f"| row {i} | value {i * 7} |" for i in range(200)),
    "one very long word": "x" * 5000,
}


def _roundtrip(parts):
    return join_chunks([(c.text, c.chunk_type.endswith(CONTINUATION_SUFFIX)) for c in parts])


@pytest.mark.parametrize("kind", list(PAGE_TEXTS))
def test_a_long_page_splits_into_parts_that_join_back_exactly(kind):
    text = PAGE_TEXTS[kind]
    parts = split_chunks([Chunk(0, 7, "page", text)], MAX, OVERLAP)
    assert len(parts) > 1
    assert all(len(p.text) <= MAX for p in parts)
    assert all(p.page == 7 for p in parts)                            # the reader's page is kept
    assert parts[0].chunk_type == "page"
    assert all(p.chunk_type == "page" + CONTINUATION_SUFFIX for p in parts[1:])
    assert _roundtrip(parts) == text


def test_each_part_starts_with_the_end_of_the_one_before():
    parts = split_chunks([Chunk(0, 1, "page", PAGE_TEXTS["prose"])], MAX, OVERLAP)
    for prev, part in zip(parts, parts[1:]):
        overlap = next(n for n in range(OVERLAP, 15, -1) if prev.text.endswith(part.text[:n]))
        assert 16 <= overlap <= OVERLAP
        assert not part.text[0].isspace() and not prev.text[-1].isspace()


def test_prose_parts_end_on_a_sentence():
    parts = split_chunks([Chunk(0, 1, "page", PAGE_TEXTS["prose"])], MAX, OVERLAP)
    assert all(p.text.endswith(".") for p in parts)


def test_short_chunks_are_unchanged_and_ords_run_in_order():
    chunks = [Chunk(0, 1, "block", "Short one."), Chunk(1, 1, "block", PAGE_TEXTS["prose"]),
              Chunk(2, 2, "page-md", "Short two.")]
    parts = split_chunks(chunks, MAX, OVERLAP)
    assert parts[0] == Chunk(0, 1, "block", "Short one.")
    assert parts[-1].text == "Short two." and parts[-1].chunk_type == "page-md" and parts[-1].page == 2
    assert [p.ord for p in parts] == list(range(len(parts)))


def test_splitting_twice_changes_nothing():
    once = split_chunks([Chunk(0, 1, "page", PAGE_TEXTS["prose"])], MAX, OVERLAP)
    assert split_chunks(once, MAX, OVERLAP) == once


def test_the_settings_are_validated(monkeypatch):
    from server.services import extract
    assert extract.chunk_settings({}) == (1200, 200)
    assert extract.chunk_settings({"CHUNK_MAX_CHARS": "800", "CHUNK_OVERLAP_CHARS": "100"}) == (800, 100)
    # Overlap must leave room to move forward, and be long enough to find again.
    assert extract.chunk_settings({"CHUNK_MAX_CHARS": "800", "CHUNK_OVERLAP_CHARS": "500"}) == (800, 200)
    assert extract.chunk_settings({"CHUNK_OVERLAP_CHARS": "5"}) == (1200, 200)
    assert extract.chunk_settings({"CHUNK_MAX_CHARS": "junk"}) == (1200, 200)
