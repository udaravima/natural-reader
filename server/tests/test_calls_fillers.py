"""Round 2 of the call benchmark: recorded filler lines and keypad digits in a scenario pack, and the filler book
that picks a line per tool (spec 2026-11-17-call-bench-round2)."""
import re
from pathlib import Path

import pytest

from server.calls.scenario import ScenarioError, load_scenario

HOTEL = Path(__file__).resolve().parents[1] / "calls" / "scenarios" / "hotel"


@pytest.fixture
def hotel():
    return load_scenario("hotel")


def _pack(tmp_path: Path, old: str, new: str) -> str:
    for name in ("data.json", "tools.py"):
        (tmp_path / name).write_text((HOTEL / name).read_text())
    text = (HOTEL / "scenario.toml").read_text()
    assert old in text
    (tmp_path / "scenario.toml").write_text(text.replace(old, new, 1))
    return str(tmp_path)


def test_every_hotel_tool_has_a_filler_and_there_are_two_still_checking_lines(hotel):
    tools = {spec.name for spec in hotel.toolset().specs()}
    assert set(hotel.agent.fillers) - {""} == tools
    assert hotel.agent.fillers[""] == (hotel.agent.filler,)
    assert all(lines and all(line.strip() for line in lines) for lines in hotel.agent.fillers.values())
    assert len(hotel.agent.still_checking) == 2
    assert set(hotel.agent.keypad) == {"phone", "reference"}
    assert all("hash" in line for line in hotel.agent.keypad.values())


def test_a_keypad_call_keys_its_number_instead_of_saying_it(hotel):
    book = hotel.call("book-deluxe")
    assert book.keys == {"phone": "0771234567"}
    assert book.lines(False) == book.caller
    keyed = book.lines(True)
    assert len(keyed) == len(book.caller) and "Kasun Perera" in keyed[2]
    assert not re.search(r"\b(zero|one|two|three|four|five|six|seven|eight|nine)\b", keyed[2].lower())
    spelled = hotel.call("spelled-name")
    assert spelled.keys == {"phone": "0715552468"} and len(spelled.lines(True)) == 3
    cancel = hotel.call("cancel-booking")
    assert cancel.keys == {"reference": "10421"} and "one zero" not in cancel.lines(True)[0].lower()


def test_a_call_without_keypad_lines_says_the_same_either_way(hotel):
    questions = hotel.call("hotel-questions")
    assert questions.caller_keypad is None and questions.keys == {}
    assert questions.lines(True) == questions.lines(False) == questions.caller


def test_a_filler_for_a_tool_the_pack_lacks_is_refused(tmp_path):
    with pytest.raises(ScenarioError, match="book_spa"):
        load_scenario(_pack(tmp_path, "[agent.fillers]\n", '[agent.fillers]\nbook_spa = "Let me see."\n'))


def test_keys_must_be_digits(tmp_path):
    with pytest.raises(ScenarioError, match="digits"):
        load_scenario(_pack(tmp_path, 'phone = "0771234567"', 'phone = "077-123"'))


def test_keys_need_a_keypad_line_for_what_is_keyed(tmp_path):
    with pytest.raises(ScenarioError, match="pin"):
        load_scenario(_pack(tmp_path, 'phone = "0771234567"', 'pin = "1234"'))


# ---- the filler book ----

def test_lines_never_repeat_back_to_back_across_tools_and_still_lines(hotel):
    from server.calls.fillers import FillerBook
    book = FillerBook(hotel.agent)
    picks = []
    for i in range(20):
        picks.append(book.for_tool("check_availability") if i % 3 else book.still())
    assert all(a != b for a, b in zip(picks, picks[1:]))
    assert set(picks) >= set(hotel.agent.fillers["check_availability"]) | set(hotel.agent.still_checking)


def test_a_tool_without_its_own_line_gets_the_default(hotel):
    from server.calls.fillers import FillerBook
    assert FillerBook(hotel.agent).for_tool("book_spa") == hotel.agent.filler


def test_the_book_lists_every_distinct_line_to_record(hotel):
    from server.calls.fillers import FillerBook
    lines = FillerBook(hotel.agent).lines()
    assert len(lines) == len(set(lines))
    assert hotel.agent.filler in lines and "I'll put you through now." in lines
    assert set(hotel.agent.still_checking) <= set(lines)
    assert set(hotel.agent.keypad.values()).isdisjoint(lines)


def test_a_pool_of_one_line_after_itself_still_answers(hotel):
    from server.calls.fillers import FillerBook
    book = FillerBook(hotel.agent)
    assert book.for_tool("transfer_to_human") == book.for_tool("transfer_to_human") == "I'll put you through now."
