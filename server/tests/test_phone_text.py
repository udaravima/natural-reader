"""Talk's text rules in Python: the sentence splitter and the noise/echo/speech verdict.

The JSON cases are shared with src/lib/speech/shared.cases.test.js, so the two languages can't drift."""
import json
from pathlib import Path

import pytest

from server.speech.base import Transcript

from server.phone.sentences import Piece, SentenceSplitter
from server.phone.verdict import heard_speech, is_echo, verdict

FIXTURES = Path(__file__).parent / "fixtures" / "speech"
SENTENCE_CASES = json.loads((FIXTURES / "sentences_cases.json").read_text())
VERDICT_CASES = json.loads((FIXTURES / "verdict_cases.json").read_text())
SETTINGS = {"max_no_speech": 0.4, "min_logprob": -1.0}


@pytest.mark.parametrize("case", SENTENCE_CASES, ids=lambda c: c["name"])
def test_sentence_cases_match_talk(case):
    spec = case["input"]
    splitter = SentenceSplitter(spec["first_clause_chars"])
    got: list[Piece] = []
    for delta in spec["deltas"]:
        got += splitter.feed(delta)
    if spec["flush"]:
        got += splitter.flush()
    assert [{"text": p.text, "end": p.end} for p in got] == case["expect"]


@pytest.mark.parametrize("case", VERDICT_CASES, ids=lambda c: c["name"])
def test_verdict_cases_match_talk(case):
    spec = case["input"]
    if case["fn"] == "heard_speech":
        got = heard_speech(spec["text"], spec["no_speech"], spec["avg_logprob"], **spec["limits"])
    else:
        got = is_echo(spec["text"], spec["said"])
    assert got is case["expect"]


def test_end_step_speaks_words_before_a_tool():
    s = SentenceSplitter(30)
    assert s.feed("Let me check that.") == []
    assert [p.text for p in s.end_step()] == ["Let me check that."]


def test_end_step_gives_the_offset_and_keeps_counting_after_it():
    s = SentenceSplitter(0)
    s.feed("Let me check that.")
    assert s.end_step() == [Piece("Let me check that.", 18)]
    got = s.feed("Done now.") + s.flush()
    assert got == [Piece("Done now.", 27)]


def test_end_step_keeps_an_unfinished_clause():
    s = SentenceSplitter(30)
    s.feed("Looking at the")
    assert s.end_step() == []
    assert [p.text for p in s.feed(" file. Ok") + s.flush()] == ["Looking at the file.", "Ok"]


def test_a_step_boundary_never_fuses_two_words():
    s = SentenceSplitter(30)
    s.feed("Looking at the")
    assert s.end_step() == []
    # The next step starts with no space: the piece is spoken with one, and its end counts the raw reply only.
    assert s.feed("file. Ok") == [Piece("Looking at the file.", 19)]
    assert s.flush() == [Piece("Ok", 22)]


def test_a_step_boundary_with_a_space_already_there_gets_no_second_one():
    s = SentenceSplitter(30)
    s.feed("Looking at the ")
    s.end_step()
    assert s.feed("file. Ok") == [Piece("Looking at the file.", 20)]
    s = SentenceSplitter(30)
    s.feed("Looking at the")
    s.end_step()
    assert s.feed(" file. Ok") == [Piece("Looking at the file.", 20)]


def test_end_step_with_nothing_pending_gives_nothing():
    assert SentenceSplitter(30).end_step() == []


def test_end_step_takes_a_question_and_leaves_nothing_twice():
    s = SentenceSplitter(0)
    s.feed("Should I look?")
    assert [p.text for p in s.end_step()] == ["Should I look?"]
    assert s.end_step() == []
    assert s.flush() == []


def _t(text, no_speech=0.05, avg_logprob=-0.3):
    return Transcript(text=text, language="en", duration_s=1.0, no_speech=no_speech, avg_logprob=avg_logprob)


def test_verdict_is_noise_echo_or_speech():
    said = ["The capital of France is Paris."]
    assert verdict(_t("Thank you.", no_speech=0.9), said, SETTINGS) == "noise"
    assert verdict(_t("the capital of France"), said, SETTINGS) == "echo"
    assert verdict(_t("what about Germany then"), said, SETTINGS) == "speech"


def test_noise_wins_over_echo():
    assert verdict(_t("the capital of France", no_speech=0.9), ["The capital of France."], SETTINGS) == "noise"
