"""The hearing x voice benchmark matrix: composing first sound from stage times, the reference bands, whole-call
recordings with the real waits, and the CSV and Excel exports (spec 2026-11-17-call-simulation)."""
import csv
import io

import numpy as np
import openpyxl
import pytest
import soundfile as sf

from server.calls.matrix import (BANDS, REFERENCES, band, compose, conversation, read_wav, write_csv,
                                 write_xlsx)


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
