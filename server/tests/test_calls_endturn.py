"""Round 2 of the call benchmark: when an end-of-turn model would decide the caller has finished, and where it
would cut them off mid-sentence (spec 2026-11-17-call-bench-round2)."""
import numpy as np
import pytest

from server.calls.endturn import MODEL_FILE, MODEL_REPO, SmartTurn, end_of_turn, speech_bounds

RATE = 16000


def clip(*parts):
    """("tone", seconds) or ("gap", seconds), joined."""
    out = []
    for kind, seconds in parts:
        n = int(seconds * RATE)
        out.append(0.3 * np.sin(2 * np.pi * 220 * np.arange(n) / RATE) if kind == "tone" else np.zeros(n))
    return np.concatenate(out).astype(np.float32)


def model(probability):
    return SmartTurn(predict=lambda audio: probability(audio) if callable(probability) else probability)


def test_speech_bounds_find_the_pauses_inside_and_the_end_of_speech():
    pauses, end = speech_bounds(clip(("tone", 1.0), ("gap", 0.4), ("tone", 1.0), ("gap", 0.5)), RATE)
    assert end == pytest.approx(2.4, abs=0.03)
    assert len(pauses) == 1 and pauses[0][0] == pytest.approx(1.0, abs=0.03)
    assert pauses[0][1] == pytest.approx(1.4, abs=0.03)


def test_a_finished_turn_ends_after_0_2_s_of_silence_and_the_models_own_time():
    r = end_of_turn(clip(("tone", 1.0), ("gap", 0.5)), RATE, model(0.9))
    assert not r["fallback"] and r["probability"] == pytest.approx(0.9)
    assert r["wait_s"] == pytest.approx(0.2 + r["model_s"]) and r["wait_s"] < 0.3
    assert r["speech_end_s"] == pytest.approx(1.0, abs=0.03)


def test_an_unfinished_turn_waits_for_the_3_s_fallback():
    r = end_of_turn(clip(("tone", 1.0), ("gap", 0.5)), RATE, model(0.2))
    assert r["fallback"] and r["wait_s"] == pytest.approx(3.0)


def test_a_pause_the_model_calls_finished_is_a_cut_off_and_the_fixed_wait_has_its_own():
    r = end_of_turn(clip(("tone", 1.0), ("gap", 0.8), ("tone", 1.0), ("gap", 0.3)), RATE, model(0.9))
    assert [round(c, 1) for c in r["cutoffs"]] == [1.0]
    assert [round(c, 1) for c in r["fixed_cutoffs"]] == [1.0]   # 0.8 s is longer than the fixed 0.7 s too
    short = end_of_turn(clip(("tone", 1.0), ("gap", 0.4), ("tone", 1.0)), RATE, model(0.9))
    assert len(short["cutoffs"]) == 1 and short["fixed_cutoffs"] == []


def test_the_model_hears_the_turn_so_far_at_each_pause():
    heard = []
    end_of_turn(clip(("tone", 1.0), ("gap", 0.4), ("tone", 1.0)), RATE,
                SmartTurn(predict=lambda audio: heard.append(len(audio) / RATE) or 0.1))
    assert heard[0] == pytest.approx(1.2, abs=0.03)   # up to the pause and 0.2 s into it
    assert heard[-1] == pytest.approx(2.6, abs=0.03)   # the end, padded with the silence after it


def test_a_silent_clip_and_another_rate_do_not_raise():
    r = end_of_turn(np.zeros(8000, dtype=np.float32), 24000, model(0.9))
    assert r["speech_end_s"] == 0.0 and r["cutoffs"] == []
    r = end_of_turn(clip(("tone", 0.5)), RATE, model(0.9))
    assert r["wait_s"] < 0.3


def test_the_real_model_gives_a_probability():
    from huggingface_hub import try_to_load_from_cache
    path = try_to_load_from_cache(MODEL_REPO, MODEL_FILE)
    if not isinstance(path, str):
        pytest.skip("Smart Turn v3.2 is not downloaded")
    probability, seconds = SmartTurn(path).predict(clip(("tone", 1.0), ("gap", 0.2)))
    assert 0.0 <= probability <= 1.0 and seconds < 2.0


def test_the_features_are_whispers_log_mel():
    from transformers import WhisperFeatureExtractor

    from server.calls.endturn import log_mel
    fe = WhisperFeatureExtractor(chunk_length=8)
    audio = np.pad(clip(("tone", 1.0), ("gap", 0.3), ("tone", 0.5)), (8 * RATE - int(1.8 * RATE), 0))
    expected = fe(audio, sampling_rate=RATE, return_tensors="np", padding="max_length", max_length=8 * RATE,
                  truncation=True, do_normalize=True).input_features[0]
    got = log_mel(audio.astype(np.float64), fe.mel_filters)
    assert got.shape == (80, 800) and np.abs(got - expected).max() < 1e-4
