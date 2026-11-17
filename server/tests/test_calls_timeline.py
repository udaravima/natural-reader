"""Round 2 of the call benchmark: what a caller hears in one turn, and when, with recorded fillers, "still checking"
lines and keypad digits (spec 2026-11-17-call-bench-round2). Times are seconds from the caller's last word."""
import numpy as np
import pytest

from server.calls.matrix import compose
from server.calls.timeline import render, turn_timeline


def step(end_s, tools=(), words=False, keyed=()):
    return {"end_s": end_s, "tools": list(tools), "words": words, "keyed": [list(k) for k in keyed]}


FILLER = lambda tool: (f"filler for {tool}", 1.0)   # noqa: E731
STILL = lambda: ("still checking", 1.0)              # noqa: E731
PROMPT = lambda what: (f"key your {what}", 3.0)      # noqa: E731


def test_a_turn_without_a_tool_sounds_as_round_one_composed_it():
    pieces = [{"ready_s": 0.8}, {"ready_s": 1.0}]
    made = [(0.5, 2.0), (0.5, 1.0)]
    t = turn_timeline(wait_s=0.7, hearing_s=1.0, steps=[step(1.0, words=True)], pieces=pieces, made=made,
                      parallel=1, filler=FILLER, still=STILL)
    [old] = compose(hearing={("c", 1): 1.0}, turns=[{"call": "c", "turn": 1, "pieces": pieces}],
                    voiced={("c", 1, 1): made[0], ("c", 1, 2): made[1]}, silence_s=0.7, parallel=1)
    assert t["first_sound_s"] == pytest.approx(old["first_sound_s"]) == pytest.approx(3.0)
    assert t["first_answer_s"] == pytest.approx(3.0)
    assert [s["kind"] for s in t["sounds"]] == ["answer", "answer"]
    assert t["end_s"] == pytest.approx(6.0)
    assert t["dead_air_s"] == pytest.approx(3.0) and t["longest_silence_s"] == pytest.approx(3.0)


def test_a_filler_plays_the_moment_the_model_decides_on_a_lookup():
    t = turn_timeline(wait_s=0.7, hearing_s=1.0, steps=[step(1.2, ["check_availability"]), step(2.5, words=True)],
                      pieces=[{"ready_s": 2.0}], made=[(0.6, 1.5)], parallel=2, filler=FILLER, still=STILL)
    filler, answer = t["sounds"]
    assert filler == {"start_s": pytest.approx(2.9), "end_s": pytest.approx(3.9), "kind": "filler",
                      "text": "filler for check_availability"}
    assert answer["start_s"] == pytest.approx(4.3) and t["first_sound_s"] == pytest.approx(2.9)
    assert t["first_answer_s"] == pytest.approx(4.3)
    assert t["dead_air_s"] == pytest.approx(5.8 - 1.0 - 1.5)


def test_the_answer_waits_for_the_filler_to_finish():
    t = turn_timeline(wait_s=0.0, hearing_s=0.0, steps=[step(1.0, ["get_hotel_info"]), step(1.3, words=True)],
                      pieces=[{"ready_s": 1.2}], made=[(0.1, 1.0)], parallel=1, filler=FILLER, still=STILL)
    assert t["first_answer_s"] == pytest.approx(2.0)


def test_a_long_lookup_gets_one_still_checking_line_after_1_5_s_of_silence():
    t = turn_timeline(wait_s=0.0, hearing_s=0.0, steps=[step(1.0, ["check_availability"]), step(5.6, words=True)],
                      pieces=[{"ready_s": 5.5}], made=[(0.5, 1.0)], parallel=2, filler=FILLER, still=STILL)
    assert [(s["kind"], round(s["start_s"], 2)) for s in t["sounds"]] == [("filler", 1.0), ("still", 3.5),
                                                                            ("answer", 6.0)]


def test_a_turn_left_silent_after_a_tool_has_its_filler_and_no_still_lines_and_no_answer():
    t = turn_timeline(wait_s=0.7, hearing_s=1.0, steps=[step(1.0, ["create_booking"]), step(9.0)], pieces=[],
                      made=[], parallel=2, filler=FILLER, still=STILL)
    assert [s["kind"] for s in t["sounds"]] == ["filler"]
    assert t["first_answer_s"] is None and t["first_sound_s"] == pytest.approx(2.7)


def test_nothing_plays_without_fillers_so_the_first_sound_is_the_answer():
    t = turn_timeline(wait_s=0.7, hearing_s=1.0, steps=[step(1.2, ["check_availability"]), step(2.5, words=True)],
                      pieces=[{"ready_s": 2.0}], made=[(0.6, 1.5)], parallel=2)
    assert t["first_sound_s"] == t["first_answer_s"] == pytest.approx(4.3)


def test_keyed_digits_play_the_instruction_then_the_keying_shifts_the_rest_of_the_turn():
    t = turn_timeline(wait_s=0.7, hearing_s=1.0,
                      steps=[step(0.5, ["collect_digits"], keyed=[("phone", "0771234567")]), step(1.5, words=True)],
                      pieces=[{"ready_s": 1.2}], made=[(0.4, 1.0)], parallel=2, filler=FILLER, still=STILL,
                      prompt=PROMPT)
    prompt, answer = t["sounds"]
    assert prompt["kind"] == "prompt" and prompt["start_s"] == pytest.approx(2.2)
    assert t["keying"] == [{"start_s": pytest.approx(5.2), "end_s": pytest.approx(10.6), "digits": "0771234567"}]
    assert answer["start_s"] == pytest.approx(1.7 + 1.2 + 8.4 + 0.4)
    assert t["dead_air_s"] == pytest.approx(answer["end_s"] - 3.0 - 1.0 - 5.4)   # keying is the caller's time


def test_render_places_each_sound_and_the_keypad_tones():
    rate = 1000
    t = turn_timeline(wait_s=0.5, hearing_s=0.5,
                      steps=[step(0.0, ["collect_digits"], keyed=[("reference", "1")]), step(0.5, words=True)],
                      pieces=[{"ready_s": 0.2}], made=[(0.1, 0.5)], parallel=1, prompt=lambda what: ("p", 1.0))
    audio = render(rate, t, lambda sound: np.full(int(round((sound["end_s"] - sound["start_s"]) * rate)), 0.5,
                                                  dtype=np.float32))
    assert len(audio) >= round(t["end_s"] * rate)
    assert not audio[:1000].any() and audio[1000:2000].min() == 0.5   # silence, then the instruction
    keying = t["keying"][0]
    assert audio[int(keying["start_s"] * rate):int(keying["end_s"] * rate)].any()   # the tones
