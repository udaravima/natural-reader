"""Round 2 of the call benchmark: keying digits on the phone's keypad instead of saying them (spec
2026-11-17-call-bench-round2)."""
import numpy as np
import pytest

from server.calls.keypad import KEYPAD_RULES, dtmf, keying_s, keypad_tool
from server.calls.scenario import load_scenario

HOTEL = load_scenario("hotel")


def test_keying_takes_a_second_to_start_and_0_4_s_a_key_including_hash():
    assert keying_s("0771234567") == pytest.approx(1.0 + 0.4 * 11)
    assert keying_s("") == pytest.approx(1.4)
    assert keying_s("12", start_s=0.5, per_key_s=0.3) == pytest.approx(0.5 + 0.9)


def _energy(samples, rate, hz):
    t = np.arange(len(samples)) / rate
    return abs(np.dot(samples, np.exp(-2j * np.pi * hz * t))) / len(samples)


def test_each_key_is_its_two_tones_and_the_clip_lasts_the_keying_time():
    rate = 16000
    audio = dtmf("1#", rate)
    assert len(audio) == round(keying_s("1#") * rate)
    first = audio[int(1.0 * rate):int(1.12 * rate)]   # the "1" key: 697 Hz and 1209 Hz
    assert _energy(first, rate, 697) > 10 * _energy(first, rate, 941)
    assert _energy(first, rate, 1209) > 10 * _energy(first, rate, 1477)
    assert not audio[:int(0.9 * rate)].any()   # silence while the caller gets ready
    hash_key = audio[int(1.8 * rate):int(1.92 * rate)]   # "#": 941 Hz and 1477 Hz
    assert _energy(hash_key, rate, 941) > 10 * _energy(hash_key, rate, 697)
    assert np.abs(audio).max() <= 1.0


def test_collect_digits_returns_what_the_caller_keyed():
    tool = keypad_tool({"phone": "0771234567"})
    assert tool.name == "collect_digits"
    assert tool.fn({}, {"what": "phone"}) == {"what": "phone", "digits": "0771234567"}


def test_collect_digits_for_something_the_caller_has_not_got_is_an_empty_answer_not_a_crash():
    result = keypad_tool({}).fn({}, {"what": "reference"})
    assert result["digits"] == "" and result["note"]


def test_the_hotel_accepts_a_keyed_reference_without_its_letters():
    tools = HOTEL.toolset((keypad_tool({"reference": "10421"}),))
    assert tools.run("collect_digits", {"what": "reference"})["digits"] == "10421"
    assert tools.run("find_booking", {"reference": "10421"})["bookings"][0]["reference"] == "LB-10421"
    assert tools.run("cancel_booking", {"reference": "10421"})["reference"] == "LB-10421"


def test_the_rules_name_the_tool():
    assert "collect_digits" in KEYPAD_RULES
