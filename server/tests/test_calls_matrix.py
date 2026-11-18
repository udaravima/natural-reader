"""The hearing x voice benchmark matrix: composing first sound from stage times, the reference bands, whole-call
recordings with the real waits, and the CSV and Excel exports (spec 2026-11-17-call-simulation)."""
import csv
import io

import numpy as np
import openpyxl
import pytest
import soundfile as sf

from server.calls.matrix import (BANDS, REFERENCES, band, call_seconds, compose, conversation, cost_section, money,
                                 read_wav, write_csv, write_xlsx)


@pytest.mark.parametrize("seconds, label", [(0.4, "human-like"), (0.7, "natural"), (1.0, "acceptable"),
                                            (1.8, "noticeable"), (3.0, "slow"), (None, "–")])
def test_bands(seconds, label):
    assert band(seconds) == label


def test_every_reference_names_its_source_and_kind():
    assert len(BANDS) == 5
    for ref in REFERENCES:
        assert ref["source"] and ref["url"].startswith("https://") and ref["kind"] in {"study", "standard",
                                                                                       "practice"}


def test_first_sound_is_silence_plus_hearing_plus_the_first_piece_ready_and_voiced():
    turns = [{"call": "c", "turn": 1, "pieces": [{"ready_s": 0.8}, {"ready_s": 1.0}]}]
    rows = compose(hearing={("c", 1): 1.0}, turns=turns,
                   voiced={("c", 1, 1): (0.5, 2.0), ("c", 1, 2): (0.5, 1.0)}, silence_s=0.7, parallel=1)
    [row] = rows
    assert row["first_sound_s"] == pytest.approx(0.7 + 1.0 + 0.8 + 0.5)
    assert row["voice_first_s"] == pytest.approx(0.5) and row["model_first_piece_s"] == pytest.approx(0.8)
    assert row["stalls_s"] == pytest.approx(0.0)
    assert row["band"] == "slow"


def test_a_turn_the_model_left_silent_has_no_first_sound():
    [row] = compose(hearing={("c", 1): 1.0}, turns=[{"call": "c", "turn": 1, "pieces": []}], voiced={},
                    silence_s=0.7, parallel=1)
    assert row["first_sound_s"] is None and row["band"] == "–"


def _wav(seconds: float, rate: int) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, np.full(int(seconds * rate), 0.1, dtype=np.float32), rate, format="WAV")
    return buf.getvalue()


def test_a_recording_is_read_as_mono_at_the_rate_asked_for():
    samples = read_wav(_wav(1.0, 16000), 24000)
    assert len(samples) == 24000 and samples.ndim == 1


def test_a_whole_call_puts_the_measured_waits_between_the_voices():
    rate = 1000
    greeting = np.ones(500, dtype=np.float32)
    turns = [{"caller": np.ones(1000, dtype=np.float32), "gap_s": 2.0,
              "pieces": [np.ones(500, dtype=np.float32), np.ones(500, dtype=np.float32)], "stalls_s": [0.25]}]
    audio = conversation(rate, greeting, turns, caller_pause_s=0.4)
    # greeting 0.5, pause 0.4, caller 1.0, gap 2.0, piece 0.5, stall 0.25, piece 0.5
    assert len(audio) == int(rate * (0.5 + 0.4 + 1.0 + 2.0 + 0.5 + 0.25 + 0.5))
    assert np.all(audio[1900:3900] == 0)   # the wait is silence


def test_csv_and_excel_exports(tmp_path):
    rows = [{"engine": "kokoro", "seconds": 0.5, "note": None}, {"engine": "chirp", "seconds": 0.55}]
    write_csv(tmp_path / "t.csv", rows)
    with open(tmp_path / "t.csv", newline="") as f:
        read = list(csv.DictReader(f))
    assert read[1]["engine"] == "chirp" and read[0]["note"] == "" and "note" in read[1]
    write_xlsx(tmp_path / "t.xlsx", {"Voices": rows, "Empty": []})
    book = openpyxl.load_workbook(tmp_path / "t.xlsx")
    assert book.sheetnames == ["Voices", "Empty"]
    sheet = book["Voices"]
    assert [c.value for c in sheet[1]] == ["engine", "seconds", "note"]
    assert sheet["B2"].value == 0.5


def test_caller_clips_are_found_by_what_they_say(tmp_path):
    from server.calls.matrix import clip_sources
    from server.calls.scenario import load_scenario
    hotel = load_scenario("hotel")
    (tmp_path / "book-deluxe-1.wav").write_bytes(b"x")
    (tmp_path / "book-deluxe-3.wav").write_bytes(b"y")
    found = clip_sources(hotel.calls, tmp_path)
    keyed = hotel.call("book-deluxe").lines(True)
    assert found[keyed[0]] == tmp_path / "book-deluxe-1.wav"   # the same words in both versions of the call
    assert keyed[2] not in found                               # the keypad line was never recorded
    assert found[hotel.call("book-deluxe").caller[2]] == tmp_path / "book-deluxe-3.wav"
    assert clip_sources(hotel.calls, None) == {}


def test_a_calls_length_is_the_length_of_the_recording_conversation_builds():
    rate = 100
    turns = [{"caller": np.ones(130, dtype=np.float32), "gap_s": 0.0, "pieces": [np.ones(420, dtype=np.float32)],
              "stalls_s": []},
             {"caller": np.ones(70, dtype=np.float32), "gap_s": 0.0, "pieces": [np.ones(300, dtype=np.float32)],
              "stalls_s": []}]
    built = conversation(rate, np.ones(250, dtype=np.float32), turns)
    # greeting 2.5 s; each turn: the 0.4 s pause, the caller to their last word, the agent's turn to its end
    assert call_seconds(2.5, [(1.3, 4.2), (0.7, 3.0)]) == pytest.approx(len(built) / rate)
    assert call_seconds(None, [(1.0, 2.0)]) == pytest.approx(3.4)   # no greeting recorded


def test_money_marks_a_lower_bound():
    assert money(1.4) == "$1.40"
    assert money(1.4, at_least=True) == "≥ $1.40"
    assert money(None, at_least=True) == "–"


def cost_row(**kw):
    base = {"hearing": "whisper:small.en", "voice": "kokoro:af_heart", "hearing_usd_per_call": 0.0,
            "model_usd_per_call": 0.002754, "voice_usd_per_call": 0.0, "total_usd_per_call": 0.002754,
            "usd_per_1000_calls": 2.75, "call_minutes": 1.35, "call_minutes_min": 0.89, "call_minutes_max": 2.3,
            "usd_per_1000_minutes": 2.04, "model_turns": 19, "model_turns_failed": 0, "model_first_error": None,
            "streams": False, "hearing_whole_call_usd_per_1000": None, "unpriced": ""}
    return base | kw


SHAPE = {"reference": "whisper:small.en", "calls": 6, "caller_turns": 3.17, "caller_s": 13.3, "agent_chars": 470,
         "prompt_tokens": 9700, "reply_tokens": 220,
         "voices": {"kokoro:af_heart": {"agent_s": 31.0, "minutes": 1.35, "min_minutes": 0.89, "max_minutes": 2.3}}}


def test_the_cost_section_says_a_call_is_not_a_minute_and_what_one_call_is():
    text = "\n".join(cost_section([cost_row()], SHAPE))
    assert "not 1,000 minutes" in text
    assert "| Caller turns | 3.2 |" in text
    assert "| Caller audio (the clips sent to hearing) | 13.3 s |" in text
    assert "| Agent text, spoken by the voice | 470 characters |" in text
    assert "| Model text | 9,700 tokens read, 220 written |" in text
    assert "| kokoro:af_heart | 31 s | 1.35 min (0.89 to 2.30) |" in text
    assert ("| whisper:small.en | kokoro:af_heart | 1.35 min | $0.0000 | $0.0028 | $0.0000 | $0.0028 | $2.75 | "
            "$2.04 |") in text
    assert "≥" not in text   # nothing failed, so nothing is a lower bound


def test_a_hearing_whose_model_turns_failed_is_a_lower_bound_listed_last_with_the_full_model_cost():
    short = cost_row(hearing="openai:gpt-transcribe", model_usd_per_call=0.0014, total_usd_per_call=0.0014,
                     usd_per_1000_calls=1.40, usd_per_1000_minutes=1.04, model_turns_failed=10,
                     model_first_error="HTTP 429: This model is busy")
    text = "\n".join(cost_section([short, cost_row()], SHAPE))
    rows = [line for line in text.splitlines() if line.startswith(("| openai:", "| whisper:"))]
    assert rows[0].startswith("| whisper:")   # complete costs first, though this one is dearer
    assert "≥ $0.0014" in rows[1] and "≥ $1.40" in rows[1] and "≥ $1.04" in rows[1]
    assert "`openai:gpt-transcribe`: 10 of 19 turns (first error: HTTP 429: This model is busy)" in text
    assert "the model cost $2.75 per 1,000 calls" in text   # where every turn was answered


def test_no_hearing_with_every_turn_answered_says_to_rerun():
    short = cost_row(model_turns_failed=19, model_first_error="HTTP 429: busy")
    assert "No hearing had every turn answered" in "\n".join(cost_section([short], SHAPE))


def test_a_streaming_hearing_says_what_streaming_the_whole_call_would_cost():
    row = cost_row(hearing="google-stream:projects/p/locations/us/recognizers/_@chirp_3", streams=True,
                   hearing_usd_per_call=0.0041, hearing_whole_call_usd_per_1000=21.6)
    text = "\n".join(cost_section([row], SHAPE))
    assert "`google-stream:us/_@chirp_3`: $21.60 per 1,000 calls instead of $4.10" in text
    assert "projects/p" not in text


def test_a_run_of_one_call_says_call_not_calls():
    assert "the mean of 1 call." in "\n".join(cost_section([cost_row()], SHAPE | {"calls": 1}))
