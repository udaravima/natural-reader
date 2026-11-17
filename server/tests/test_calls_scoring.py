"""Sentence cuts, playback waits and call scoring for the call benchmark (spec 2026-11-17-call-simulation)."""
import pytest

from server.calls.playback import playback
from server.calls.scenario import CallScript, Expect, load_scenario
from server.calls.scoring import money, normalize, score_call, wer
from server.calls.sentences import SentenceSplitter
from server.calls.simtools import ToolRun


def cuts(deltas, first_clause_chars=0):
    s = SentenceSplitter(first_clause_chars)
    out = []
    for d in deltas:
        out += s.feed(d)
    return out + s.flush()


# ---- sentences: the same cuts as src/lib/speech/sentences.js ----

def test_a_sentence_is_cut_when_the_space_after_its_full_stop_arrives():
    s = SentenceSplitter(0)
    assert s.feed("Yes, we have a deluxe room.") == []
    assert s.feed(" It is") == ["Yes, we have a deluxe room."]
    assert s.flush() == ["It is"]


def test_markdown_is_dropped_and_empty_pieces_are_not_spoken():
    assert cuts(["**Sure!**\n\n", "The *total* is `84,000`. ", "...  "]) == ["Sure!", "The total is 84,000."]


def test_a_long_opening_is_cut_at_the_first_comma_past_the_limit_then_only_at_sentence_ends():
    text = "Thank you for waiting, Mr Perera, the deluxe room is free on both nights, and it is 84,000. Shall I book?"
    assert cuts([text], first_clause_chars=30) == [
        "Thank you for waiting, Mr Perera,",
        "the deluxe room is free on both nights, and it is 84,000.",
        "Shall I book?"]


def test_a_sentence_end_before_the_limit_wins_over_a_comma():
    assert cuts(["Yes. Well, that one is lovely, honestly, very lovely indeed."], first_clause_chars=30) == [
        "Yes.", "Well, that one is lovely, honestly, very lovely indeed."]


# ---- normalising what was said ----

@pytest.mark.parametrize("raw, said", [
    ("The total is LKR 84,000.", "the total is lkr 84000"),
    ("Your reference is L B one zero four five zero.", "your reference is lb 10450"),
    ("Check-in is from 2 p.m.", "check in is from 2 pm"),
    ("arriving on the twelfth", "arriving on the 12th"),
    ("from the twenty-fourth to the thirty-first", "from the 24th to the 31st"),
    ("The reference is lb10421.", "the reference is lb 10421"),
    ("That's T-H-I-L-I-N-I", "thats thilini"),
    ("Check-in is from 2:00 pm, or 10:00 AM", "check in is from 2 pm or 10 am"),
    ("You won't be charged", "you wont be charged"),
    ("077-123-4567", "0771234567"),
])
def test_normalize(raw, said):
    assert normalize(raw) == said


def test_word_error_rate_counts_substitutions_insertions_and_deletions():
    assert wer("book a deluxe room", "book a deluxe room") == 0
    assert wer("my name is Kasun Perera", "my name is Kasun Pereira") == pytest.approx(1 / 5)
    assert wer("zero seven seven one two three", "077 123") == 0
    assert wer("two nights", "") == 1
    assert wer("arriving on the twelfth of December", "arriving on the 12th of December") == 0
    assert wer("The reference is L B one zero four two one.", "The reference is lb10421.") == 0
    assert wer("", "") == 0


def test_money_finds_amounts_with_thousands_or_a_currency_but_not_phone_numbers_or_references():
    said = "That is LKR 84,000 in total, Rs 9500 for the car, 52,500 a night. Call 0771234567, reference LB-10450."
    assert money(said) == {84000, 9500, 52500}


# ---- playback: when each sentence can start, and the silences in between ----

def test_first_audio_waits_for_the_first_sentence_and_its_voice():
    p = playback(ready_s=[1.0, 1.5], synth_s=[0.5, 0.5], audio_s=[2.0, 2.0], parallel=1)
    assert p["first_audio_s"] == pytest.approx(1.5)
    assert p["stalls_s"] == 0
    assert p["finished_s"] == pytest.approx(5.5)


def test_a_voice_slower_than_speech_leaves_silences_between_sentences():
    p = playback(ready_s=[0.2, 0.4, 0.6], synth_s=[2.5, 2.5, 2.5], audio_s=[1.0, 1.0, 1.0], parallel=1)
    # one at a time: done at 2.7, 5.2, 7.7; playing 2.7-3.7, 5.2-6.2, 7.7-8.7
    assert p["stalls_s"] == pytest.approx(3.0)
    assert p["longest_stall_s"] == pytest.approx(1.5)
    assert p["gaps_s"] == [pytest.approx(1.5), pytest.approx(1.5)]
    p2 = playback(ready_s=[0.2, 0.4, 0.6], synth_s=[2.5, 2.5, 2.5], audio_s=[1.0, 1.0, 1.0], parallel=2)
    # two at a time: done at 2.7, 2.9, 5.2: playing 2.7-3.7, 3.7-4.7, 5.2-6.2
    assert p2["stalls_s"] == pytest.approx(0.5)


def test_playback_of_nothing():
    assert playback([], [], [], parallel=1) == {"first_audio_s": None, "stalls_s": 0.0, "longest_stall_s": 0.0,
                                                 "finished_s": None, "audio_s": 0.0, "gaps_s": []}


# ---- scoring a call ----

HOTEL = load_scenario("hotel")


def run(name, args, result):
    return ToolRun(name, args, result, 0.01)


def test_a_booking_call_that_did_everything_passes():
    tools = HOTEL.toolset()
    tools.run("check_availability", {"check_in": "2026-12-12", "check_out": "2026-12-14", "guests": 2,
                                     "room_type": "deluxe"})
    tools.run("create_booking", {"guest_name": "Kasun Perera", "phone": "077 123 4567", "room_type": "deluxe",
                                 "check_in": "2026-12-12", "check_out": "2026-12-14", "guests": 2})
    replies = ["The deluxe room is free, LKR 84,000 for both nights.", "Booked. Your reference is LB-10450."]
    score = score_call(HOTEL.call("book-deluxe"), replies, tools.log, tools.state, HOTEL.data)
    assert score.passed, [c for c in score.checks if not c.ok]


def test_an_invented_price_a_missing_tool_and_a_wrong_name_each_fail_and_say_why():
    tools = HOTEL.toolset()
    tools.run("create_booking", {"guest_name": "Kasun Pereira", "phone": "0771234567", "room_type": "deluxe",
                                 "check_in": "2026-12-12", "check_out": "2026-12-14", "guests": 2})
    replies = ["It is LKR 80,000 for both nights.", "Your reference is LB-10450."]
    score = score_call(HOTEL.call("book-deluxe"), replies, tools.log, tools.state, HOTEL.data)
    failed = {c.name: c.detail for c in score.checks if not c.ok}
    assert "check_availability" in failed["tools"]
    assert "80,000" in failed["money"]
    assert "guest_name" in failed["booking"] and "Kasun Pereira" in failed["booking"]
    assert "84000" in failed["says"]
    assert not score.passed


def test_derived_amounts_from_tool_results_are_not_called_invented():
    runs = [run("check_availability", {}, {"rooms": [{"total": 70000, "nightly_rates": [35000, 35000]}]})]
    script = CallScript("x", "x", ("hi",), "en", Expect())
    score = score_call(script, ["Two rooms come to LKR 140,000, or 35,000 a night each."], runs, {"bookings": []},
                       {"bookings": []})
    assert score.passed


def test_cancellation_transfer_and_never_tools_are_checked():
    tools = HOTEL.toolset()
    tools.run("create_booking", {"guest_name": "A", "phone": "0770000000", "room_type": "standard",
                                 "check_in": "2026-12-12", "check_out": "2026-12-13", "guests": 1})
    script = CallScript("x", "x", ("hi",), "en",
                        Expect(never_tools=("create_booking",), cancelled=("LB-10421",), transferred=True))
    failed = {c.name for c in score_call(script, ["ok"], tools.log, tools.state, HOTEL.data).checks if not c.ok}
    assert failed == {"never_tools", "cancelled", "transferred"}


def test_a_tool_that_answered_with_an_error_does_not_count_as_used():
    tools = HOTEL.toolset()
    tools.run("cancel_booking", {"reference": "LB-55555"})
    script = CallScript("x", "x", ("hi",), "en", Expect(tools=("cancel_booking",)))
    [check] = [c for c in score_call(script, ["ok"], tools.log, tools.state, HOTEL.data).checks if c.name == "tools"]
    assert not check.ok and "only with errors" in check.detail


def test_turns_left_without_an_answer_fail_the_call():
    script = CallScript("x", "x", ("hi", "and?"), "en", Expect())
    score = score_call(script, ["Let me check.", ""], [], {"bookings": []}, {"bookings": []}, unanswered=1)
    assert not score.passed
    assert [c.name for c in score.checks if not c.ok] == ["answered"]


def test_misheard_words_are_the_scripts_words_missing_from_what_was_heard():
    from server.calls.scoring import misheard_words
    pairs = [("My name is Kasun Perera", "My name is Kasan Perera"), ("Nimal Perera", "Nimble Parer"),
             ("Okay, thanks", "Okay, thanks")]
    assert misheard_words(pairs) == [("kasun", 1), ("nimal", 1), ("perera", 1)]
