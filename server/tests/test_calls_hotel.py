"""The hotel scenario pack and its pretend tools (spec 2026-11-17-call-simulation)."""
from pathlib import Path

import pytest

from server.calls.scenario import ScenarioError, load_scenario


@pytest.fixture
def hotel():
    return load_scenario("hotel")


@pytest.fixture
def tools(hotel):
    return hotel.toolset()


def test_the_hotel_pack_loads_with_its_agent_and_calls(hotel):
    assert hotel.id == "hotel"
    assert "Lotus Bay" in hotel.name
    assert hotel.today == "2026-12-01"
    assert hotel.agent.greeting and hotel.agent.filler and hotel.agent.rules
    assert len(hotel.calls) >= 5
    names = {spec.name for spec in hotel.toolset().specs()}
    for call in hotel.calls:
        assert call.caller, call.id
        assert set(call.expect.tools) <= names, call.id


def test_two_nights_in_a_deluxe_room_cost_two_nightly_rates(tools):
    r = tools.run("check_availability", {"check_in": "2026-12-12", "check_out": "2026-12-14", "guests": 2,
                                         "room_type": "deluxe"})
    assert r["nights"] == 2
    [room] = r["rooms"]
    assert room["available"] is True
    assert room["nightly_rates"] == [42000, 42000]
    assert room["total"] == 84000
    assert r["currency"] == "LKR"


def test_nights_in_the_peak_season_cost_more(tools):
    r = tools.run("check_availability", {"check_in": "2026-12-19", "check_out": "2026-12-21", "guests": 2,
                                         "room_type": "deluxe"})
    assert r["rooms"][0]["nightly_rates"] == [42000, 52500]
    assert r["rooms"][0]["total"] == 94500


def test_without_a_room_type_every_room_is_listed(tools):
    r = tools.run("check_availability", {"check_in": "2026-12-12", "check_out": "2026-12-13", "guests": 2})
    assert [room["room_type"] for room in r["rooms"]] == ["standard", "deluxe", "family", "executive"]


def test_the_family_suite_is_sold_out_over_christmas(tools):
    r = tools.run("check_availability", {"check_in": "2026-12-24", "check_out": "2026-12-26", "guests": 4,
                                         "room_type": "family"})
    room = r["rooms"][0]
    assert room["available"] is False
    assert "sold out" in room["reason"]
    assert "total" not in room


def test_a_room_too_small_for_the_party_is_not_available(tools):
    r = tools.run("check_availability", {"check_in": "2026-12-12", "check_out": "2026-12-14", "guests": 4,
                                         "room_type": "deluxe"})
    assert r["rooms"][0]["available"] is False
    assert "up to 3 guests" in r["rooms"][0]["reason"]


@pytest.mark.parametrize("args, words", [
    ({"check_in": "2026-12-14", "check_out": "2026-12-12", "guests": 2}, "after"),
    ({"check_in": "2026-11-20", "check_out": "2026-11-22", "guests": 2}, "past"),
    ({"check_in": "12th December", "check_out": "2026-12-14", "guests": 2}, "YYYY-MM-DD"),
    ({"check_in": "2026-12-01", "check_out": "2026-12-31", "guests": 2}, "14 nights"),
    ({"check_in": "2026-12-12", "check_out": "2026-12-14", "guests": 2, "room_type": "penthouse"}, "standard"),
])
def test_bad_requests_come_back_as_errors_the_agent_can_say(tools, args, words):
    r = tools.run("check_availability", args)
    assert words in r["error"]


def test_a_booking_made_in_one_turn_is_found_and_cancelled_later(tools):
    made = tools.run("create_booking", {"guest_name": "Kasun Perera", "phone": "0771234567", "room_type": "deluxe",
                                        "check_in": "2026-12-12", "check_out": "2026-12-14", "guests": 2})
    assert made["reference"] == "LB-10450"
    assert made["status"] == "confirmed"
    assert made["total"] == 84000
    found = tools.run("find_booking", {"reference": "lb 10450"})
    assert found["bookings"][0]["guest_name"] == "Kasun Perera"
    gone = tools.run("cancel_booking", {"reference": "LB10450"})
    assert gone["status"] == "cancelled"
    assert gone["cancellation_fee"] == 0
    again = tools.run("cancel_booking", {"reference": "LB-10450"})
    assert "already cancelled" in again["error"]
    assert tools.booking("LB-10450")["status"] == "cancelled"


def test_cancelling_within_48_hours_charges_one_night(tools):
    r = tools.run("cancel_booking", {"reference": "LB-10399"})
    assert r["cancellation_fee"] == 28000


def test_a_found_booking_says_what_cancelling_it_now_would_cost(tools):
    """The agent must quote the fee before cancelling; without this it could only guess (first benchmark run)."""
    far = tools.run("find_booking", {"reference": "LB-10421"})["bookings"][0]
    near = tools.run("find_booking", {"reference": "LB-10399"})["bookings"][0]
    assert far["cancellation_fee_now"] == 0
    assert near["cancellation_fee_now"] == 28000
    assert "cancellation_fee_now" not in tools.booking("LB-10421")


def test_a_booking_is_found_by_the_guests_name(tools):
    r = tools.run("find_booking", {"guest_name": "nimal perera"})
    assert [b["reference"] for b in r["bookings"]] == ["LB-10421"]
    assert tools.run("find_booking", {"reference": "LB-99999"}) == {"found": False, "bookings": []}


def test_a_sold_out_room_cannot_be_booked(tools):
    r = tools.run("create_booking", {"guest_name": "A Silva", "phone": "0770000000", "room_type": "family",
                                     "check_in": "2026-12-24", "check_out": "2026-12-26", "guests": 4})
    assert "sold out" in r["error"]
    assert len(tools.state["bookings"]) == len(load_scenario("hotel").data["bookings"])


def test_hotel_information_comes_from_the_pack(tools):
    assert "9,500" in tools.run("get_hotel_info", {"topic": "airport"})["answer"]
    r = tools.run("get_hotel_info", {"topic": "spa"})
    assert "breakfast" in r["error"]


def test_a_transfer_is_recorded(tools):
    r = tools.run("transfer_to_human", {"reason": "billing complaint"})
    assert r["transferred"] is True
    assert tools.state["transferred"] == "billing complaint"


def test_each_call_starts_from_fresh_data(hotel):
    first, second = hotel.toolset(), hotel.toolset()
    first.run("cancel_booking", {"reference": "LB-10421"})
    assert second.booking("LB-10421")["status"] == "confirmed"


def test_unknown_tools_missing_and_mistyped_arguments_are_errors_not_crashes(tools):
    assert "No tool named" in tools.run("book_spa", {})["error"]
    assert "phone" in tools.run("create_booking", {"guest_name": "X", "room_type": "deluxe",
                                                   "check_in": "2026-12-12", "check_out": "2026-12-14",
                                                   "guests": 2})["error"]
    r = tools.run("check_availability", {"check_in": "2026-12-12", "check_out": "2026-12-14", "guests": "2",
                                         "room_type": "deluxe"})
    assert r["rooms"][0]["available"] is True
    assert "guests" in tools.run("check_availability", {"check_in": "2026-12-12", "check_out": "2026-12-14",
                                                         "guests": "two"})["error"]


def test_every_run_is_logged_with_its_result_and_time(tools):
    tools.run("get_hotel_info", {"topic": "pool"})
    tools.run("get_hotel_info", {"topic": "spa"})
    assert [(r.name, r.ok) for r in tools.log] == [("get_hotel_info", True), ("get_hotel_info", False)]
    assert all(r.seconds >= 0 for r in tools.log)


def test_a_folder_without_a_scenario_file_is_refused(tmp_path: Path):
    with pytest.raises(ScenarioError, match="scenario.toml"):
        load_scenario(str(tmp_path))


def test_a_call_expecting_a_tool_the_pack_lacks_is_refused(tmp_path: Path):
    source = Path(__file__).resolve().parents[1] / "calls" / "scenarios" / "hotel"
    for name in ("data.json", "tools.py"):
        (tmp_path / name).write_text((source / name).read_text())
    text = (source / "scenario.toml").read_text().replace('tools = ["check_availability"',
                                                          'tools = ["book_spa", "check_availability"', 1)
    (tmp_path / "scenario.toml").write_text(text)
    with pytest.raises(ScenarioError, match="book_spa"):
        load_scenario(str(tmp_path))


def test_the_hotel_pack_names_its_fixed_facts_and_the_tool_that_serves_them(hotel):
    assert hotel.facts.tool == "get_hotel_info"
    assert hotel.facts.answers["breakfast"].startswith("Breakfast is included")
    assert set(hotel.facts.answers) == set(hotel.data["info"])


def test_a_pack_without_a_facts_table_has_no_facts(tmp_path: Path):
    source = Path(__file__).resolve().parents[1] / "calls" / "scenarios" / "hotel"
    for name in ("data.json", "tools.py"):
        (tmp_path / name).write_text((source / name).read_text())
    text = (source / "scenario.toml").read_text()
    start = text.index("[agent.facts]")
    (tmp_path / "scenario.toml").write_text(text[:start] + text[text.index("\n\n", start):])
    assert load_scenario(str(tmp_path)).facts is None


@pytest.mark.parametrize("table, words", [
    ('tool = "get_spa_info"\ndata = "info"', "get_spa_info"),
    ('tool = "get_hotel_info"\ndata = "spa"', "spa"),
    ('tool = "get_hotel_info"\ndata = "rooms"', "rooms"),
])
def test_a_facts_table_naming_a_missing_tool_or_data_that_is_not_text_is_refused(tmp_path: Path, table, words):
    source = Path(__file__).resolve().parents[1] / "calls" / "scenarios" / "hotel"
    for name in ("data.json", "tools.py"):
        (tmp_path / name).write_text((source / name).read_text())
    text = (source / "scenario.toml").read_text()
    start = text.index("[agent.facts]")
    end = text.index("\n\n", start)
    (tmp_path / "scenario.toml").write_text(text[:start] + "[agent.facts]\n" + table + text[end:])
    with pytest.raises(ScenarioError, match=words):
        load_scenario(str(tmp_path))


def test_a_toolset_can_leave_a_tool_out(hotel):
    names = {spec.name for spec in hotel.toolset(without=("get_hotel_info",)).specs()}
    assert "get_hotel_info" not in names and "check_availability" in names
