"""The call benchmark's report (spec 2026-11-17-call-simulation)."""
import pytest

from server.calls.report import compare, network_share, notes, render, stats


def turn(**kw):
    base = {"call": "c", "repeat": 0, "turn": 1, "said": "book a room", "heard": "book a room", "wer": 0.0,
            "silence_s": 0.7, "hearing_s": 0.9, "model_first_word_s": 0.8, "model_first_piece_s": 1.0,
            "model_whole_s": 1.5, "voice_first_s": 2.5, "first_sound_s": 5.1, "stalls_s": 0.0, "held_s": 0.0,
            "reply": "Yes.", "tool_runs": [], "voice_errors": []}
    return base | kw


def test_stats_gives_median_nearest_rank_90th_percentile_and_worst():
    assert stats([None]) is None
    s = stats([float(x) for x in range(1, 11)] + [None])
    assert (s["median"], s["p90"], s["worst"], s["n"]) == (5.5, 9.0, 10.0, 10)
    assert stats([3.0])["p90"] == 3.0


def test_the_slowest_link_is_named_with_its_share():
    first = notes([turn(), turn(voice_first_s=2.3)], [])[0]
    assert "Voice: first piece" in first and "2.40 s of a typical 5.00 s (48%)" in first


def test_network_share_counts_cloud_requests_and_leaves_a_local_model_out():
    t = turn(hearing_net={"connect": 0.1, "upload": 0.14, "wait": 1.0, "download": 0.01}, hearing_new_connection=True,
             voice_first_net={"connect": 0.12, "upload": 0.0, "wait": 2.4, "download": 0.08},
             voice_first_new_connection=True,
             model_requests=[{"host": "localhost", "new_connection": True, "connect": 0.001}])
    share = network_share([t])
    assert share["requests"] == 2 and share["new_connections"] == 2
    assert share["seconds"] == pytest.approx(0.45)
    assert share["of_seconds"] == pytest.approx(3.85)
    cloud = network_share([t | {"model_requests": [{"host": "generativelanguage.googleapis.com",
                                                    "new_connection": True, "connect": 0.13}]}])
    assert cloud["requests"] == 3 and cloud["seconds"] == pytest.approx(0.58)
    assert cloud["of_seconds"] == pytest.approx(3.85 + 1.5)


def test_notes_name_invented_amounts_silent_turns_and_held_words():
    calls = [{"id": "book", "checks": [{"name": "money", "ok": False, "detail": "said 80,000"}]}]
    said = notes([turn(empty_after_tool=True, held_s=1.4)], calls)
    assert any("book" in n and "amounts" in n for n in said)
    assert any("silence after a tool" in n for n in said)
    assert any("held" in n and "1.40" in n for n in said)


def test_render_and_compare_produce_tables():
    setup = {"scenario_name": "Hotel", "shown": {"Model": "m", "Hearing": "text", "Voice": "none"}, "repeat": 1}
    calls = [{"id": "c", "repeat": 0, "title": "A call", "passed": True, "checks": [{"name": "answered", "ok": True,
                                                                                      "detail": ""}]}]
    md = render(setup, calls, [turn()])
    assert "## Calls: 1 of 1 passed" in md and "| **First sound after the caller stopped** | 5.10 |" in md
    run = {"setup": setup, "calls": calls, "turns": [turn()]}
    side = compare([("a", run), ("b", run)])
    assert "| Calls passed | 1/1 | 1/1 |" in side


def test_a_misheard_name_is_named_even_when_the_average_error_is_small():
    turns = [turn(said="my name is Kasun Perera", heard="my name is Kasun Perra", wer=0.2)] + [turn()] * 9
    assert any("perera" in n for n in notes(turns, []))
